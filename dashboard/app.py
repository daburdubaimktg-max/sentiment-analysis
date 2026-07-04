"""Dabur International — Social Listening Dashboard (Streamlit).

Run with:  python -m dabur_listen dashboard   (or: streamlit run dashboard/app.py)
"""

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dabur_listen.config import DB_PATH, append_correction  # noqa: E402

# ── Palette (validated data-viz palette; sentiment uses polarity colors) ─────
POS, NEU, NEG = "#1baf7a", "#898781", "#e34948"
SENTIMENT_COLORS = {"positive": POS, "neutral": NEU, "negative": NEG}
SERIES = ["#2a78d6", "#1baf7a", "#eda100", "#008300", "#4a3aa7", "#e34948", "#e87ba4", "#eb6834"]
GRID = "#e1e0d9"
INK_MUTED = "#898781"

st.set_page_config(page_title="Dabur Social Listening", page_icon="📡", layout="wide")


def style(fig, height=340):
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=36, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color=INK_MUTED),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        xaxis=dict(gridcolor=GRID, zeroline=False),
        yaxis=dict(gridcolor=GRID, zeroline=False),
        hovermode="x unified" if any(t.type == "scatter" for t in fig.data) else "closest",
    )
    fig.update_traces(marker_line_width=0)
    return fig


@st.cache_data(ttl=60)
def load_data() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    con = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM comments", con)
    con.close()
    if df.empty:
        return df
    for col in ("themes", "topics", "brand_mentions"):
        df[col] = df[col].apply(lambda v: json.loads(v) if v else [])
    df["date"] = pd.to_datetime(df["posted_at"], errors="coerce", utc=True, format="mixed")
    df["date"] = df["date"].fillna(pd.to_datetime(df["scraped_at"], errors="coerce", utc=True))
    df["day"] = df["date"].dt.date
    return df


df = load_data()

st.title("📡 Dabur International — Social Listening")

if df.empty:
    st.info(
        "No data yet. Ingest something first:\n\n"
        "```\npython -m dabur_listen ingest-keywords \"#vatika\" -p tiktok -p instagram\n"
        "python -m dabur_listen process\n```"
    )
    st.stop()

classified = df[df["sentiment"].notna()].copy()

# ── Filters (one row, above the charts) ──────────────────────────────────────
f1, f2, f3, f4, f5 = st.columns([2, 1, 1, 1, 1])
with f1:
    dmin, dmax = df["day"].min(), df["day"].max()
    date_range = st.date_input("Date range", (dmin, dmax), min_value=dmin, max_value=dmax)
with f2:
    platforms = st.multiselect("Platform", sorted(df["platform"].dropna().unique()))
with f3:
    entities = st.multiselect("Entity", ["own", "competitor", "both", "none"])
with f4:
    markets = st.multiselect("Market", sorted(classified["market"].dropna().unique()))
with f5:
    tags = st.multiselect("Tag", sorted(df["tracking_tag"].dropna().unique()))

view = classified
if len(date_range) == 2:
    view = view[(view["day"] >= date_range[0]) & (view["day"] <= date_range[1])]
if platforms:
    view = view[view["platform"].isin(platforms)]
if entities:
    view = view[view["entity"].isin(entities)]
if markets:
    view = view[view["market"].isin(markets)]
if tags:
    view = view[view["tracking_tag"].isin(tags)]

# ── KPI row ───────────────────────────────────────────────────────────────────
total = len(view)
pos_pct = 100 * (view["sentiment"] == "positive").mean() if total else 0
neg_pct = 100 * (view["sentiment"] == "negative").mean() if total else 0
purchase = int((view["intent"] == "purchase_intent").sum())
net = pos_pct - neg_pct
pending = int(df["sentiment"].isna().sum())

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Mentions analyzed", f"{total:,}")
k2.metric("Positive", f"{pos_pct:.0f}%")
k3.metric("Negative", f"{neg_pct:.0f}%")
k4.metric("Net sentiment", f"{net:+.0f} pts")
k5.metric("Purchase-intent signals", f"{purchase:,}",
          help="Comments asking price / where to buy — hottest leads")
if pending:
    st.caption(f"⏳ {pending:,} scraped comments not yet processed — run `python -m dabur_listen process`")

