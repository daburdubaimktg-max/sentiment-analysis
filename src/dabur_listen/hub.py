"""Build the multi-brand Sentiment Hub (dashboard/hub_template.html).

The hub template carries the full feature set — brand switcher, JSON
import/export, Overview / All Comments / Themes / Brand Monitor / Issue /
Risk Flags / Video Comparison / Recommendations / Trend Radar tabs — and this
module feeds it: every brand section is computed from the pipeline database,
optionally overlaid with Claude-written insights (data/insights/<id>.json),
then spliced into the template. A "Pipeline Analytics" tab is appended for
the pipeline-only views (tracked inputs, share of voice, markets, languages).

    python -m dabur_listen hub            # data/exports/hub.html from the DB
    python -m dabur_listen hub --demo     # sample-data preview
"""

import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from .config import DATA_DIR, DB_PATH, ROOT, load_settings

TEMPLATE_PATH = ROOT / "dashboard" / "hub_template.html"
STATE_PATH = DATA_DIR / "hub_state.json"
INSIGHTS_DIR = DATA_DIR / "insights"

THEME_LABEL = {
    "efficacy": "Efficacy / Results", "fragrance": "Fragrance & Scent",
    "price_value": "Price & Value", "availability": "Availability / Stockout",
    "packaging": "Packaging", "ingredients": "Ingredients & Halal",
    "side_effects": "Side Effects", "counterfeit": "Counterfeit Concerns",
    "comparison": "Competitor Comparison", "nostalgia": "Nostalgia & Heritage",
    "influencer_trust": "Influencer Trust", "customer_service": "Customer Service",
}
THEME_KIND = {
    "efficacy": "pos", "nostalgia": "pos", "influencer_trust": "gold",
    "fragrance": "gold", "price_value": "purple", "ingredients": "navy",
    "packaging": "navy", "comparison": "purple",
    "availability": "risk", "side_effects": "risk", "counterfeit": "risk",
    "customer_service": "risk",
}
RISK_THEMES = ("counterfeit", "side_effects", "availability", "customer_service")
INTENT_TONE = {
    "advocacy": "green", "purchase_intent": "green", "consideration": "purple",
    "usage_question": "navy", "general_engagement": "navy",
    "complaint": "red", "spam": "purple", "other": "purple",
}
DEFAULT_PALETTE = {
    "bg": "#EFEDE6", "card": "#FFFFFF", "primary": "#1E3A66", "primaryD": "#16294A",
    "gold": "#D6A22E", "goldBg": "#FAF3DD", "green": "#34A85A", "greenD": "#2C7A47",
    "greenBg": "#E6F2EA", "purple": "#7E5BC2", "purpleBg": "#ECE6F7",
    "red": "#E0503A", "redBg": "#FBE9E6", "ink": "#232730", "muted": "#6B7280",
    "line": "#E7E4DB", "issue": "#E0503A", "issueBg": "#FBE9E6",
}


def _lang_code(lang: str | None) -> str:
    s = (lang or "").lower()
    for key, code in (("arabizi", "ar"), ("arabic", "ar"), ("swahili", "sw"),
                      ("sheng", "sw"), ("french", "fr"), ("english", "en"),
                      ("pidgin", "en"), ("turkish", "tr"), ("urdu", "ur")):
        if key in s:
            return code
    return s[:2] if s else "en"


_ARABIC = re.compile(r"[؀-ۿ]")


def _pct(n, total):
    return round(100 * n / total) if total else 0


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1))


