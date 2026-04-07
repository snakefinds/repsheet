import json
import re
import urllib.request
from urllib.parse import urlparse, parse_qs, unquote, urlencode
from typing import Dict, Any, Tuple


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.kakobuy.com/",
}
JSON_HEADERS = {**HEADERS, "Accept": "application/json, text/plain, */*"}

CDN_RE = re.compile(
    r'https://[^\s"\'<>]*'
    r"(?:geilicdn\.com|alicdn\.com|weidianimg\.com|gw\.alicdn\.com|img\.alicdn\.com|"
    r"yupoo\.com|tbcdn\.cn|oss-cn|alicdn\.net|cdn)[^\s\"'<>]*"
    r"\.(?:jpg|jpeg|png|webp)",
    re.I,
)


def _fetch(u: str, headers: Dict[str, str], timeout: int = 15) -> Tuple[str, str]:
    req = urllib.request.Request(u, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        final_url = resp.url
        body = resp.read().decode("utf-8", errors="ignore")
        return final_url, body


def _fetch_json(u: str, timeout: int = 12) -> Any:
    _, body = _fetch(u, JSON_HEADERS, timeout=timeout)
    return json.loads(body)


def _og(html: str, prop: str) -> str:
    for pat in (
        r'<meta\b[^>]+\bproperty=["\']' + re.escape(prop) + r'["\'][^>]+\bcontent=["\']([^"\']+)',
        r'<meta\b[^>]+\bcontent=["\']([^"\']+)["\'][^>]+\bproperty=["\']' + re.escape(prop) + r'["\']',
    ):
        m = re.search(pat, html, re.I | re.S)
        if m:
            return m.group(1).strip()
    return ""


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


def _extract_source_url(final_url: str, kb_html: str) -> str:
    parsed = urlparse(final_url)
    qs = parse_qs(parsed.query)
    source_url = unquote(qs.get("url", [""])[0])
    if source_url:
        return source_url

    m = re.search(
        r'["\']url["\']\s*:\s*["\'](https?://(?:weidian|taobao|1688|detail\.tmall)[^"\']+)',
        kb_html,
        re.I,
    )
    return unquote(m.group(1)) if m else ""


def _kakobuy_api_lookup(source_url: str) -> Dict[str, str]:
    if not source_url:
        return {}

    q = urlencode({"url": source_url})
    api_urls = [
        f"https://www.kakobuy.com/api/v1/item/query-item-info?{q}",
        f"https://www.kakobuy.com/api/v1/item/info?{q}",
        f"https://www.kakobuy.com/api/item?{q}",
    ]

    for api_url in api_urls:
        try:
            data = _fetch_json(api_url)
        except Exception:
            continue

        item = data.get("data") or data.get("result") or data.get("item") or data
        if isinstance(item, list):
            item = item[0] if item else {}
        if not isinstance(item, dict):
            continue

        title = (
            item.get("title")
            or item.get("name")
            or item.get("subject")
            or item.get("itemTitle")
            or ""
        )
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
    if not picksly_url:
        return {}

    try:
        _, html = _fetch(picksly_url, HEADERS, timeout=15)
    except Exception:
        return {}

    title = _og(html, "og:title")
    img = _og(html, "og:image")
    price = _og(html, "product:price:amount") or _og(html, "og:price:amount") or ""
    if not price:
        m = re.search(r"\$\s*([0-9]+(?:\.[0-9]{1,2})?)", html)
        if m:
            price = m.group(1)

    out: Dict[str, str] = {}
    if title:
        out["title"] = title
    if img:
        out["img"] = img.split("?")[0].split("!")[0]
    if price:
        out["price"] = price
    return out


def scrape_ikako(ikako_url: str) -> Dict[str, str]:
    """
    Input: ikako/kakobuy URL
    Output keys match AdminApp expectations: kakobuy, picksly, title, price, img, category
    """
    final_url, kb_html = _fetch(ikako_url, HEADERS, timeout=18)

    source_url = _extract_source_url(final_url, kb_html)
    picksly = _build_picksly(source_url)

    result: Dict[str, str] = {
        "kakobuy": ikako_url,
        "picksly": picksly,
        "title": "",
        "price": "",
        "img": "",
        "category": "",
    }

    api = _kakobuy_api_lookup(source_url)
    for k, v in api.items():
        if v:
            result[k] = v

    if not result["title"]:
        t = _og(kb_html, "og:title")
        if t:
            result["title"] = t
    if not result["img"]:
        im = _og(kb_html, "og:image")
        if im:
            result["img"] = im.split("?")[0].split("!")[0]
    if not result["price"]:
        pr = _og(kb_html, "og:price:amount")
        if pr:
            result["price"] = pr

    if (not result["title"] or not result["img"] or not result["price"]) and picksly:
        qc = _picksly_fallback(picksly)
        for k in ("title", "img", "price"):
            if not result[k] and qc.get(k):
                result[k] = qc[k]

    if not result["img"]:
        m = CDN_RE.search(kb_html)
        if m:
            result["img"] = m.group(0).split("?")[0].split("!")[0]

    return result

