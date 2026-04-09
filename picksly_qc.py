"""
picks.ly QC helpers (HTTP + optional headless browser).

Used by one-off backfill scripts — not part of the main Kakobuy scrape path.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict


def _picksly_parse_html(page_html: str) -> Dict[str, str]:
    from scraper import _og

    def parse_next_data(h: str) -> Dict[str, str]:
        payload = ""
        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            h,
            re.I | re.S,
        )
        if m:
            payload = m.group(1)
        else:
            marker = 'id="__NEXT_DATA__"'
            idx = h.find(marker)
            if idx != -1:
                gt = h.find(">", idx)
                end = h.find("</script>", gt + 1) if gt != -1 else -1
                if gt != -1 and end != -1:
                    payload = h[gt + 1 : end]

        if not payload:
            return {}

        try:
            data = json.loads(payload)
        except Exception:
            return {}

        strings: list[str] = []

        def flatten(o: Any) -> None:
            if isinstance(o, str):
                strings.append(o)
            elif isinstance(o, dict):
                for v in o.values():
                    flatten(v)
            elif isinstance(o, list):
                for v in o:
                    flatten(v)

        flatten(data)
        if not strings:
            return {}

        img_candidates: list[str] = []
        for s in strings:
            if not isinstance(s, str) or not s.startswith("http"):
                continue
            if not re.search(r"\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", s, re.I):
                continue
            if "og-image.jpg" in s:
                continue
            u = s.split("?")[0].split("!")[0].strip()
            if u:
                img_candidates.append(u)

        def _img_score(u: str) -> int:
            low = u.lower()
            sc = 0
            for host in ("weidianimg.com", "geilicdn.com", "alicdn.com", "gw.alicdn.com", "img.alicdn.com"):
                if host in low:
                    sc += 6
            if any(b in low for b in ("logo", "avatar", "icon", "banner", "sprite")):
                sc -= 8
            sc += min(len(u), 200) // 40
            return sc

        img_url = ""
        if img_candidates:
            img_url = max(img_candidates, key=_img_score)

        price_val = ""
        for s in strings:
            if isinstance(s, str) and re.fullmatch(r"\d{1,5}(?:\.\d{1,2})?", s):
                try:
                    v = float(s)
                except Exception:
                    continue
                if 0.5 < v < 9999:
                    price_val = s
                    break

        title_val = ""
        for s in strings:
            if not isinstance(s, str):
                continue
            t = s.strip()
            if len(t) < 6 or len(t) > 250:
                continue
            low = t.lower()
            if low.startswith("picks.ly") or "your go-to qc finder" in low:
                continue
            if "http://" in low or "https://" in low:
                continue
            if sum(ch.isalpha() for ch in t) < 4:
                continue
            if len(t) > len(title_val):
                title_val = t

        out: Dict[str, str] = {}
        if title_val:
            out["title"] = title_val
        if img_url:
            out["img"] = img_url
        if price_val:
            out["price"] = price_val
        return out

    next_data = parse_next_data(page_html)
    if next_data and next_data.get("img"):
        return next_data

    title = _og(page_html, "og:title")
    img = _og(page_html, "og:image")
    price = _og(page_html, "product:price:amount") or _og(page_html, "og:price:amount")

    if title and title.strip().lower().startswith("picks.ly"):
        title = ""
    if img and img.rstrip("/").endswith("/og-image.jpg"):
        img = ""

    if not title:
        m = re.search(r'"title"\s*:\s*"([^"]{6,250})"', page_html)
        if m:
            t = m.group(1).strip()
            low = t.lower()
            if not low.startswith("picks.ly") and "your go-to qc finder" not in low:
                title = t

    if not img:
        m = re.search(
            r'(https?://[^"\'<>\s]+?\.(?:jpg|jpeg|png|webp|gif))(?:\?[^"\'<>\s]*)?',
            page_html,
            re.I,
        )
        if m and "og-image.jpg" not in m.group(1):
            img = m.group(1)

    if not price:
        m = re.search(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", page_html)
        if m:
            price = m.group(1)

    out: Dict[str, str] = {}
    if title:
        out["title"] = title.strip()
    if img:
        out["img"] = img.split("?")[0].split("!")[0].strip()
    if price:
        out["price"] = str(price).strip()

    if next_data:
        if not out.get("title") and next_data.get("title"):
            out["title"] = next_data["title"]
        if not out.get("img") and next_data.get("img"):
            out["img"] = next_data["img"]
        pv = next_data.get("price", "")
        if pv and str(pv) not in {"0", "1"} and not out.get("price"):
            out["price"] = str(pv).strip()
    return out


def picksly_http_fallback(picksly_url: str) -> Dict[str, str]:
    """Title/price/img from picks.ly using only the initial HTTP response (no browser)."""
    from scraper import HEADERS, _fetch

    if not picksly_url:
        return {}
    try:
        _, html = _fetch(picksly_url.strip(), HEADERS, timeout=18)
    except Exception:
        return {}
    return _picksly_parse_html(html)


def _picksly_fallback_selenium(picksly_url: str) -> Dict[str, str]:
    from scraper import CDN_RE, IMAGE_EXT_RE, _get_driver

    low = picksly_url.lower()
    if not picksly_url or "picks.ly" not in low:
        return {}
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
    except Exception:
        return {}

    prev_headless = os.environ.get("SNAKEFINDS_HEADLESS")
    os.environ["SNAKEFINDS_HEADLESS"] = "1"
    driver = None
    try:
        driver = _get_driver()
        driver.set_page_load_timeout(45)
        driver.get(picksly_url)
        wait = WebDriverWait(driver, 28)
        try:
            wait.until(
                EC.presence_of_element_located(
                    (
                        By.CSS_SELECTOR,
                        "img[src*='weidian'], img[src*='geilicdn'], img[src*='alicdn'], "
                        "img[src*='gw.alicdn'], img[src*='img.alicdn'], picture img",
                    )
                )
            )
        except Exception:
            try:
                wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "main img, article img")))
            except Exception:
                pass
        for _ in range(4):
            time.sleep(0.7)
            try:
                driver.execute_script(
                    "window.scrollTo(0, Math.min(document.body.scrollHeight || 0, 1600));"
                )
            except Exception:
                break
        html = driver.page_source
        out = _picksly_parse_html(html)
        if out.get("img"):
            return out

        srcs: list[str] = []
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, "img[src], img[data-src]"):
                for attr in ("src", "data-src"):
                    src = (el.get_attribute(attr) or "").strip()
                    if not src or src.startswith("data:"):
                        continue
                    if IMAGE_EXT_RE.search(src) or CDN_RE.search(src):
                        low_src = src.lower()
                        if "og-image" in low_src or "twitter-image" in low_src:
                            continue
                        srcs.append(src.split("?")[0].split("!")[0])
        except Exception:
            pass
        cdn_srcs = [s for s in srcs if CDN_RE.search(s)]
        pick = cdn_srcs[0] if cdn_srcs else (srcs[0] if srcs else "")
        if pick:
            out["img"] = pick
        if not out.get("img"):
            m = CDN_RE.search(html)
            if m:
                cand = m.group(0).split("?")[0].split("!")[0]
                if "og-image" not in cand.lower():
                    out["img"] = cand
        return out
    except Exception:
        return {}
    finally:
        if prev_headless is None:
            os.environ.pop("SNAKEFINDS_HEADLESS", None)
        else:
            os.environ["SNAKEFINDS_HEADLESS"] = prev_headless
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def picksly_image_url(picksly_url: str) -> str:
    """Remote image URL for a picks.ly item (HTTP first, then headless render if needed)."""
    if not (picksly_url or "").strip():
        return ""
    out = picksly_http_fallback(picksly_url.strip())
    if out.get("img"):
        return out["img"].strip()
    js = _picksly_fallback_selenium(picksly_url.strip())
    return (js.get("img") or "").strip()