tab_overview, tab_brands, tab_themes, tab_review = st.tabs(
    ["Overview", "Brands & Share of Voice", "Themes & Markets", "Review / Train"]
)

# ── Overview ──────────────────────────────────────────────────────────────────
with tab_overview:
    c1, c2 = st.columns([3, 2])
    with c1:
        daily = view.groupby(["day", "sentiment"]).size().reset_index(name="count")
        fig = px.line(
            daily, x="day", y="count", color="sentiment",
            color_discrete_map=SENTIMENT_COLORS,
            category_orders={"sentiment": ["positive", "neutral", "negative"]},
            title="Sentiment over time",
        )
        fig.update_traces(line_width=2)
        st.plotly_chart(style(fig), width="stretch")
    with c2:
        counts = view["sentiment"].value_counts()
        fig = go.Figure(go.Pie(
            labels=counts.index, values=counts.values, hole=0.62,
            marker=dict(colors=[SENTIMENT_COLORS.get(s, NEU) for s in counts.index],
                        line=dict(color="#fcfcfb", width=2)),
            textinfo="label+percent",
        ))
        fig.update_layout(title="Sentiment split", showlegend=False)
        st.plotly_chart(style(fig), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        plat = (view.groupby(["platform", "sentiment"]).size().reset_index(name="count"))
        fig = px.bar(
            plat, x="platform", y="count", color="sentiment", barmode="stack",
            color_discrete_map=SENTIMENT_COLORS,
            category_orders={"sentiment": ["positive", "neutral", "negative"]},
            title="Sentiment by platform",
        )
        fig.update_traces(marker_line=dict(color="#fcfcfb", width=2))
        st.plotly_chart(style(fig), width="stretch")
    with c4:
        intents = view["intent"].value_counts().reset_index()
        intents.columns = ["intent", "count"]
        fig = px.bar(intents, x="count", y="intent", orientation="h",
                     title="Intent breakdown", color_discrete_sequence=[SERIES[0]])
        fig.update_layout(yaxis=dict(categoryorder="total ascending"))
        st.plotly_chart(style(fig), width="stretch")

    langs = view["detected_language"].value_counts().head(12).reset_index()
    langs.columns = ["language", "count"]
    fig = px.bar(langs, x="language", y="count", title="Detected languages",
                 color_discrete_sequence=[SERIES[4]])
    st.plotly_chart(style(fig, height=280), width="stretch")

# ── Brands & Share of Voice ───────────────────────────────────────────────────
with tab_brands:
    mentions = view.explode("brand_mentions").dropna(subset=["brand_mentions"])
    if mentions.empty:
        st.info("No brand mentions detected in the current filter.")
    else:
        c1, c2 = st.columns(2)
        with c1:
            sov = mentions.groupby(["brand_mentions", "entity"]).size().reset_index(name="count")
            fig = px.bar(
                sov.sort_values("count"), x="count", y="brand_mentions", color="entity",
                orientation="h", title="Share of voice (brand mentions)",
                color_discrete_map={"own": SERIES[0], "competitor": SERIES[7],
                                    "both": SERIES[2], "none": NEU},
            )
            st.plotly_chart(style(fig, 420), width="stretch")
        with c2:
            bs = mentions.groupby(["brand_mentions", "sentiment"]).size().reset_index(name="count")
            totals = bs.groupby("brand_mentions")["count"].transform("sum")
            bs["share"] = 100 * bs["count"] / totals
            fig = px.bar(
                bs, x="share", y="brand_mentions", color="sentiment", orientation="h",
                title="Sentiment mix per brand (%)",
                color_discrete_map=SENTIMENT_COLORS,
                category_orders={"sentiment": ["positive", "neutral", "negative"]},
            )
            fig.update_traces(marker_line=dict(color="#fcfcfb", width=2))
            st.plotly_chart(style(fig, 420), width="stretch")

        ent_daily = view[view["entity"].isin(["own", "competitor"])] \
            .groupby(["day", "entity"]).size().reset_index(name="count")
        fig = px.line(ent_daily, x="day", y="count", color="entity",
                      title="Own vs competitor conversation volume",
                      color_discrete_map={"own": SERIES[0], "competitor": SERIES[7]})
        fig.update_traces(line_width=2)
        st.plotly_chart(style(fig, 300), width="stretch")

# ── Themes & Markets ──────────────────────────────────────────────────────────
with tab_themes:
    c1, c2 = st.columns(2)
    with c1:
        th = view.explode("themes").dropna(subset=["themes"])
        tcount = th.groupby(["themes", "sentiment"]).size().reset_index(name="count")
        fig = px.bar(
            tcount, x="count", y="themes", color="sentiment", orientation="h",
            title="Themes (colored by sentiment)",
            color_discrete_map=SENTIMENT_COLORS,
            category_orders={"sentiment": ["positive", "neutral", "negative"]},
        )
        fig.update_layout(yaxis=dict(categoryorder="total ascending"))
        fig.update_traces(marker_line=dict(color="#fcfcfb", width=2))
        st.plotly_chart(style(fig, 420), width="stretch")
    with c2:
        mk = view[view["market"].notna() & (view["market"] != "unknown")]
        mcount = mk.groupby(["market", "sentiment"]).size().reset_index(name="count")
        fig = px.bar(
            mcount, x="market", y="count", color="sentiment", barmode="stack",
            title="Mentions by market",
            color_discrete_map=SENTIMENT_COLORS,
            category_orders={"sentiment": ["positive", "neutral", "negative"]},
        )
        fig.update_traces(marker_line=dict(color="#fcfcfb", width=2))
        st.plotly_chart(style(fig, 420), width="stretch")

    topics = view.explode("topics").dropna(subset=["topics"])
    top_topics = topics["topics"].value_counts().head(20).reset_index()
    top_topics.columns = ["topic", "count"]
    fig = px.bar(top_topics, x="count", y="topic", orientation="h",
                 title="Top free-form topics", color_discrete_sequence=[SERIES[2]])
    fig.update_layout(yaxis=dict(categoryorder="total ascending"))
    st.plotly_chart(style(fig, 480), width="stretch")

# ── Review / Train ────────────────────────────────────────────────────────────
with tab_review:
    st.markdown(
        "Review comments and **fix wrong flags** — every correction is saved as a "
        "training example and applied on the next `process` / `reclassify` run."
    )
    q = st.text_input("Search text / translation")
    table = view.copy()
    if q:
        mask = table["text"].str.contains(q, case=False, na=False) | \
               table["translation"].str.contains(q, case=False, na=False)
        table = table[mask]
    show = table[["id", "platform", "author", "text", "translation", "detected_language",
                  "sentiment", "intent", "market", "likes", "post_url"]].head(300)

    edited = st.data_editor(
        show,
        disabled=["id", "platform", "author", "text", "translation",
                  "detected_language", "market", "likes", "post_url"],
        column_config={
            "sentiment": st.column_config.SelectboxColumn(
                options=["positive", "negative", "neutral"]),
            "intent": st.column_config.SelectboxColumn(
                options=["purchase_intent", "consideration", "advocacy", "complaint",
                         "usage_question", "general_engagement", "spam", "other"]),
            "post_url": st.column_config.LinkColumn(),
        },
        hide_index=True,
        width="stretch",
        key="review_editor",
    )

    if st.button("💾 Save corrections", type="primary"):
        changed = 0
        con = sqlite3.connect(DB_PATH)
        for _, new in edited.iterrows():
            old = show[show["id"] == new["id"]].iloc[0]
            fixes = {}
            if new["sentiment"] != old["sentiment"]:
                fixes["sentiment"] = new["sentiment"]
            if new["intent"] != old["intent"]:
                fixes["intent"] = new["intent"]
            if fixes:
                sets = ", ".join(f"{k}=?" for k in fixes) + ", manually_corrected=1"
                con.execute(f"UPDATE comments SET {sets} WHERE id=?",
                            (*fixes.values(), int(new["id"])))
                append_correction({"text": old["text"], "correct": fixes,
                                   "note": "dashboard correction"})
                changed += 1
        con.commit()
        con.close()
        st.success(f"Saved {changed} correction(s). Run `reclassify` to propagate the learning.")
        st.cache_data.clear()
