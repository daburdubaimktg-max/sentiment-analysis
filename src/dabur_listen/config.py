"""Load settings, classification rules, and correction examples."""

import json
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
TRAINING_DIR = ROOT / "training"
DATA_DIR = ROOT / "data"
CORRECTIONS_PATH = TRAINING_DIR / "corrections.jsonl"
DB_PATH = DATA_DIR / "listening.db"

load_dotenv(ROOT / ".env")


def load_settings() -> dict:
    with open(CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_rules() -> dict:
    with open(CONFIG_DIR / "classification_rules.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_corrections(limit: int | None = None) -> list[dict]:
    """Most-recent-last list of correction examples for few-shot training."""
    if not CORRECTIONS_PATH.exists():
        return []
    rows = []
    with open(CORRECTIONS_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return rows[-limit:] if limit else rows


def append_correction(correction: dict) -> None:
    TRAINING_DIR.mkdir(exist_ok=True)
    with open(CORRECTIONS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(correction, ensure_ascii=False) + "\n")


def apify_token() -> str:
    token = os.environ.get("APIFY_TOKEN", "")
    if not token:
        raise RuntimeError("APIFY_TOKEN is not set — copy .env.example to .env and fill it in.")
    return token
