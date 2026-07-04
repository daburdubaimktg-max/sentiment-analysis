"""Smoke tests for the listening pipeline — no network or API keys required.

Covers: Apify item normalization, platform detection, DB round-trip
(insert/dedupe/translate/classify/manual label), classifier prompt assembly,
corrections file, and the Streamlit dashboard rendering with seeded data.
"""

import json

import pytest

import dabur_listen.config as config
import dabur_listen.db as db
from dabur_listen.ingest import detect_platform, normalize_item


@pytest.fixture(autouse=True)
def tmp_paths(tmp_path, monkeypatch):
    """Point every data path at a temp dir so tests never touch real data."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(config, "TRAINING_DIR", tmp_path)
    monkeypatch.setattr(config, "CORRECTIONS_PATH", tmp_path / "corrections.jsonl")
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    yield tmp_path


FAKE_ITEMS = [
    {"text": "بكم سعره في السعودية؟ ❤️", "ownerUsername": "user1", "likesCount": 12,
     "timestamp": "2026-07-01T10:00:00Z", "postUrl": "https://www.instagram.com/p/ABC/", "id": "c1"},
    {"comment": "7abibi this oil 3ajeeb 🔥", "uniqueId": "tk_user", "diggCount": 4,
     "createTimeISO": "2026-07-02T11:00:00Z", "videoWebUrl": "https://www.tiktok.com/@x/video/1", "cid": "c2"},
    {"text": "", "id": "empty-should-drop"},
]


def normalized_rows():
    rows = [normalize_item(i, "instagram", "url", "https://www.instagram.com/p/ABC/", "test")
            for i in FAKE_ITEMS]
    return [r for r in rows if r]


def test_normalize_and_platform_detection():
    rows = normalized_rows()
    assert len(rows) == 2                      # empty-text item dropped
    assert rows[0]["author"] == "user1"
    assert rows[0]["likes"] == 12
    assert rows[1]["text"].startswith("7abibi")
    assert detect_platform("https://www.tiktok.com/@x/video/1") == "tiktok"
    assert detect_platform("https://youtu.be/abc") == "youtube"
    assert detect_platform("https://x.com/user/status/1") == "x"
    with pytest.raises(ValueError):
        detect_platform("https://example.com/post/1")


def test_db_round_trip():
    rows = normalized_rows()
    with db.connect() as con:
        assert db.insert_comments(con, rows) == 2
        assert db.insert_comments(con, rows) == 0   # duplicates ignored

        pend = db.pending_translation(con)
        assert len(pend) == 2
        db.save_translation(con, pend[0]["id"], {
            "detected_language": "arabic (gulf)", "is_arabizi": False,
            "translation": "How much is it in Saudi? ❤️", "notes": ""})

        pc = db.pending_classification(con)
        assert len(pc) == 1
        db.save_classification(con, pc[0]["id"], {
            "sentiment": "neutral", "confidence": 0.9, "intent": "purchase_intent",
            "themes": ["price_value"], "topics": ["price in KSA"],
            "brand_mentions": ["Vatika"], "entity": "own", "market": "KSA"})

        db.apply_manual_label(con, pc[0]["id"], "positive", None)
        row = con.execute("SELECT sentiment, manually_corrected FROM comments WHERE id=?",
                          (pc[0]["id"],)).fetchone()
        assert row["sentiment"] == "positive" and row["manually_corrected"] == 1

        # manually corrected rows are excluded from reclassification;
        # the only translated row was just corrected, so nothing is pending
        assert len(db.pending_classification(con, include_done=True)) == 0

        s = db.stats(con)
        assert s["total"] == 2 and s["classified"] == 1


def test_classifier_prompt_assembly():
    from dabur_listen.enrich import CLASSIFY_SCHEMA, TRANSLATE_SCHEMA, build_classify_system
    prompt = build_classify_system()
    assert "Heart emojis" in prompt          # sentiment rules injected
    assert "Vatika" in prompt                # own brands injected
    assert "Sunsilk" in prompt               # competitors injected
    assert "UZB" in prompt and "KAZ" in prompt  # markets injected
    assert "counterfeit" in prompt           # theme taxonomy injected
    json.dumps(TRANSLATE_SCHEMA)             # schemas are valid JSON
    json.dumps(CLASSIFY_SCHEMA)


def test_corrections_append_and_feed_prompt():
    from dabur_listen.enrich import build_classify_system
    config.append_correction({"text": "🙌🙌", "correct": {"sentiment": "positive"},
                              "note": "raised hands are positive"})
    assert config.load_corrections()[-1]["note"] == "raised hands are positive"
    assert "raised hands are positive" in build_classify_system()


def seed_enriched_db():
    rows = [
        dict(platform="tiktok", source_type="keyword", source_value="#vatika",
             tracking_tag="demo", external_id=f"t{i}", post_url="https://tiktok.com/x",
             author=f"u{i}", text=t, likes=i,
             posted_at=f"2026-07-0{1 + i % 3}T10:00:00Z", raw={})
        for i, t in enumerate(["❤️❤️", "بكم سعره؟", "this oil ruined my hair",
                               "sunsilk is better", "mashallah great results"])
    ]
    langs = ["emoji", "arabic (gulf)", "english", "english", "arabic (transliterated)"]
    trans = ["love ❤️❤️", "How much is it?", "this oil ruined my hair",
             "sunsilk is better", "Mashallah great results"]
    sents = ["positive", "neutral", "negative", "negative", "positive"]
    intents = ["advocacy", "purchase_intent", "complaint", "other", "advocacy"]
    ents = ["own", "own", "own", "competitor", "own"]
    mkts = ["UAE", "KSA", "unknown", "NGA", "EGY"]
    with db.connect() as con:
        db.insert_comments(con, rows)
        for r, lg, tr, s, it, e, m in zip(db.pending_translation(con),
                                          langs, trans, sents, intents, ents, mkts):
            db.save_translation(con, r["id"], {"detected_language": lg, "is_arabizi": False,
                                               "translation": tr, "notes": ""})
            db.save_classification(con, r["id"], {
                "sentiment": s, "confidence": 0.9, "intent": it, "themes": ["efficacy"],
                "topics": ["hair oil"],
                "brand_mentions": ["Vatika" if e == "own" else "Sunsilk"],
                "entity": e, "market": m})


def _clear_streamlit_cache():
    # st.cache_data is process-global; clear it so dashboard tests are independent
    import streamlit as st
    st.cache_data.clear()


def test_dashboard_renders_with_data():
    from streamlit.testing.v1 import AppTest
    _clear_streamlit_cache()
    seed_enriched_db()
    at = AppTest.from_file(str(config.ROOT / "dashboard" / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.title[0].value.endswith("Social Listening")
    labels = [m.label for m in at.metric]
    assert "Mentions analyzed" in labels and "Net sentiment" in labels
    assert len(at.tabs) == 4


def test_dashboard_empty_state():
    from streamlit.testing.v1 import AppTest
    _clear_streamlit_cache()
    at = AppTest.from_file(str(config.ROOT / "dashboard" / "app.py"), default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.info                            # "No data yet" message shown
