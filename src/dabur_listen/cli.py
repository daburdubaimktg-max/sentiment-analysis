"""Command-line interface for the listening pipeline.

  python -m dabur_listen ingest-urls <url>... [--tag campaign-name]
  python -m dabur_listen ingest-keywords "<keyword>" -p tiktok -p instagram
  python -m dabur_listen process              # translate + classify pending comments
  python -m dabur_listen reclassify --all     # re-flag after editing rules/corrections
  python -m dabur_listen label <id> --sentiment positive --intent purchase_intent
  python -m dabur_listen stats
  python -m dabur_listen export out.csv
  python -m dabur_listen dashboard
"""

import json
import subprocess
import sys
import time
from pathlib import Path

import click

from . import db
from .config import ROOT, append_correction, load_settings


@click.group()
def cli():
    """Dabur International — social listening & sentiment pipeline."""


@cli.command("ingest-urls")
@click.argument("urls", nargs=-1, required=True)
@click.option("--tag", default=None, help="Tracking tag, e.g. 'vatika-ksa-launch'")
def ingest_urls_cmd(urls, tag):
    """Scrape comments from specific post URLs (TikTok, Instagram, YouTube, Facebook)."""
    from . import ingest
    total = 0
    with db.connect() as con:
        for url in urls:
            click.echo(f"→ scraping {url} ...")
            try:
                rows = ingest.ingest_url(url, tag)
            except Exception as e:
                click.echo(f"  ✗ {e}", err=True)
                continue
            n = db.insert_comments(con, rows)
            total += n
            click.echo(f"  ✓ {len(rows)} items fetched, {n} new comments stored")
        con.execute("INSERT INTO runs(kind, detail, items) VALUES('ingest', ?, ?)",
                    (f"urls:{len(urls)}", total))
    click.echo(f"Done — {total} new comments. Next: python -m dabur_listen process")


@cli.command("ingest-keywords")
@click.argument("keyword")
@click.option("--platforms", "-p", multiple=True, default=("tiktok", "instagram"),
              help="Repeatable: tiktok, instagram, youtube, x")
@click.option("--tag", default=None, help="Tracking tag")
def ingest_keywords_cmd(keyword, platforms, tag):
    """Track a keyword or #hashtag across platforms."""
    from . import ingest
    total = 0
    with db.connect() as con:
        for platform in platforms:
            click.echo(f"→ searching {platform} for {keyword!r} ...")
            try:
                rows = ingest.ingest_keyword(platform, keyword, tag)
            except Exception as e:
                click.echo(f"  ✗ {e}", err=True)
                continue
            n = db.insert_comments(con, rows)
            total += n
            click.echo(f"  ✓ {len(rows)} items fetched, {n} new stored")
        con.execute("INSERT INTO runs(kind, detail, items) VALUES('ingest', ?, ?)",
                    (f"keyword:{keyword}", total))
    click.echo(f"Done — {total} new comments. Next: python -m dabur_listen process")


@cli.command("import-file")
@click.argument("paths", nargs=-1, required=True, type=click.Path(exists=True, dir_okay=False))
@click.option("--platform", default=None, help="tiktok/instagram/... (auto-detected from URLs if omitted)")
@click.option("--tag", default=None, help="Tracking tag")
def import_file_cmd(paths, platform, tag):
    """Import Apify dataset exports (.json / .xlsx / .csv) into the DB."""
    from .importer import import_file
    total = 0
    with db.connect() as con:
        for path in paths:
            try:
                rows = import_file(path, platform, tag)
            except Exception as e:
                click.echo(f"  ✗ {path}: {e}", err=True)
                continue
            n = db.insert_comments(con, rows)
            total += n
            click.echo(f"  ✓ {path}: {len(rows)} items, {n} new comments stored")
        con.execute("INSERT INTO runs(kind, detail, items) VALUES('ingest', ?, ?)",
                    (f"import:{len(paths)} files", total))
    click.echo(f"Done — {total} new comments. Next: python -m dabur_listen process")


@cli.command()
@click.option("--out", type=click.Path(dir_okay=False), default=None,
              help="Output path (default data/exports/hub.html)")
@click.option("--demo", is_flag=True, help="Sample data instead of the DB")
@click.option("--no-samples", is_flag=True, help="Exclude the built-in sample brands")
def hub(out, demo, no_samples):
    """Build the multi-brand Sentiment Hub (all tabs, self-contained HTML)."""
    from .hub import build_hub
    path = build_hub(out, demo=demo, include_samples=not no_samples)
    click.echo(f"Wrote {path} — open in a browser or share as a Claude artifact.")


def _chunks(rows, size):
    for i in range(0, len(rows), size):
        yield rows[i:i + size]


