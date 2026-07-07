"""Generate strategist-grade insight narratives with Claude.

For each brand in the hub, Claude reads the classified comments plus the
computed stats and writes the narrative layer — alert, timeline, variant and
channel intel, a risk register with suggested replies, a prioritised action
plan, and a takeaway — saved to data/insights/<brand_id>.json, which
`python -m dabur_listen hub` overlays automatically. A market trend analysis
is written to data/insights/trend.json for the Trend Radar tab.

    python -m dabur_listen insights            # all brands + trend
    python -m dabur_listen insights --brand herbl_clove_iraq
"""

import json
from collections import defaultdict

from .config import DATA_DIR
from .enrich import client
from .hub import brand_from_rows, rows_from_db

INSIGHTS_DIR = DATA_DIR / "insights"

_STR = {"type": "string"}
_SECTIONS = {"type": "array", "items": {"type": "object", "properties": {
    "title": _STR, "body": _STR}, "required": ["title", "body"], "additionalProperties": False}}

BRAND_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "alert": _STR,
            "timeline": {"type": "array", "items": {"type": "object", "properties": {
                "d": _STR, "t": _STR, "tone": {"type": "string", "enum": ["green", "red", "gold", "purple", "navy"]}},
                "required": ["d", "t", "tone"], "additionalProperties": False}},
            "variants": {"type": "array", "items": {"type": "object", "properties": {
                "rank": _STR, "name": _STR, "note": _STR, "tag": _STR,
                "tone": {"type": "string", "enum": ["green", "red", "gold", "purple", "navy"]}},
                "required": ["rank", "name", "note", "tag", "tone"], "additionalProperties": False}},
            "channels": {"type": "array", "items": {"type": "object", "properties": {
                "name": _STR, "status": {"type": "string", "enum": ["ok", "warn"]}, "note": _STR},
                "required": ["name", "status", "note"], "additionalProperties": False}},
            "channelNote": _STR,
            "respQuality": _STR,
            "risks": {"type": "array", "items": {"type": "object", "properties": {
                "sev": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM"]},
                "title": _STR, "desc": _STR, "q": _STR, "by": _STR, "sla": _STR,
                "reply": _STR}, "required": ["sev", "title", "desc", "q", "by", "sla", "reply"],
                "additionalProperties": False}},
            "recos": {"type": "array", "items": {"type": "object", "properties": {
                "n": {"type": "integer"}, "title": _STR, "when": _STR,
                "tone": {"type": "string", "enum": ["green", "red", "gold", "purple", "navy"]},
                "desc": _STR}, "required": ["n", "title", "when", "tone", "desc"],
                "additionalProperties": False}},
            "takeaway": _STR,
        },
        "required": ["alert", "timeline", "variants", "channels", "channelNote",
                     "respQuality", "risks", "recos", "takeaway"],
        "additionalProperties": False,
    },
}

TREND_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["GO", "WATCH", "NO-GO"]},
            "vsub": _STR, "name": _STR, "for": _STR,
            "snapshot": {"type": "array", "items": {"type": "object", "properties": {
                "label": _STR, "val": _STR, "sub": _STR,
                "tone": {"type": "string", "enum": ["green", "red", "gold", "purple", "navy"]}},
                "required": ["label", "val", "sub", "tone"], "additionalProperties": False}},
            "what": _STR,
            "sections": _SECTIONS,
        },
        "required": ["verdict", "vsub", "name", "for", "snapshot", "what", "sections"],
        "additionalProperties": False,
    },
}

SYSTEM = """You are a senior social-listening strategist for Dabur International. You are given
a brand's computed dashboard data (KPIs, themes, per-post stats) plus the actual classified
comments (original text + English translation, sentiment, intent, themes, likes).

Write the narrative layer of the dashboard. Be specific and evidence-led: quote real comments,
name real users, count real occurrences. No filler, no generic marketing advice — every claim
must trace to the data. Suggested replies must be publish-ready, in the commenter's language
first (with an English gloss in parentheses when not English), warm and non-corporate.
SLAs should be concrete ("Respond today", "7 days"). Timeline dates from the data's date range."""


def _brand_payload(brand: dict) -> str:
    slim = {k: brand[k] for k in ("meta", "kpis", "sent", "themes", "intent", "videos") if k in brand}
    comments = [{k: c.get(k) for k in ("u", "lk", "orig", "en", "lang", "s", "th", "vid", "d")}
                for c in brand.get("comments", [])[:250]]
    return json.dumps({"stats": slim, "comments": comments}, ensure_ascii=False)


def _generate(system: str, user: str, schema: dict, model: str) -> dict:
    response = client().messages.create(
        model=model, max_tokens=16000,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": schema},
        messages=[{"role": "user", "content": user}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Insight generation was refused by the model.")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def generate_all(only_brand: str | None = None) -> list[str]:
    from datetime import date
    from .config import load_settings
    import re
    settings = load_settings()
    model = settings["model"]
    rows = rows_from_db()
    if not rows:
        raise RuntimeError("No classified data — run `process` first.")
    by_tag = defaultdict(list)
    for r in rows:
        by_tag[r["tag"] or "untagged"].append(r)
    INSIGHTS_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    today = date.today().isoformat()
    for tag, tag_rows in sorted(by_tag.items()):
        bid = re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_") or "untagged"
        if only_brand and bid != only_brand:
            continue
        brand = brand_from_rows(tag_rows, bid, tag.title(), today)
        result = _generate(
            SYSTEM,
            "Write the insight layer for this brand:\n" + _brand_payload(brand),
            BRAND_SCHEMA, model)
        (INSIGHTS_DIR / f"{bid}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append(bid)
    if not only_brand:
        all_brand = brand_from_rows(rows, "all_data", "All Tracked Data", today)
        trend = _generate(
            SYSTEM + "\nNow act as a trend analyst: identify the single most actionable "
            "market/content trend visible in this data for Dabur, and write a GO/WATCH/NO-GO "
            "trend-radar analysis with a 90-day plan.",
            "Analyze the trend opportunity in this data:\n" + _brand_payload(all_brand),
            TREND_SCHEMA, model)
        (INSIGHTS_DIR / "trend.json").write_text(
            json.dumps(trend, ensure_ascii=False, indent=1), encoding="utf-8")
        written.append("trend")
    return written