def rows_from_db() -> list[dict]:
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rs = con.execute(
        """SELECT platform, source_type, source_value, tracking_tag, post_url, author,
                  text, translation, detected_language, sentiment, intent, themes,
                  topics, brand_mentions, entity, market, likes, posted_at, scraped_at
           FROM comments WHERE sentiment IS NOT NULL"""
    ).fetchall()
    con.close()
    max_scrape = max((r["scraped_at"] or "")[:10] for r in rs) if rs else ""
    out = []
    for r in rs:
        out.append({
            "platform": r["platform"], "source_type": r["source_type"],
            "source": r["source_value"], "tag": r["tracking_tag"],
            "post_url": r["post_url"], "author": r["author"] or "user",
            "text": r["text"], "translation": r["translation"] or "",
            "language": r["detected_language"] or "", "sentiment": r["sentiment"],
            "intent": r["intent"], "themes": json.loads(r["themes"] or "[]"),
            "topics": json.loads(r["topics"] or "[]"),
            "brands": json.loads(r["brand_mentions"] or "[]"),
            "entity": r["entity"], "market": r["market"], "likes": r["likes"] or 0,
            "day": (r["posted_at"] or r["scraped_at"] or "")[:10],
            "new": (r["scraped_at"] or "")[:10] == max_scrape,
        })
    return out


