"""
Debug script - run this to see exactly what AdminApp's scraper is fetching.
Usage: python debug_scraper.py https://ikako.vip/YOUR_LINK
"""
import sys
import re
import json
import urllib.request
from urllib.parse import urlparse, parse_qs, unquote

URL = sys.argv[1] if len(sys.argv) > 1 else input("Paste ikako link: ").strip()

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Referer': 'https://www.kakobuy.com/',
}
MOBILE_HEADERS = {**HEADERS,
    'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
                  'AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
}

def fetch(u, mobile=False, timeout=14):
    h = MOBILE_HEADERS if mobile else HEADERS
    req = urllib.request.Request(u, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.url, resp.read().decode('utf-8', errors='ignore')

def og(prop, html):
    for pat in [
        r'<meta\b[^>]+\bproperty=["\']' + re.escape(prop) + r'["\'][^>]+\bcontent=["\']([^"\']+)',
        r'<meta\b[^>]+\bcontent=["\']([^"\']+)["\'][^>]+\bproperty=["\']' + re.escape(prop) + r'["\']',
    ]:
        m = re.search(pat, html, re.I | re.S)
        if m: return m.group(1).strip()
    return ''

sep = lambda: print("-" * 60)

print("\n" + "="*60)
print("SNAKEFINDS SCRAPER DEBUG")
print("="*60)

# Step 1: Follow redirect
print(f"\n[1] Fetching: {URL}")
try:
    final_url, kb_html = fetch(URL)
    print(f"    → Redirected to: {final_url}")
    print(f"    → HTML size: {len(kb_html):,} bytes")
except Exception as e:
    print(f"    ✗ FAILED: {e}")
    sys.exit(1)

# Step 2: Extract source URL
parsed = urlparse(final_url)
qs = parse_qs(parsed.query)
source_url = unquote(qs.get('url', [''])[0])
sep()
print(f"[2] Source marketplace URL from ?url= param:")
print(f"    {source_url or '(none found in URL params)'}")

if not source_url:
    m = re.search(r'["\']url["\']\s*:\s*["\'](https?://(?:weidian|taobao|1688|detail\.tmall)[^"\']+)', kb_html)
    if m:
        source_url = unquote(m.group(1))
        print(f"    Found in HTML: {source_url}")

# Step 3: Picks.ly URL
sep()
print("[3] Picks.ly URL build:")
picksly = ''
if source_url:
    if 'weidian.com' in source_url:
        m = re.search(r'itemID[=\s]*(\d+)', source_url)
        if m: picksly = f'https://picks.ly/item/WD{m.group(1)}'
    elif 'taobao.com' in source_url or 'tmall.com' in source_url:
        m = re.search(r'[?&]id=(\d+)', source_url)
        if m: picksly = f'https://picks.ly/item/TB{m.group(1)}'
    elif '1688.com' in source_url:
        m = re.search(r'/offer/(\d+)', source_url)
        if m: picksly = f'https://picks.ly/item/ALI{m.group(1)}'
print(f"    {picksly or '(none)'}")

# Step 4: Kakobuy page og: tags
sep()
print("[4] Kakobuy page og: tags:")
print(f"    og:title  = {og('og:title', kb_html) or '(empty)'}")
print(f"    og:image  = {og('og:image', kb_html) or '(empty)'}")
print(f"    og:price  = {og('og:price:amount', kb_html) or '(empty)'}")
page_title_m = re.search(r'<title>(.*?)</title>', kb_html, re.S)
page_title = re.sub(r'<[^>]+>', '', page_title_m.group(1)).strip() if page_title_m else ''
print(f"    <title>   = {page_title[:80] or '(empty)'}")

# Step 5: __NUXT_DATA__
sep()
print("[5] __NUXT_DATA__ check:")
nuxt_m = re.search(r'<script[^>]+id=["\']__NUXT_DATA__["\'][^>]*>(.*?)</script>', kb_html, re.S)
if nuxt_m:
    raw = nuxt_m.group(1)
    print(f"    Found! Size: {len(raw):,} bytes")
    try:
        nuxt = json.loads(raw)
        # Save full data to file for inspection
        with open('nuxt_data_dump.json', 'w', encoding='utf-8') as f:
            json.dump(nuxt, f, indent=2, ensure_ascii=False)
        print(f"    Saved full __NUXT_DATA__ to: nuxt_data_dump.json")
        
        # Scan for useful strings
        def flatten(obj, acc=None):
            if acc is None: acc = []
            if isinstance(obj, str): acc.append(obj)
            elif isinstance(obj, (list, tuple)):
                for v in obj: flatten(v, acc)
            elif isinstance(obj, dict):
                for v in obj.values(): flatten(v, acc)
            return acc
        
        strings = flatten(nuxt)
        CDN = re.compile(r'https://[^\s"\'<>]*(?:geilicdn\.com|alicdn\.com|weidianimg\.com|gw\.alicdn\.com|img\.alicdn\.com|yupoo\.com)[^\s"\'<>]*\.(?:jpg|jpeg|png|webp)', re.I)
        imgs = [s for s in strings if CDN.search(s)]
        prices = [s for s in strings if re.fullmatch(r'\d{1,5}(?:\.\d{1,2})?', str(s)) and 0.5 < float(s) < 9999]
        
        print(f"    String values found:  {len(strings)}")
        print(f"    CDN image URLs found: {len(imgs)}")
        if imgs: print(f"    First image: {imgs[0][:100]}")
        print(f"    Price candidates: {prices[:5]}")
    except Exception as e:
        print(f"    Failed to parse JSON: {e}")
        print(f"    First 500 chars: {raw[:500]}")
else:
    print("    ✗ __NUXT_DATA__ script tag NOT found")
    # Look for other data patterns
    m = re.search(r'window\.__(?:NUXT|INITIAL_STATE|STATE)__\s*=\s*(\{.*?\})\s*;', kb_html, re.S)
    if m:
        print(f"    Found window state: {m.group(1)[:200]}")
    else:
        print("    No window state patterns found either")

# Step 6: Try source URL (desktop + mobile)
sep()
if source_url:
    for label, hdrs in [("desktop", False), ("mobile", True)]:
        print(f"[6] Fetching source page ({label}): {source_url[:80]}")
        try:
            _, src_html = fetch(source_url, mobile=hdrs, timeout=12)
            print(f"    HTML size: {len(src_html):,} bytes")
            print(f"    og:title  = {og('og:title', src_html)[:80] or '(empty)'}")
            print(f"    og:image  = {og('og:image', src_html)[:80] or '(empty)'}")
            print(f"    og:price  = {og('og:price:amount', src_html) or '(empty)'}")
            t_m = re.search(r'<title>(.*?)</title>', src_html, re.S)
            if t_m: print(f"    <title>   = {re.sub(chr(60)+r'[^>]+>','',t_m.group(1)).strip()[:80]}")
            # Save for inspection
            fname = f'source_page_{label}.html'
            with open(fname, 'w', encoding='utf-8') as f: f.write(src_html)
            print(f"    Saved raw HTML to: {fname}")
        except Exception as e:
            print(f"    ✗ FAILED: {e}")

sep()
print("\nDEBUG COMPLETE. Check the saved files above for inspection.")
print("Share this output so the right parser can be written!\n")
