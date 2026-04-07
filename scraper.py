"""
scraper.py – SnakeFinds product scraper (no Selenium).

Takes an ikako.vip / kakobuy link and extracts:
- title
- price
- image URL
- picks.ly QC link

Strategy:
1) Follow redirect to Kakobuy page and extract the underlying marketplace URL (?url=...).
2) Query Kakobuy's JSON API with URL-encoded source URL (most reliable when available).
3) Fallback to Kakobuy page OpenGraph tags.
4) If we have a picks.ly link, optionally fallback to picks.ly *only if* it provides non-generic OG tags.
"""

import json
import re
import urllib.request
from typing import Any, Dict, Tuple
from urllib.parse import parse_qs, unquote, urlencode, urlparse
import os
import shutil


HEADERS: Dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.kakobuy.com/",
}
JSON_HEADERS: Dict[str, str] = {**HEADERS, "Accept": "application/json, text/plain, */*"}

CDN_RE = re.compile(
    r"https?://[^\s\"'<>]*?"
    r"(?:geilicdn\.com|alicdn\.com|weidianimg\.com|gw\.alicdn\.com|img\.alicdn\.com|"
    r"yupoo\.com|tbcdn\.cn|oss-cn|alicdn\.net|media-amazon\.com|goat\.com|"
    r"imgur\.com|discordapp\.(?:com|net)|pinimg\.com|cloudinary\.com|reddit\.com|"
    r"ibb\.co|fbcdn\.net|googleusercontent\.com|upic\.me|imgbb\.com|postimg\.cc)[^\s\"'<>]*?"
    r"\.(?:jpg|jpeg|png|webp|gif)",
    re.I,
)

IMAGE_EXT_RE = re.compile(r"\.(?:jpg|jpeg|png|webp|gif)(?:\?|$)", re.I)


def _get_driver():
    """
    Headless browser for JS-rendered pages.

    Prefers Microsoft Edge (usually installed on Windows) because Chrome may not be present.
    Falls back to Chrome if available.
    """
    from selenium import webdriver

    # Try Edge first (uses Selenium Manager to locate/download driver)
    headless = os.environ.get("SNAKEFINDS_HEADLESS", "0").strip() not in {"0", "false", "False", ""}

    try:
        from selenium.webdriver.edge.options import Options as EdgeOptions

        opts = EdgeOptions()
        if headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--disable-gpu")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--window-size=1280,900")
        opts.add_argument("--lang=en-US")
        try:
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)
        except Exception:
            pass
        opts.add_argument(
            "user-agent="
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )

        driver = webdriver.Edge(options=opts)
        try:
            driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception:
            pass
        return driver
    except Exception:
        pass

    # Fall back to Chrome if present
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.chrome.service import Service as ChromeService
    from webdriver_manager.chrome import ChromeDriverManager

    chrome_bin = shutil.which("chrome") or shutil.which("chrome.exe")
    opts = ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1280,900")
    opts.add_argument("--lang=en-US")
    if chrome_bin:
        opts.binary_location = chrome_bin
    svc = ChromeService(ChromeDriverManager().install())
    return webdriver.Chrome(service=svc, options=opts)