def brand_from_rows(rows: list[dict], bid: str, name: str, today: str) -> dict:
    """Compute a full hub-schema brand object from classified comment rows."""
    settings = load_settings()
    n = len(rows)
    pos = sum(r["sentiment"] == "positive" for r in rows)
    neu = sum(r["sentiment"] == "neutral" for r in rows)
    neg = sum(r["sentiment"] == "negative" for r in rows)
    net = _pct(pos, n) - _pct(neg, n)
    buy = sum(r["intent"] == "purchase_intent" for r in rows)
    posts = [u for u, _ in Counter(r["post_url"] for r in rows if r["post_url"]).most_common()]
    vid_of = {u: f"v{i+1}" for i, u in enumerate(posts)}
    markets = Counter(r["market"] for r in rows if r["market"] and r["market"] != "unknown")
    top_market = markets.most_common(1)[0][0] if markets else "—"
    negatives = sorted((r for r in rows if r["sentiment"] == "negative"),
                       key=lambda r: -r["likes"])
    positives = sorted((r for r in rows if r["sentiment"] == "positive"),
                       key=lambda r: -r["likes"])

    # deltas vs previous snapshot
    state = _load_state()
    prev = state.get(bid, {})
    state[bid] = {"total": n, "pos_pct": _pct(pos, n), "date": today}
    _save_state(state)
    d_total = f"↑ from {prev['total']} ({prev['date']})" if prev.get("total") not in (None, n) \
        else ("first snapshot" if not prev else "→ stable")
    d_pos = (f"↑ from {prev['pos_pct']}%" if prev.get("pos_pct", _pct(pos, n)) < _pct(pos, n)
             else f"↓ from {prev['pos_pct']}%" if prev.get("pos_pct", _pct(pos, n)) > _pct(pos, n)
             else "→ stable")

    theme_counts = Counter(t for r in rows for t in r["themes"])
    theme_sent = defaultdict(Counter)
    for r in rows:
        for t in r["themes"]:
            theme_sent[t][r["sentiment"]] += 1
    risk_theme = next((t for t, _ in theme_counts.most_common()
                       if t in RISK_THEMES), "availability")

    # brand-health heuristic: sentiment + engagement risk, 0-10
    health_score = round(max(0, min(10, 5 + net / 15 - (2 if negatives and negatives[0]["likes"] > 30 else 0))), 1)

    intent_counts = Counter(r["intent"] for r in rows)
    lang_counts = Counter(r["language"] or "unknown" for r in rows)
    sources = Counter((r["source_type"] or "keyword", r["source"] or "—") for r in rows)
    own_names = [b["name"] for b in settings["brands"]["own"]]
    brand_counts = Counter(b for r in rows for b in r["brands"])

    def sev(r):
        if r["likes"] > 30 or set(r["themes"]) & {"counterfeit", "side_effects"}:
            return "CRITICAL"
        return "HIGH" if r["likes"] > 10 else "MEDIUM"

    risks = [{
        "sev": sev(r),
        "title": f"{THEME_LABEL.get((r['themes'] or ['other'])[0], 'Complaint')} — @{r['author']}",
        "desc": f"{r['likes']} likes, unanswered in scraped data. Market: {r['market'] or 'unknown'}. "
                f"Theme: {', '.join(r['themes']) or 'general'}.",
        "q": r["translation"] or r["text"], "by": f"@{r['author']} · {r['likes']} likes",
        "sla": "Respond ASAP" if sev(r) == "CRITICAL" else "Within 48h",
    } for r in negatives[:6]]

    videos = []
    for u in posts[:8]:
        vr = [r for r in rows if r["post_url"] == u]
        vneg = _pct(sum(r["sentiment"] == "negative" for r in vr), len(vr))
        top = max(vr, key=lambda r: r["likes"])
        videos.append({
            "name": f"POST — {vr[0]['platform'].upper()}",
            "id": (u or "").rstrip("/").split("/")[-1][:24],
            "stats": [["Comments", str(len(vr))],
                      ["Positive", f"{_pct(sum(r['sentiment']=='positive' for r in vr), len(vr))}%"],
                      ["Negative", f"{vneg}%"],
                      ["Top Likes", str(top["likes"])],
                      ["Purchase intent", str(sum(r['intent']=='purchase_intent' for r in vr))],
                      ["Brand Response", "❌ None detected"]],
            "insight": ("🔴 High negative share — review top complaints." if vneg >= 25
                        else "✅ Healthy engagement on this post."),
            "tone": "red" if vneg >= 25 else "green",
        })

    day_counts = Counter(r["day"] for r in rows if r["day"])
    timeline = [{"d": d, "t": f"{c} comments scraped · "
                 f"{_pct(sum(r['day']==d and r['sentiment']=='positive' for r in rows), c)}% positive",
                 "tone": "green"}
                for d, c in sorted(day_counts.items())[-5:]]

    medals = ["🥇", "🥈", "🥉", "4", "5"]
    topic_counts = Counter(t for r in rows for t in r["topics"])
    variants = [{"rank": medals[i], "name": t, "note": f"{c} mentions",
                 "tag": "Talked about", "tone": "green" if i < 2 else "navy"}
                for i, (t, c) in enumerate(topic_counts.most_common(5))]

    channels = [{"name": m, "status": "warn" if any(
        m == r["market"] and r["sentiment"] == "negative" and "availability" in r["themes"]
        for r in rows) else "ok",
        "note": f"{c} mentions"} for m, c in markets.most_common(5)]

    brand = {
        "id": bid,
        "meta": {
            "name": name, "badge": "DABUR", "handle": posts[0] if posts else "keyword tracking",
            "market": top_market, "dates": f"Refreshed {today}",
            "scraper": "Apify · dabur-listen pipeline",
            "refresh": d_total, "postsLabel": f"{len(posts)} Posts · {n} Comments",
        },
        "palette": dict(DEFAULT_PALETTE),
        "issue": {"key": risk_theme, "tab": THEME_LABEL.get(risk_theme, "Top Issue"),
                  "filter": THEME_LABEL.get(risk_theme, "Issue").split(" ")[0],
                  "flag": "issue", "colour": "#E0503A"},
        "kpis": [
            {"label": "TOTAL COMMENTS", "val": str(n), "sub": f"{len(posts)} posts",
             "delta": d_total, "tone": "navy", "dtone": "up"},
            {"label": "POSITIVE", "val": f"{_pct(pos, n)}%", "sub": f"~{pos} comments",
             "delta": d_pos, "tone": "green", "dtone": "up"},
            {"label": "NEUTRAL", "val": f"{_pct(neu, n)}%", "sub": f"~{neu} comments",
             "delta": "→", "tone": "purple", "dtone": "flat"},
            {"label": "NEGATIVE", "val": f"{_pct(neg, n)}%", "sub": f"~{neg} comments",
             "delta": "→", "tone": "red", "dtone": "flat"},
            {"label": "BRAND HEALTH", "val": str(health_score), "sub": "/ 10 · heuristic",
             "delta": "computed", "tone": "gold", "dtone": "flat"},
            {"label": "NET SENTIMENT", "val": f"{'+' if net >= 0 else ''}{net}",
             "sub": "pts", "delta": f"{buy} purchase-intent signals", "tone": "green",
             "dtone": "up" if net >= 0 else "down"},
        ],
        "sent": {"pos": _pct(pos, n), "neu": _pct(neu, n), "neg": _pct(neg, n),
                 "posN": f"~{pos}", "neuN": f"~{neu}", "negN": f"~{neg}"},
        "alert": (f"<b>TOP RISK:</b> “{(negatives[0]['translation'] or negatives[0]['text'])[:120]}” "
                  f"— @{negatives[0]['author']}, {negatives[0]['likes']} likes, unanswered."
                  if negatives else ""),
        "comments": [{
            "u": r["author"], "lk": r["likes"], "orig": r["text"],
            "en": r["translation"] if r["translation"] and r["translation"] != r["text"] else "",
            "lang": _lang_code(r["language"]), "rtl": bool(_ARABIC.search(r["text"])),
            "s": {"positive": "pos", "neutral": "neu", "negative": "neg"}[r["sentiment"]],
            "vid": vid_of.get(r["post_url"], "v1"), "brand": False,
            "issue": risk_theme in r["themes"], "new": bool(r.get("new")),
            "th": (r["themes"] or ["general"])[0],
        } for r in sorted(rows, key=lambda r: -r["likes"])[:500]],
        "themes": [{"name": THEME_LABEL.get(t, t), "n": c,
                    "kind": ("risk" if theme_sent[t]["negative"] > theme_sent[t]["positive"]
                             else THEME_KIND.get(t, "navy"))}
                   for t, c in theme_counts.most_common(10)],
        "intent": [{"label": i.replace("_", " ").title(), "pct": _pct(c, n),
                    "tone": INTENT_TONE.get(i, "navy")}
                   for i, c in intent_counts.most_common(4)],
        "timeline": timeline,
        "variants": variants,
        "channels": channels,
        "channelNote": "Markets inferred from dialect, currency and place mentions.",
        "deltas": [
            {"label": "Total comments", "pct": d_total, "note": f"now {n}", "tone": "green"},
            {"label": "Positive share", "pct": d_pos, "note": f"now {_pct(pos, n)}%", "tone": "green"},
            {"label": "Purchase intent", "pct": str(buy), "note": "signals in window", "tone": "gold"},
            {"label": "Top complaint likes", "pct": str(negatives[0]["likes"] if negatives else 0),
             "note": "highest-liked negative", "tone": "red"},
        ],
        "health": [
            {"label": "BRAND HEALTH", "val": str(health_score), "sub": "heuristic /10", "tone": "gold"},
            {"label": "NET SENTIMENT", "val": f"{'+' if net >= 0 else ''}{net}", "sub": "pts", "tone": "green"},
            {"label": "RISK LOAD", "val": f"{_pct(neg, n)}%", "sub": "negative share", "tone": "red"},
            {"label": "RESPONSE RATE", "val": "0%", "sub": "no brand replies detected", "tone": "purple"},
        ],
        "critical": [{"u": r["author"], "c": (r["translation"] or r["text"])[:110],
                      "lk": str(r["likes"]), "pri": sev(r), "st": "UNANSWERED"}
                     for r in negatives[:7]],
        "advocates": [{"icon": "🌟", "u": r["author"], "tag": f"{r['likes']} likes",
                       "q": (r["translation"] or r["text"])[:120]} for r in positives[:5]],
        "respQuality": f"0 brand replies detected across {n} scraped comments. "
                       "Run the insights command for a written response-quality assessment.",
        "risks": risks,
        "videos": videos,
        "recos": _fallback_recos(negatives, buy, brand_counts, own_names),
        "takeaway": (f"Net sentiment {'+' if net >= 0 else ''}{net} pts across {n} comments; "
                     f"{buy} purchase-intent signals. "
                     + (f"Top risk: {THEME_LABEL.get(risk_theme)} — respond to the highest-liked "
                        f"complaints first." if negatives else "No major complaints detected.")),
        # extension tab data (pipeline-only views)
        "pipeline": {
            "inputs": [{"type": t, "value": v, "n": c} for (t, v), c in sources.most_common(20)],
            "sov": [{"brand": b, "n": c, "own": b in own_names}
                    for b, c in brand_counts.most_common(12)],
            "markets": [{"name": m, "n": c} for m, c in markets.most_common(12)],
            "langs": [{"name": l, "n": c} for l, c in lang_counts.most_common(12)],
        },
    }
    # overlay Claude-written insights when present
    ins = INSIGHTS_DIR / f"{bid}.json"
    if ins.exists():
        try:
            brand.update({k: v for k, v in json.loads(ins.read_text()).items() if k != "trend"})
        except Exception:
            pass
    return brand


