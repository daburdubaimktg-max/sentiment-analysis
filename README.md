# Dabur International — In-House Social Listening & Sentiment Analysis

Track your brands **and** competitors across TikTok, Instagram, YouTube, Facebook
and X in MENA, African and Central Asian markets — scrape with Apify, translate
any language (Arabic dialects, Arabizi, Swahili/Sheng, Uzbek, Kazakh, emoji-speak)
to English with Claude, classify sentiment/intent/themes with **trainable rules**,
and explore everything in a Streamlit dashboard.

```
 ┌─────────────┐   ┌──────────────┐   ┌───────────────┐   ┌──────────────┐
 │ 1. SCRAPE    │ → │ 2. TRANSLATE │ → │ 3. CLASSIFY   │ → │ 4. DASHBOARD │
 │ Apify actors │   │ Claude        │   │ Claude + your │   │ Streamlit    │
 │ URLs/keywords│   │ dialect-aware │   │ rules + your  │   │ + Plotly     │
 │              │   │ Arabizi/emoji │   │ corrections   │   │              │
 └─────────────┘   └──────────────┘   └───────────────┘   └──────────────┘
        └──────────────────── SQLite (data/listening.db) ────────────────┘
```

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env        # add your APIFY_TOKEN and ANTHROPIC_API_KEY
```

## Usage

**Input type 1 — post URLs** (TikTok, Instagram, YouTube, Facebook):

```bash
python -m dabur_listen ingest-urls \
  "https://www.tiktok.com/@creator/video/123..." \
  "https://www.instagram.com/p/ABC.../" \
  --tag vatika-ksa-campaign
```

**Input type 2 — keywords / hashtags** across platforms:

```bash
python -m dabur_listen ingest-keywords "#daburamla" -p tiktok -p instagram
python -m dabur_listen ingest-keywords "vatika hair oil" -p youtube -p x --tag competitor-watch
```

**Process** (translate + classify everything new):

```bash
python -m dabur_listen process
```

**Dashboard**:

```bash
python -m dabur_listen dashboard
```

## Training the classifier (changing how data gets flagged)

Two levers, no ML training required — both feed straight into the classifier prompt:

1. **Rules** — edit `config/classification_rules.yaml`. It already encodes e.g.
   *heart emojis = positive* and *price questions = purchase intent*. Add or
   change rules, themes, and taxonomy freely.
2. **Corrections** — fix a wrong flag and it becomes a worked example:
   - CLI: `python -m dabur_listen label 123 --sentiment positive --note "sarcastic praise"`
   - Dashboard: **Review / Train** tab → change the sentiment/intent cell → *Save corrections*

Then re-flag existing data (translations are kept, nothing is re-scraped):

```bash
python -m dabur_listen reclassify --all
```

Manually corrected rows are never overwritten by the model.

## Configuration

| File | What it controls |
|---|---|
| `config/settings.yaml` | brands + competitor aliases, markets, Apify actors, Claude model, batch size |
| `config/classification_rules.yaml` | sentiment/intent rules, theme taxonomy |
| `training/corrections.jsonl` | your accumulated corrections (auto-appended) |
| `.env` | `APIFY_TOKEN`, `ANTHROPIC_API_KEY` |

### Swapping Apify actors

Each `(platform, mode)` pair in `settings.yaml → apify.actors` names an Apify
actor and an input template (`{value}` = the URL or keyword). If an actor from
the Apify store changes its input shape or you prefer another scraper, edit the
template — no code changes needed. Raw scraper output is preserved per comment
in the `raw_json` column for auditing.

## Data model

Single table `comments` in `data/listening.db` — one row per comment, carrying
raw scrape → translation → classification columns. Export any time:

```bash
python -m dabur_listen export data/exports/all.csv
```

## Cost & scale notes

- Apify runs are capped by `apify.max_items` per run (default 500).
- Claude calls batch 25 comments per request with prompt caching on the
  system prompt/rules, so repeated runs are cheap.
- For very large backfills, the Anthropic **Message Batches API** (50% cost,
  async) is the natural next step — the enrichment layer is isolated in
  `src/dabur_listen/enrich.py` to make that swap easy.

## Roadmap ideas

- Scheduled ingestion (cron) for always-on keyword tracking
- Message Batches API for large backfills
- Alerting (negative-sentiment spikes, counterfeit-theme surges) to email/Slack
- Influencer-level rollups (author aggregation is already stored)
