"""Layers 2 & 3 — translation and classification via the Claude API.

Layer 2 (translate): any language/dialect -> English. Handles Arabizi
(Arabic in Latin script with digit-letters), Gulf/Egyptian/Maghrebi dialects,
Swahili & Sheng, Uzbek/Kazakh/Russian, emoji-only comments.

Layer 3 (classify): sentiment / intent / themes / topics / brand attribution /
market guess. Behavior is steered by config/classification_rules.yaml plus the
correction examples in training/corrections.jsonl — edit either and rerun
`reclassify` to re-flag existing data without re-translating or re-scraping.
"""

import json

import anthropic
import yaml

from .config import load_corrections, load_rules, load_settings

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# ── Layer 2: translation ──────────────────────────────────────────────────────

TRANSLATE_SYSTEM = """You are an expert social-media translator for a consumer-goods company \
operating across MENA, Africa, and Central Asia. You translate user comments into natural English.

You are fluent in:
- Modern Standard Arabic and dialects: Gulf (Khaleeji), Egyptian, Levantine, Iraqi, Maghrebi/Darija.
- Arabizi / Franco-Arabic: Arabic written in Latin letters with digits as letters \
(2=ء, 3=ع, 5/7'=خ, 6=ط, 7=ح, 8=غ/ق, 9=ص/ق). E.g. "7abibi" = حبيبي = "my dear", "3ajeeb" = amazing.
- Swahili and Sheng (Kenyan urban slang), Nigerian Pidgin, Hausa, Amharic.
- Uzbek, Kazakh, Russian, Turkish, Hindi/Urdu (incl. Roman script), French.
- Emoji and internet slang: an emoji-only comment still gets a "translation" describing what it expresses \
(e.g. "❤️🔥" -> "love / excitement").

Rules:
- Translate meaning and TONE, not word-for-word. Keep sarcasm sarcastic.
- Keep brand and product names as-is.
- Keep emojis in the translation.
- If text is already English, return it unchanged with detected_language "english".
- detected_language must name dialect where relevant (e.g. "arabic (egyptian)", "arabizi (gulf)", "sheng").
- notes: only when something is genuinely non-obvious (slang decode, sarcasm flag); otherwise empty string."""

TRANSLATE_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "detected_language": {"type": "string"},
                        "is_arabizi": {"type": "boolean"},
                        "translation": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["id", "detected_language", "is_arabizi", "translation", "notes"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}


def translate_batch(comments: list[dict]) -> dict[int, dict]:
    """comments: [{id, text}]. Returns {id: translation-result}."""
    settings = load_settings()
    payload = json.dumps(
        [{"id": c["id"], "text": c["text"]} for c in comments], ensure_ascii=False, indent=1
    )
    response = client().messages.create(
        model=settings["model"],
        max_tokens=16000,
        system=[{"type": "text", "text": TRANSLATE_SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": TRANSLATE_SCHEMA},
        messages=[{
            "role": "user",
            "content": "Translate every comment in this JSON array. Return one result per id:\n" + payload,
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Translation request was refused by the model.")
    text = next(b.text for b in response.content if b.type == "text")
    return {r["id"]: r for r in json.loads(text)["results"]}


# ── Layer 3: classification ───────────────────────────────────────────────────

CLASSIFY_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                        "confidence": {"type": "number"},
                        "intent": {
                            "type": "string",
                            "enum": ["purchase_intent", "consideration", "advocacy", "complaint",
                                     "usage_question", "general_engagement", "spam", "other"],
                        },
                        "themes": {"type": "array", "items": {"type": "string"}},
                        "topics": {"type": "array", "items": {"type": "string"}},
                        "brand_mentions": {"type": "array", "items": {"type": "string"}},
                        "entity": {"type": "string", "enum": ["own", "competitor", "both", "none"]},
                        "market": {"type": "string"},
                    },
                    "required": ["id", "sentiment", "confidence", "intent", "themes",
                                 "topics", "brand_mentions", "entity", "market"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["results"],
        "additionalProperties": False,
    },
}


def build_classify_system() -> str:
    """Assemble the classifier system prompt from settings + rules + corrections."""
    settings = load_settings()
    rules = load_rules()
    corrections = load_corrections(limit=rules.get("max_corrections_in_prompt", 40))

    own = settings["brands"]["own"]
    comp = settings["brands"]["competitors"]
    markets = settings["markets"]

    parts = [
        "You classify social-media comments for Dabur International's social listening system.",
        "\n## Our brands (entity = 'own')\n" + yaml.safe_dump(own, allow_unicode=True),
        "\n## Competitor brands (entity = 'competitor')\n" + yaml.safe_dump(comp, allow_unicode=True),
        "\n## Markets — guess the commenter's market from dialect, currency, places; use code, or 'unknown'\n"
        + yaml.safe_dump(markets, allow_unicode=True),
        "\n## Sentiment rules (follow these EXACTLY — they override your defaults)\n"
        + "\n".join(f"- {r}" for r in rules["sentiment_rules"]),
        "\n## Intent rules\n" + "\n".join(f"- {r}" for r in rules["intent_rules"]),
        "\n## Theme taxonomy — themes[] may ONLY contain these values\n"
        + "\n".join(f"- {t}" for t in rules["themes"]),
        "\n## Other fields\n"
        "- topics: 1-3 short free-form topic phrases (e.g. 'hair fall after use', 'ramadan gifting').\n"
        "- brand_mentions: canonical brand names actually referenced (explicitly or via alias).\n"
        "- entity: own/competitor/both/none based on brand_mentions; if the comment sits under one of our "
        "posts and mentions no brand, use 'own'.\n"
        "- confidence: 0.0-1.0 for the sentiment call.",
    ]
    if corrections:
        parts.append(
            "\n## Worked examples from human reviewers (match these judgments on similar comments)\n"
            + "\n".join(json.dumps(c, ensure_ascii=False) for c in corrections)
        )
    return "\n".join(parts)


def classify_batch(comments: list[dict], system_prompt: str) -> dict[int, dict]:
    """comments: [{id, text, translation, detected_language}]. Returns {id: result}."""
    settings = load_settings()
    payload = json.dumps(
        [
            {
                "id": c["id"],
                "original": c["text"],
                "english": c.get("translation") or c["text"],
                "language": c.get("detected_language") or "unknown",
            }
            for c in comments
        ],
        ensure_ascii=False,
        indent=1,
    )
    response = client().messages.create(
        model=settings["model"],
        max_tokens=16000,
        system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": CLASSIFY_SCHEMA},
        messages=[{
            "role": "user",
            "content": "Classify every comment in this JSON array. Return one result per id:\n" + payload,
        }],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Classification request was refused by the model.")
    text = next(b.text for b in response.content if b.type == "text")
    return {r["id"]: r for r in json.loads(text)["results"]}