def _fallback_recos(negatives, buy, brand_counts, own_names):
    recos = []
    if negatives:
        r = negatives[0]
        recos.append({"n": 1, "title": f"Reply to @{r['author']} ({r['likes']} likes)",
                      "when": "TODAY", "tone": "red",
                      "desc": (r["translation"] or r["text"])[:140]})
    if buy:
        recos.append({"n": len(recos) + 1, "title": f"Route {buy} purchase-intent comments to sales/CX",
                      "when": "THIS WEEK", "tone": "green",
                      "desc": "Price and where-to-buy questions are warm leads — answer with "
                              "stockists per market."})
    comp = [b for b in brand_counts if b not in own_names]
    if comp:
        recos.append({"n": len(recos) + 1, "title": f"Monitor competitor chatter ({', '.join(comp[:3])})",
                      "when": "ONGOING", "tone": "purple",
                      "desc": "Track comparison comments for switching triggers."})
    recos.append({"n": len(recos) + 1, "title": "Generate strategy insights",
                  "when": "OPTIONAL", "tone": "navy",
                  "desc": "Run `python -m dabur_listen insights` to have Claude write the "
                          "risk narratives, action plan and trend radar for this brand."})
    return recos


def _extract_span(src: str, name: str) -> tuple[int, int]:
    i = src.find(f"const {name} = ") + len(f"const {name} = ")
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return i, j + 1
    raise ValueError(f"cannot find {name} object in template")


