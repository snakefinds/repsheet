"""
One-off: resolve ikako.vip URLs to final destinations and normalize Kakobuy affcode.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data.json")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def ensure_affcode_dqfte(url: str) -> str:
    parsed = urlparse(url)
    if "kakobuy.com" not in parsed.netloc.lower():
        return url
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["affcode"] = ["dqfte"]
    new_q = urlencode(qs, doseq=True)
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_q, parsed.fragment)
    )


def resolve_ikako(url: str, cache: dict[str, str]) -> str:
    low = url.lower()
    if "ikako.vip" not in low:
        return url
    if url in cache:
        return cache[url]
    req = urllib.request.Request(url, headers=HEADERS, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            final = resp.geturl() or url
    except urllib.error.HTTPError as e:
        final = e.geturl() or url
    except Exception:
        cache[url] = url
        return url
    cache[url] = final
    time.sleep(0.15)
    return final


def fix_url_string(s: str, cache: dict[str, str]) -> str:
    t = s.strip()
    if not t.startswith(("http://", "https://")):
        return s
    u = resolve_ikako(t, cache)
    u = ensure_affcode_dqfte(u)
    return u


def walk(obj, cache: dict[str, str]):
    if isinstance(obj, dict):
        for k, v in obj.items():
            obj[k] = walk(v, cache)
        return obj
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            obj[i] = walk(v, cache)
        return obj
    if isinstance(obj, str):
        return fix_url_string(obj, cache)
    return obj


def normalize_all_kakobuy(obj):
    """Second pass: any kakobuy.com URL gets affcode=dqfte (including already-resolved)."""

    def inner(x):
        if isinstance(x, dict):
            for k, v in x.items():
                x[k] = inner(v)
            return x
        if isinstance(x, list):
            for i, v in enumerate(x):
                x[i] = inner(v)
            return x
        if isinstance(x, str):
            t = x.strip()
            if t.startswith(("http://", "https://")) and "kakobuy.com" in t.lower():
                return ensure_affcode_dqfte(t)
            return x
        return x

    return inner(obj)


def main():
    with open(DATA, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    cache: dict[str, str] = {}
    walk(data, cache)
    normalize_all_kakobuy(data)
    with open(DATA, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"resolved unique ikako URLs: {len(cache)}", flush=True)


if __name__ == "__main__":
    main()
