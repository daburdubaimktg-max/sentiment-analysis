"""Build a self-contained HTML snapshot of the dashboard.

The output is a single file with all data embedded — no server, no API keys,
no external requests. Open it in any browser, email it, or publish it as a
Claude artifact to share with the team.

    python -m dabur_listen snapshot                 # data/exports/dashboard.html
    python -m dabur_listen snapshot --demo          # sample data (for a preview)
"""

import json
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from .config import DATA_DIR, DB_PATH, ROOT, load_settings

TEMPLATE_PATH = ROOT / "dashboard" / "artifact_template.html"


def _rows_from_db() -> list[dict]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT platform, tracking_tag, author, text, translation, detected_language,
                  sentiment, intent, themes, topics, brand_mentions, entity, market,
                  likes, posted_at, scraped_at
           FROM comments WHERE sentiment IS NOT NULL"""
    ).fetchall()
    con.close()
    out = []
    for r in rows:
        day = (r["posted_at"] or r["scraped_at"] or "")[:10]
        out.append({
            "platform": r["platform"],
            "tag": r["tracking_tag"],
            "author": r["author"],
            "text": r["text"],
            "translation": r["translation"],
            "language": r["detected_language"],
            "sentiment": r["sentiment"],
            "intent": r["intent"],
            "themes": json.loads(r["themes"] or "[]"),
            "brands": json.loads(r["brand_mentions"] or "[]"),
            "entity": r["entity"],
            "market": r["market"],
            "likes": r["likes"] or 0,
            "day": day,
        })
    return out


def _demo_rows(n: int = 160) -> list[dict]:
    """Deterministic sample data so the dashboard can be previewed with no scraping."""
    rng = random.Random(7)
    today = date(2026, 7, 6)
    samples = [
        # (text, translation, language, sentiment, intent, themes)
        ("❤️❤️❤️", "love ❤️❤️❤️", "emoji", "positive", "advocacy", ["efficacy"]),
        ("بكم سعره في السعودية؟", "How much is it in Saudi Arabia?", "arabic (gulf)", "neutral", "purchase_intent", ["price_value", "availability"]),
        ("7abibi this oil 3ajeeb wallah", "My dear, this oil is amazing, I swear", "arabizi (gulf)", "positive", "advocacy", ["efficacy"]),
        ("ما شاء الله شعري تغير تماما", "Mashallah, my hair has completely changed", "arabic (gulf)", "positive", "advocacy", ["efficacy"]),
        ("ريحته قوية شوي بس النتيجة حلوة", "The scent is a bit strong but the result is nice", "arabic (gulf)", "positive", "general_engagement", ["fragrance", "efficacy"]),
        ("خلص من السوق وين احصله؟", "It's sold out — where can I find it?", "arabic (egyptian)", "neutral", "purchase_intent", ["availability"]),
        ("Mafuta haya ni kali sana nywele zangu zimeota", "This oil is excellent, my hair has grown", "swahili", "positive", "advocacy", ["efficacy"]),
        ("Bei gani Nairobi?", "What's the price in Nairobi?", "swahili", "neutral", "purchase_intent", ["price_value"]),
        ("This product wan spoil my hair o", "This product almost ruined my hair", "nigerian pidgin", "negative", "complaint", ["side_effects"]),
        ("Where fit buy am for Lagos?", "Where can I buy it in Lagos?", "nigerian pidgin", "neutral", "purchase_intent", ["availability"]),
        ("Bu yog' juda zo'r ekan, sochlarim o'sdi", "This oil is great, my hair grew", "uzbek", "positive", "advocacy", ["efficacy"]),
        ("Necha pul turadi Toshkentda?", "How much does it cost in Tashkent?", "uzbek", "neutral", "purchase_intent", ["price_value"]),
        ("Бұл май шынымен жақсы", "This oil is really good", "kazakh", "positive", "advocacy", ["efficacy"]),
        ("Осторожно, много подделок на рынке", "Careful, there are many fakes on the market", "russian", "negative", "complaint", ["counterfeit"]),
        ("sunsilk is better tbh", "sunsilk is better to be honest", "english", "negative", "other", ["comparison"]),
        ("my mom used this on me as a kid 🥹", "my mom used this on me as a kid 🥹", "english", "positive", "general_engagement", ["nostalgia"]),
        ("does it work on curly hair?", "does it work on curly hair?", "english", "neutral", "consideration", ["efficacy"]),
        ("التغليف الجديد شكله رخيص", "The new packaging looks cheap", "arabic (levantine)", "negative", "complaint", ["packaging"]),
        ("هل هو حلال؟ ما مكوناته", "Is it halal? What are its ingredients?", "arabic (msa)", "neutral", "consideration", ["ingredients"]),
        ("follow me back 🙏 giveaway pls", "follow me back 🙏 giveaway please", "english", "neutral", "spam", []),
        ("bought 3 bottles after seeing this 🔥", "bought 3 bottles after seeing this 🔥", "english", "positive", "advocacy", ["efficacy", "influencer_trust"]),
        ("итишь какой запах приятный", "what a pleasant scent", "russian", "positive", "general_engagement", ["fragrance"]),
    ]
    own = ["Vatika", "Dabur Amla", "Dermoviva"]
    comp = ["Sunsilk", "Pantene", "Garnier", "Parachute"]
    platforms = ["tiktok", "instagram", "youtube", "facebook", "x"]
    markets = ["UAE", "KSA", "EGY", "NGA", "KEN", "UZB", "KAZ", "MAR", "unknown"]
    tags = ["amla-summer-push", "vatika-ksa-campaign", "competitor-watch", None]
    rows = []
    for i in range(n):
        text, trans, lang, sent, intent, themes = samples[rng.randrange(len(samples))]
        is_comp = rng.random() < 0.25
        brand = rng.choice(comp if is_comp else own)
        rows.append({
            "platform": rng.choice(platforms),
            "tag": rng.choice(tags),
            "author": f"user_{rng.randrange(1000, 9999)}",
            "text": text,
            "translation": trans,
            "language": lang,
            "sentiment": sent,
            "intent": intent,
            "themes": themes,
            "brands": [brand],
            "entity": "competitor" if is_comp else "own",
            "market": rng.choice(markets),
            "likes": rng.randrange(0, 400),
            "day": (today - timedelta(days=rng.randrange(0, 45))).isoformat(),
        })
    return rows


def build_snapshot(out_path: Path | None = None, demo: bool = False) -> Path:
    rows = _demo_rows() if demo else _rows_from_db()
    settings = load_settings()
    meta = {
        "generated": date.today().isoformat() if not demo else "2026-07-06",
        "sample": demo,
        "max_day": max((r["day"] for r in rows if r["day"]), default=None),
        "own_brands": [b["name"] for b in settings["brands"]["own"]],
    }
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = template.replace("/*__DATA__*/[]", json.dumps(rows, ensure_ascii=False)) \
                   .replace("/*__META__*/{}", json.dumps(meta, ensure_ascii=False))
    if out_path is None:
        out_path = DATA_DIR / "exports" / "dashboard.html"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