EXTENSION_JS = """
<script>
/* ── Pipeline Analytics tab (added by dabur-listen) ───────────────────── */
(function(){
  const tabs=document.querySelector('.tabs'); if(!tabs) return;
  const btn=document.createElement('button');
  btn.className='tab'; btn.id='tab-pipeline-btn'; btn.textContent='Pipeline Analytics';
  btn.onclick=()=>showTab('tab-pipeline',btn); tabs.appendChild(btn);
  const ref=document.getElementById('tab-trend');
  const sec=document.createElement('section');
  sec.className='section'; sec.id='tab-pipeline';
  ref.parentElement.insertBefore(sec,ref.nextSibling);
  const esc2=s=>(s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
  function bars(items,color){
    if(!items||!items.length) return '<div style="color:var(--muted);font-size:12px">No data</div>';
    const max=Math.max(...items.map(i=>i.n));
    return items.map(i=>`
      <div style="display:grid;grid-template-columns:150px 1fr 40px;gap:8px;align-items:center;margin:6px 0;font-size:12.5px">
        <div style="text-align:right;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc2(i.label)}">${esc2(i.label)}</div>
        <div style="background:var(--line);border-radius:99px;height:12px;overflow:hidden">
          <div style="width:${100*i.n/max}%;height:100%;border-radius:99px;background:${i.color||color}"></div></div>
        <div style="font-weight:600">${i.n}</div></div>`).join('');
  }
  function renderPipeline(){
    const b=DATA.brands[state.active];
    if(!b||!b.pipeline){
      sec.innerHTML='<h1 class="h1">Pipeline Analytics</h1><div class="card" style="padding:18px">'+
        'This tab shows tracked inputs, share of voice, markets and languages for brands scraped '+
        'by the dabur-listen pipeline. The current brand was imported manually, so pipeline data '+
        'is not available for it.</div>';
      return;
    }
    const p=b.pipeline;
    const chips=p.inputs.map(i=>`<span style="display:inline-flex;gap:6px;align-items:center;margin:3px;padding:5px 12px;border-radius:99px;font-size:12px;background:${i.type==='url'?'var(--goldBg)':'var(--greenBg)'};color:var(--ink)">${esc2(i.type==='url'?i.value.replace(/^https?:\\/\\/(www\\.)?/,''):i.value)} <b>${i.n}</b></span>`).join('');
    sec.innerHTML=`<h1 class="h1">Pipeline Analytics — Inputs & Reach</h1>
      <div class="card" style="padding:16px;margin-bottom:14px"><div class="card-h">Tracked inputs — hashtags, keywords & post URLs</div>${chips||'<i>none recorded</i>'}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px">
        <div class="card" style="padding:16px"><div class="card-h">Share of voice — brand mentions</div>
          ${bars(p.sov.map(s=>({label:s.brand,n:s.n,color:s.own?'var(--primary)':'var(--gold)'})),'var(--primary)')}</div>
        <div class="card" style="padding:16px"><div class="card-h">Mentions by market</div>
          ${bars(p.markets.map(m=>({label:m.name,n:m.n})),'var(--green)')}</div>
        <div class="card" style="padding:16px"><div class="card-h">Detected languages</div>
          ${bars(p.langs.map(l=>({label:l.name,n:l.n})),'var(--purple)')}</div>
      </div>`;
  }
  const orig=renderAll;
  renderAll=function(){orig();renderPipeline();};
  renderPipeline();
})();
</script>
"""


