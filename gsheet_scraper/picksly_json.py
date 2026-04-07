"""
Add picks.ly links to an existing scraped JSON (no Google Sheet access).

Expects the same shape as sheet_scrape.json: {"items": [{..., "kakobuy": "..."}, ...]}
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .gsheet_scraper import build_picksly_from_kakobuy_url


def add_picksly_to_items(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of data with each item's `picksly` set from `kakobuy`."""
    out = dict(data)
    items = out.get("items")
    if not isinstance(items, list):
        return out

    new_items: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            new_items.append(it)
            continue
        row = dict(it)
        row["picksly"] = build_picksly_from_kakobuy_url(str(row.get("kakobuy") or ""))
        new_items.append(row)
    out["items"] = new_items
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Add picks.ly URLs to scraped JSON (from kakobuy links only)."
    )
    p.add_argument("input", help="Input JSON path (e.g. sheet_scrape.json).")
    p.add_argument(
        "-o",
        "--out",
        default="",
        help="Output JSON path (default: overwrite input).",
    )
    args = p.parse_args(argv)

    with open(args.input, encoding="utf-8") as f:
        data = json.load(f)

    updated = add_picksly_to_items(data)
    out_path = args.out or args.input

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2, ensure_ascii=False)
        f.write("\n")

    n = len(updated.get("items") or [])
    picks = sum(
        1
        for it in (updated.get("items") or [])
        if isinstance(it, dict) and (it.get("picksly") or "").strip()
    )
    print(f"Wrote {out_path} ({n} items, {picks} with picks.ly).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