def _fetch(u: str, headers: Dict[str, str], timeout: int = 18) -> Tuple[str, str]:
    req = urllib.request.Request(u, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.url
        body = resp.read().decode("utf-8", errors="ignore")
        return final_url, body


def _fetch_json(u: str, timeout: int = 12) -> Any:
    _, body = _fetch(u, JSON_HEADERS, timeout=timeout)
    return json.loads(body)


def resolve_image_url(url: str) -> str:
    """
    Normalize/resolve image URLs.

    - If `url` already points to an image, return it (stripped of query/fragment).
    - If it's a Yupoo album/photo page, fetch and extract the first direct image URL.
    """
    if not url:
        return ""
    u = url.strip()

    if re.search(r"\.(jpg|jpeg|png|webp)(?:\?|#|$)", u, re.I):
        return u.split("?")[0].split("#")[0]

    if "yupoo.com" in u:
        try:
            _, html = _fetch(u, HEADERS, timeout=18)
        except Exception:
            return u

        og_img = _og(html, "og:image")
        if og_img and re.search(r"\.(jpg|jpeg|png|webp)(?:\?|$)", og_img, re.I):
            return og_img.split("?")[0].split("#")[0]

        m = re.search(
            r"(https?://[^\"'<>\\s]+\\.(?:jpg|jpeg|png|webp))(?:\\?[^\"'<>\\s]*)?",
            html,
            re.I,
        )
        if m:
            return m.group(1).split("?")[0].split("#")[0]

    return u


def _og(html: str, prop: str) -> str:
    for pat in (
        r'<meta\b[^>]+\bproperty=["\']' + re.escape(prop) + r'["\'][^>]+\bcontent=["\']([^"\']+)',
        r'<meta\b[^>]+\bcontent=["\']([^"\']+)["\'][^>]+\bproperty=["\']' + re.escape(prop) + r'["\']',
    ):
        m = re.search(pat, html, re.I | re.S)
        if m:
            return m.group(1).strip()
    return ""


def _extract_source_url(final_url: str, kb_html: str) -> str:
    parsed = urlparse(final_url)
    qs = parse_qs(parsed.query)
    source_url = unquote(qs.get("url", [""])[0])
    if source_url:
        return source_url

    # Sometimes embedded in HTML
    m = re.search(
        r'["\']url["\']\s*:\s*["\'](https?://(?:weidian|taobao|1688|detail\.tmall)[^"\']+)',
        kb_html,
        re.I,
    )
    return unquote(m.group(1)) if m else ""


def _build_picksly(source_url: str) -> str:
    if not source_url:
        return ""
    if "weidian.com" in source_url:
        m = re.search(r"itemID[=\s]*(\d+)", source_url)
        return f"https://picks.ly/item/WD{m.group(1)}" if m else ""
    if "taobao.com" in source_url or "tmall.com" in source_url:
        m = re.search(r"[?&]id=(\d+)", source_url)
        return f"https://picks.ly/item/TB{m.group(1)}" if m else ""
    if "1688.com" in source_url:
        m = re.search(r"/offer/(\d+)", source_url)
        return f"https://picks.ly/item/ALI{m.group(1)}" if m else ""
    return ""


def _kakobuy_api_lookup(source_url: str) -> Dict[str, str]:
    if not source_url:
        return {}

    q = urlencode({"url": source_url})
    for api_url in (
        f"https://www.kakobuy.com/api/v1/item/query-item-info?{q}",
        f"https://www.kakobuy.com/api/v1/item/info?{q}",
        f"https://www.kakobuy.com/api/item?{q}",
    ):
        try:
            data = _fetch_json(api_url)
        except Exception:
            continue

        item = data.get("data") or data.get("result") or data.get("item") or data
        if isinstance(item, list):
            item = item[0] if item else {}
        if not isinstance(item, dict):
            continue

        title = item.get("title") or item.get("name") or item.get("subject") or item.get("itemTitle") or ""
        price = (
            item.get("price")
            or (item.get("priceInfo") or {}).get("price")
            or item.get("itemPrice")
            or ""
        )
        imgs = item.get("images") or item.get("imgList") or item.get("itemImgList") or []
        img = ""
        if isinstance(imgs, list) and imgs:
            img = str(imgs[0])
        else:
            img = item.get("img") or item.get("image") or item.get("pic") or ""

        out: Dict[str, str] = {}
        if title:
            out["title"] = str(title).strip()
        if price != "":
            out["price"] = str(price).strip()
        if img:
            out["img"] = str(img).split("?")[0].split("!")[0].strip()

        if out:
            return out

    return {}


def _picksly_fallback(picksly_url: str) -> Dict[str, str]:
    """
    picks.ly often sets generic OG values that are NOT the product.
    We only accept it if it looks non-generic.
    """
    if not picksly_url:
        return {}

    try:
        _, html = _fetch(picksly_url, HEADERS, timeout=18)
    except Exception:
        return {}

    def parse_next_data() -> Dict[str, str]:
        # Prefer regex, but fall back to string slicing (more robust against minification).
        payload = ""
        m = re.search(
            r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
            html,
            re.I | re.S,
        )
        if m:
            payload = m.group(1)
        else:
            marker = 'id="__NEXT_DATA__"'
            idx = html.find(marker)
            if idx != -1:
                gt = html.find(">", idx)
                end = html.find("</script>", gt + 1) if gt != -1 else -1
                if gt != -1 and end != -1:
                    payload = html[gt + 1 : end]

        if not payload:
            return {}

        try:
            data = json.loads(payload)
        except Exception:
            return {}

        # Flatten strings and pick best candidates (more resilient to shape changes).
        strings = []

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

        # Image: first plausible image URL that isn't the generic OG image.
        img_url = ""
        for s in strings:
            if isinstance(s, str) and s.startswith("http") and re.search(r"\.(?:jpg|jpeg|png|webp)(?:\?|$)", s, re.I):
                if "og-image.jpg" in s:
                    continue
                img_url = s.split("?")[0].split("!")[0]
                break

        # Price: first realistic number, or USD string patterns.
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

        # Title: longest "human" string that isn't generic.
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
            # heuristic: title-like strings usually have spaces and letters
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

    # First try to parse real item data from Next.js payload.
    next_data = parse_next_data()
    if next_data:
        return next_data

    # Try OG tags first (often generic on picks.ly).
    title = _og(html, "og:title")
    img = _og(html, "og:image")
    price = _og(html, "product:price:amount") or _og(html, "og:price:amount")

    if title and title.strip().lower().startswith("picks.ly"):
        title = ""
    if img and img.rstrip("/").endswith("/og-image.jpg"):
        img = ""

    # If OG tags were generic/missing, try to regex real values out of the HTML
    # (Next.js pages often inline JSON).
    if not title:
        m = re.search(r'"title"\s*:\s*"([^"]{6,250})"', html)
        if m:
            t = m.group(1).strip()
            low = t.lower()
            if not low.startswith("picks.ly") and "your go-to qc finder" not in low:
                title = t

    if not img:
        m = re.search(
            r'(https?://[^"\'<>\s]+?\.(?:jpg|jpeg|png|webp))(?:\?[^"\'<>\s]*)?',
            html,
            re.I,
        )
        if m and "og-image.jpg" not in m.group(1):
            img = m.group(1)

    if not price:
        m = re.search(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", html)
        if m:
            price = m.group(1)

    out: Dict[str, str] = {}
    if title:
        out["title"] = title.strip()
    if img:
        out["img"] = img.split("?")[0].split("!")[0].strip()
    if price:
        out["price"] = str(price).strip()
    return out


def scrape_ikako(url: str) -> Dict[str, str]:
    # ── 0. Handle direct image links immediately ─────────────────────────────
    if IMAGE_EXT_RE.search(url.split("?")[0]):
        return {
            "kakobuy": url,
            "picksly": "",
            "title": url.split("/")[-1].split("?")[0],
            "img": url,
            "price": "",
            "category": "",
        }

    # First try JS-rendered scraping (Kakobuy/picks.ly are SPA shells without JS).
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        debug_keep_open = os.environ.get("SNAKEFINDS_KEEP_OPEN", "0").strip() not in {"0", "false", "False", ""}

        driver = _get_driver()
        try:
            driver.get(url)

            final_url = driver.current_url
            parsed = urlparse(final_url)
            qs = parse_qs(parsed.query)
            source_url = unquote(qs.get("url", [""])[0])
            picksly = _build_picksly(source_url)

            # Wait for content to render (Kakobuy can be slow)
            wait = WebDriverWait(driver, 45)
            try:
                wait.until(
                    EC.presence_of_element_located(
                        (By.CSS_SELECTOR, "h1, [class*='title'], [class*='price'], img, main")
                    )
                )
            except Exception:
                pass

            # Scroll a bit to trigger lazy-loaded content.
            try:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.35);")
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.70);")
                driver.execute_script("window.scrollTo(0, 0);")
            except Exception:
                pass

            html = driver.page_source

            result: Dict[str, str] = {
                "kakobuy": url,
                "picksly": picksly,
                "title": "",
                "img": "",
                "price": "",
                "category": "",
            }

            # NOTE: We intentionally do NOT navigate to picks.ly here.
            # The previous behavior caused the Kakobuy page to flash and close before it could load.

            # Title
            texts = []
            for sel in (
                "h1",
                "[class*='goods-title']",
                "[class*='item-title']",
                "[class*='product-title']",
                "[class*='detail-title']",
                "[class*='title']",
                "[class*='name']",
            ):
                try:
                    for el in driver.find_elements(By.CSS_SELECTOR, sel):
                        t = (el.text or "").strip()
                        if not t:
                            continue
                        low = t.lower()
                        if "kakobuy" in low or "taobao agent" in low:
                            continue
                        if len(t) < 4 or len(t) > 300:
                            continue
                        texts.append(t)
                except Exception:
                    continue
            if texts:
                # pick the longest plausible title
                result["title"] = max(texts, key=len)

            if not result["title"]:
                dt = (driver.title or "").strip()
                if dt and "kakobuy" not in dt.lower():
                    result["title"] = dt

            if not result["title"]:
                t = _og(html, "og:title")
                if t and "kakobuy" not in t.lower():
                    result["title"] = t

            # Image
            im = _og(html, "og:image")
            if im:
                low = im.lower()
                if "logo" not in low and "banner" not in low and "icon" not in low:
                    result["img"] = im.split("?")[0].split("!")[0]

            if not result["img"]:
                # Prefer real product images over banners
                srcs = []
                for el in driver.find_elements(By.CSS_SELECTOR, "img"):
                    src = (el.get_attribute("src") or el.get_attribute("data-src") or "").strip()
                    if not src.startswith("http"):
                        continue
                    low = src.lower()
                    if any(bad in low for bad in ("logo", "banner", "icon", "sprite", "favicon", "avatar", "nstatic.kakobuy.com/banner")):
                        continue
                    if re.search(r"\.(jpg|jpeg|png|webp)(?:\\?|$)", src, re.I):
                        srcs.append(src.split("?")[0].split("!")[0])
                # Prefer known CDN patterns if possible
                cdn_srcs = [s for s in srcs if CDN_RE.search(s)]
                pick = (cdn_srcs[0] if cdn_srcs else (srcs[0] if srcs else ""))
                if pick:
                    result["img"] = pick

            if not result["img"]:
                m = CDN_RE.search(html)
                if m:
                    cand = m.group(0).split("?")[0].split("!")[0]
                    low = cand.lower()
                    if "banner" not in low and "logo" not in low:
                        result["img"] = cand

            # Price
            pr = _og(html, "og:price:amount") or _og(html, "product:price:amount")
            if pr:
                result["price"] = pr.strip()
            if not result["price"]:
                # search visible text blocks
                for sel in ("[class*='price']", "[class*='Price']", "[class*='amount']", "[class*='cost']", "span", "div"):
                    try:
                        els = driver.find_elements(By.CSS_SELECTOR, sel)
                        for el in els:
                            txt = (el.text or "").strip()
                            if not txt or len(txt) > 80:
                                continue
                            if "$" not in txt and "usd" not in txt.lower():
                                continue
                            m = re.search(r"([0-9]+(?:\\.[0-9]{1,2})?)", txt.replace(",", ""))
                            if m:
                                val = float(m.group(1))
                                if 0.5 < val < 99999:
                                    result["price"] = m.group(1)
                                    break
                        if result["price"]:
                            break
                    except Exception:
                        continue

            if debug_keep_open:
                input("SNAKEFINDS_KEEP_OPEN=1: Press Enter to close browser and continue...")

            if result.get("img"):
                result["img"] = resolve_image_url(result["img"])
            return result
        finally:
            if not debug_keep_open:
                try:
                    driver.quit()
                except Exception:
                    pass
    except Exception:
        # Fall back to non-JS scraping (may not return title/img/price for SPA pages).
        pass

    final_url, kb_html = _fetch(url, HEADERS, timeout=18)
    source_url = _extract_source_url(final_url, kb_html)
    picksly = _build_picksly(source_url)

    result = {
        "kakobuy": url,
        "picksly": picksly,
        "title": "",
        "img": "",
        "price": "",
        "category": "",
    }

    api = _kakobuy_api_lookup(source_url)
    result.update({k: v for k, v in api.items() if v})

    if not result["title"]:
        t = _og(kb_html, "og:title")
        if t and "kakobuy" not in t.lower():
            result["title"] = t
    if not result["img"]:
        im = _og(kb_html, "og:image")
        if im and "logo" not in im.lower():
            result["img"] = im.split("?")[0].split("!")[0]
    if not result["price"]:
        pr = _og(kb_html, "og:price:amount") or _og(kb_html, "product:price:amount")
        if pr:
            result["price"] = pr

    if (not result["title"] or not result["img"] or not result["price"] or result["price"] in {"0", "1"}) and picksly:
        qc = _picksly_fallback(picksly)
        for k in ("title", "img", "price"):
            if (not result[k] or (k == "price" and result["price"] in {"0", "1"})) and qc.get(k):
                result[k] = qc[k]

    if not result["img"]:
        m = CDN_RE.search(kb_html)
        if m:
            result["img"] = m.group(0).split("?")[0].split("!")[0]

    if result.get("img"):
        result["img"] = resolve_image_url(result["img"])

    return result