def build_hub(out_path: Path | None = None, demo: bool = False,
              include_samples: bool = True) -> Path:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    i0, i1 = _extract_span(template, "DATA")
    data = json.loads(template[i0:i1])
    today = date.today().isoformat()

    if demo:
        from .artifact import _demo_rows
        rows = _demo_rows()
        for k, r in enumerate(rows):
            r.setdefault("post_url", r["source"] if r["source_type"] == "url" else None)
            r.setdefault("topics", ["hair oil ritual", "price check", "where to buy"][k % 3:k % 3 + 1])
            r["new"] = k % 4 == 0
        brands = {"pipeline_demo": brand_from_rows(rows, "pipeline_demo",
                                                   "Pipeline Demo · All Brands", today)}
    else:
        rows = rows_from_db()
        brands = {}
        by_tag = defaultdict(list)
        for r in rows:
            by_tag[r["tag"] or "untagged"].append(r)
        if rows:
            brands["all_data"] = brand_from_rows(rows, "all_data", "All Tracked Data", today)
        for tag, tag_rows in sorted(by_tag.items()):
            if len(by_tag) > 1 or tag != "untagged":
                bid = re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_") or "untagged"
                brands[bid] = brand_from_rows(tag_rows, bid, tag.title(), today)

    if include_samples:
        data["brands"].update(brands)
        data["order"] = list(brands.keys()) + [b for b in data["order"] if b not in brands]
    else:
        data = {"order": list(brands.keys()), "brands": brands}
    if not data["order"]:
        raise RuntimeError("No classified data and samples disabled — nothing to build.")

    html = template[:i0] + json.dumps(data, ensure_ascii=False) + template[i1:]

    trend_file = INSIGHTS_DIR / "trend.json"
    if trend_file.exists():
        j0, j1 = _extract_span(html, "TREND")
        html = html[:j0] + trend_file.read_text(encoding="utf-8") + html[j1:]

    html = html.replace("</body></html>", EXTENSION_JS + "\n</body></html>")

    if out_path is None:
        out_path = DATA_DIR / "exports" / "hub.html"
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
