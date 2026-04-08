"""Normalize every item kakobuy URL in data.json (no ikako left; affcode=dqfte)."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from scraper import normalize_stored_kakobuy_link

DATA = os.path.join(ROOT, "data.json")


def main():
    with open(DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items", [])
    for i, it in enumerate(items):
        kb = it.get("kakobuy", "")
        it["kakobuy"] = normalize_stored_kakobuy_link(kb if isinstance(kb, str) else str(kb))
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{len(items)}", flush=True)
    with open(DATA, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"done {len(items)} items", flush=True)


if __name__ == "__main__":
    main()
