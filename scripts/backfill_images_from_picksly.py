"""Set item img from picks.ly QC pages where picksly URL exists.

Only updates items from the catalog entry titled FIRST_CATALOG_TITLE onward
(everything strictly before that title in data.json order is left unchanged).
"""

from __future__ import annotations
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from picksly_qc import picksly_image_url
from scraper import cache_image

DATA = os.path.join(ROOT, "data.json")

# Match is case-insensitive; must match the `title` in data.json for the first row to update.
FIRST_CATALOG_TITLE = "Air Force 1 x Ambush"


def _first_update_index(items: list) -> int | None:
    target = FIRST_CATALOG_TITLE.strip().casefold()
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        t = str(it.get("title", "")).strip().casefold()
        if t == target:
            return i
    return None


def main():
    with open(DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items", [])
    start = _first_update_index(items)
    if start is None:
        print(
            f'error: no item with title matching {FIRST_CATALOG_TITLE!r} (case-insensitive)',
            flush=True,
        )
        sys.exit(1)

    updated = 0
    skipped_before = start
    skipped_no_picksly = 0
    failed = 0
    for i, it in enumerate(items):
        if i < start:
            continue
        if not isinstance(it, dict):
            continue
        row = i + 1
        pl = str(it.get("picksly", "")).strip()
        if not pl:
            skipped_no_picksly += 1
            continue
        try:
            raw = picksly_image_url(pl)
            if not raw:
                failed += 1
                print(f"[row {row}] id={it.get('id')} no image from picks.ly", flush=True)
                continue
            local = cache_image(raw)
            if not local:
                failed += 1
                continue
            it["img"] = local
            updated += 1
            if row % 25 == 0:
                print(f"[{row}/{len(items)}] updated so far: {updated}", flush=True)
                with open(DATA, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=2, ensure_ascii=False)
            time.sleep(0.12)
        except Exception as e:
            failed += 1
            print(f"[row {row}] id={it.get('id')} error: {e}", flush=True)
    with open(DATA, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(
        f"done updated={updated} skipped_before_marker={skipped_before} "
        f"skipped_no_picksly_in_range={skipped_no_picksly} failed={failed}",
        flush=True,
    )


if __name__ == "__main__":
    main()
