"""Layer 1 — scraping via Apify.

Two entry points:
  ingest_url(platform, url)         -> pulls comments from a specific post
  ingest_keyword(platform, keyword) -> searches a platform for a keyword/hashtag

Actor ids and input templates live in config/settings.yaml so you can swap
scrapers without touching code. Items are normalized best-effort (Apify actors
disagree on field names) and the full raw item is kept in the DB for auditing.
"""

import copy
import json
import time
from urllib.parse import urlparse

import requests

from .config import apify_token, load_settings

APIFY_BASE = "https://api.apify.com/v2"


def _fill_template(obj, value: str):
    """Recursively substitute '{value}' in the actor input template."""
    if isinstance(obj, str):
        return obj.replace("{value}", value)
    if isinstance(obj, list):
        return [_fill_template(x, value) for x in obj]
    if isinstance(obj, dict):
        return {k: _fill_template(v, value) for k, v in obj.items()}
    return obj


def run_actor(actor: str, run_input: dict, timeout_secs: int, max_items: int) -> list[dict]:
    """Start an Apify actor run, poll until it finishes, return dataset items."""
    token = apify_token()
    resp = requests.post(
        f"{APIFY_BASE}/acts/{actor}/runs",
        params={"token": token},
        json=run_input,
        timeout=60,
    )
    resp.raise_for_status()
    run = resp.json()["data"]
    run_id = run["id"]

    deadline = time.time() + timeout_secs
    status = run["status"]
    while status in ("READY", "RUNNING") and time.time() < deadline:
        time.sleep(10)
        r = requests.get(f"{APIFY_BASE}/actor-runs/{run_id}", params={"token": token}, timeout=60)
        r.raise_for_status()
        run = r.json()["data"]
        status = run["status"]

    if status != "SUCCEEDED":
        raise RuntimeError(f"Apify run {run_id} for {actor} ended with status {status}")

    items = requests.get(
        f"{APIFY_BASE}/datasets/{run['defaultDatasetId']}/items",
        params={"token": token, "limit": max_items, "clean": "true"},
        timeout=120,
    )
    items.raise_for_status()
    return items.json()


# ── Normalization ─────────────────────────────────────────────────────────────
# Apify actors use different field names; try the common ones in order.

TEXT_FIELDS = ["text", "comment", "commentText", "content", "message", "full_text", "title", "caption", "description"]
AUTHOR_FIELDS = ["ownerUsername", "username", "author", "authorMeta", "uniqueId", "profileName", "user", "channelName"]
ID_FIELDS = ["id", "cid", "commentId", "uid", "itemId", "tweetId"]
LIKE_FIELDS = ["likesCount", "diggCount", "likeCount", "likes", "favouriteCount", "voteCount", "reactionsCount"]
TIME_FIELDS = ["timestamp", "createTimeISO", "createdAt", "created_at", "publishedTimeText", "date", "publishedAt"]
URL_FIELDS = ["postUrl", "videoWebUrl", "url", "inputUrl", "postLink", "twitterUrl", "commentUrl"]


def _first(item: dict, fields: list[str]):
    for f in fields:
        v = item.get(f)
        if v is None:
            continue
        if isinstance(v, dict):
            v = v.get("name") or v.get("username") or v.get("nickName") or v.get("screen_name")
        if v:
            return v
    return None


def normalize_item(item: dict, platform: str, source_type: str, source_value: str, tag: str | None) -> dict | None:
    text = _first(item, TEXT_FIELDS)
    if not text or not str(text).strip():
        return None
    likes = _first(item, LIKE_FIELDS)
    return {
        "platform": platform,
        "source_type": source_type,
        "source_value": source_value,
        "tracking_tag": tag,
        "external_id": str(_first(item, ID_FIELDS) or ""),
        "post_url": _first(item, URL_FIELDS) or (source_value if source_type == "url" else None),
        "author": str(_first(item, AUTHOR_FIELDS) or ""),
        "text": str(text).strip(),
        "likes": int(likes) if isinstance(likes, (int, float)) else 0,
        "posted_at": str(_first(item, TIME_FIELDS) or "") or None,
        "raw": item,
    }


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for key in ("tiktok", "instagram", "youtube", "facebook"):
        if key in host:
            return key
    if "twitter" in host or host.endswith("x.com") or host == "x.com":
        return "x"
    if "youtu.be" in host:
        return "youtube"
    raise ValueError(f"Cannot detect platform from URL: {url}")


def _scrape(platform: str, mode: str, value: str, tag: str | None) -> list[dict]:
    settings = load_settings()
    apify_cfg = settings["apify"]
    try:
        spec = apify_cfg["actors"][platform][mode]
    except KeyError:
        raise ValueError(f"No actor configured for platform={platform} mode={mode} — add it to config/settings.yaml")
    run_input = _fill_template(copy.deepcopy(spec["input"]), value)
    items = run_actor(spec["actor"], run_input, apify_cfg["timeout_secs"], apify_cfg["max_items"])
    rows = [normalize_item(it, platform, mode, value, tag) for it in items]
    return [r for r in rows if r]


def ingest_url(url: str, tag: str | None = None) -> list[dict]:
    return _scrape(detect_platform(url), "url", url, tag)


def ingest_keyword(platform: str, keyword: str, tag: str | None = None) -> list[dict]:
    return _scrape(platform, "keyword", keyword.lstrip("#"), tag)