@cli.command()
@click.option("--limit", default=None, type=int, help="Max comments to process this run")
def process(limit):
    """Run layer 2 (translate) then layer 3 (classify) on pending comments."""
    from . import enrich
    settings = load_settings()
    batch = settings.get("batch_size", 25)

    with db.connect() as con:
        pending = db.pending_translation(con, limit)
        click.echo(f"Translating {len(pending)} comments ...")
        for chunk in _chunks(pending, batch):
            try:
                results = enrich.translate_batch([dict(r) for r in chunk])
            except Exception as e:
                click.echo(f"  ✗ batch failed: {e}", err=True)
                time.sleep(5)
                continue
            for r in chunk:
                if r["id"] in results:
                    db.save_translation(con, r["id"], results[r["id"]])
            con.commit()
            click.echo(f"  ✓ {min(len(results), len(chunk))} translated")

        pending = db.pending_classification(con, limit)
        click.echo(f"Classifying {len(pending)} comments ...")
        system_prompt = enrich.build_classify_system()
        for chunk in _chunks(pending, batch):
            try:
                results = enrich.classify_batch([dict(r) for r in chunk], system_prompt)
            except Exception as e:
                click.echo(f"  ✗ batch failed: {e}", err=True)
                time.sleep(5)
                continue
            for r in chunk:
                if r["id"] in results:
                    db.save_classification(con, r["id"], results[r["id"]])
            con.commit()
            click.echo(f"  ✓ {min(len(results), len(chunk))} classified")

    click.echo("Done. View it: python -m dabur_listen dashboard")


@cli.command()
@click.option("--all", "reclassify_all", is_flag=True,
              help="Re-flag ALL comments (after editing rules), not just pending ones")
@click.option("--limit", default=None, type=int)
def reclassify(reclassify_all, limit):
    """Re-run classification with the current rules + corrections.

    Translation is kept — only the flags change. Manually corrected rows are
    never overwritten."""
    from . import enrich
    settings = load_settings()
    batch = settings.get("batch_size", 25)
    with db.connect() as con:
        pending = db.pending_classification(con, limit, include_done=reclassify_all)
        click.echo(f"Reclassifying {len(pending)} comments with current rules ...")
        system_prompt = enrich.build_classify_system()
        for chunk in _chunks(pending, batch):
            try:
                results = enrich.classify_batch([dict(r) for r in chunk], system_prompt)
            except Exception as e:
                click.echo(f"  ✗ batch failed: {e}", err=True)
                time.sleep(5)
                continue
            for r in chunk:
                if r["id"] in results:
                    db.save_classification(con, r["id"], results[r["id"]])
            con.commit()
            click.echo(f"  ✓ {min(len(results), len(chunk))} reclassified")
    click.echo("Done.")


@cli.command()
@click.argument("comment_id", type=int)
@click.option("--sentiment", type=click.Choice(["positive", "negative", "neutral"]))
@click.option("--intent", type=click.Choice(["purchase_intent", "consideration", "advocacy",
                                             "complaint", "usage_question", "general_engagement",
                                             "spam", "other"]))
@click.option("--note", default="", help="Why — this teaches the classifier")
def label(comment_id, sentiment, intent, note):
    """Manually correct a comment's flags; it becomes a training example."""
    if not sentiment and not intent:
        raise click.UsageError("Provide --sentiment and/or --intent")
    with db.connect() as con:
        row = con.execute("SELECT text, translation FROM comments WHERE id=?", (comment_id,)).fetchone()
        if not row:
            raise click.UsageError(f"No comment with id {comment_id}")
        db.apply_manual_label(con, comment_id, sentiment, intent)
    correction = {"text": row["text"], "correct": {}, "note": note}
    if sentiment:
        correction["correct"]["sentiment"] = sentiment
    if intent:
        correction["correct"]["intent"] = intent
    append_correction(correction)
    click.echo("Saved. It will be used as a worked example on the next process/reclassify run.")


@cli.command()
def stats():
    """Pipeline counts."""
    with db.connect() as con:
        s = db.stats(con)
    for k, v in s.items():
        click.echo(f"{k:>12}: {v or 0}")


@cli.command()
@click.argument("path", type=click.Path(dir_okay=False))
def export(path):
    """Export the full enriched dataset to CSV."""
    import pandas as pd
    with db.connect() as con:
        df = pd.read_sql_query("SELECT * FROM comments", con)
    df.to_csv(path, index=False)
    click.echo(f"Wrote {len(df)} rows to {path}")


@cli.command()
@click.option("--out", type=click.Path(dir_okay=False), default=None,
              help="Output path (default data/exports/dashboard.html)")
@click.option("--demo", is_flag=True, help="Use built-in sample data instead of the DB")
def snapshot(out, demo):
    """Build a self-contained HTML dashboard (shareable, no server or keys)."""
    from .artifact import build_snapshot
    path = build_snapshot(out, demo=demo)
    click.echo(f"Wrote {path} — open it in a browser or share it as a Claude artifact.")


@cli.command()
def dashboard():
    """Launch the Streamlit dashboard."""
    app = ROOT / "dashboard" / "app.py"
    sys.exit(subprocess.call(["streamlit", "run", str(app)]))


if __name__ == "__main__":
    cli()
