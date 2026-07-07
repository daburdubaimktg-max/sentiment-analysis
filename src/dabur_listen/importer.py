"""Import Apify dataset exports (JSON / XLSX / CSV) straight into the DB.

Useful when a scrape was run in the Apify console and downloaded, rather than
triggered by this pipeline.
"""

import csv
import json
from pathlib import Path

from .ingest import URL_FIELDS, _first, detect_platform, normalize_item


def _read_items(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else data.get("items", [])
    if suffix in (".xlsx", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = [str(h) if h is not None else "" for h in next(rows)]
        return [dict(zip(header, r)) for r in rows if any(v is not None for v in r)]
    if suffix == ".csv":
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    raise ValueError(f"Unsupported file type: {suffix} (use .json, .xlsx or .csv)")


def import_file(path: str, platform: str | None = None, tag: str | None = None) -> list[dict]:
    p = Path(path)
    items = _read_items(p)
    if not items:
        return []
    if platform is None:
        url = _first(items[0], URL_FIELDS)
        if url:
            platform = detect_platform(str(url))
        else:
            raise ValueError("Cannot detect platform from file — pass --platform")
    rows = [normalize_item(i, platform, "url", str(_first(i, URL_FIELDS) or p.name), tag)
            for i in items]
    return [r for r in rows if r]
