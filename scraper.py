"""
scraper.py  –  SnakeFinds product scraper
Uses headless Chrome (Selenium) to fully render the Kakobuy product page,
then extracts title, price, image, and builds the picks.ly QC link automatically.
"""

import re
import json
import time
from urllib.parse import urlparse, parse_qs, unquote


# ── Chrome driver factory ─────────────────────────────────────────────────────

def _get_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--window-size=1280,900')
    opts.add_argument('--lang=en-US')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

    svc = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=svc, options=opts)


# ── Helpers ───────────────────────────────────────────────────────────────────

CDN_RE = re.compile(
    r'https://[^\s"\'<>]*'
    r'(?:geilicdn\.com|alicdn\.com|weidianimg\.com|gw\.alicdn\.com|'
    r'img\.alicdn\.com|yupoo\.com|tbcdn\.cn|oss-cn)[^\s"\'<>]*'
    r'\.(?:jpg|jpeg|png|webp)',
    re.I)


def _og(prop, html):
    """Extract an og: or product: meta tag from rendered HTML."""
    for pat in [
        r'<meta\b[^>]+\bproperty=["\']' + re.escape(prop) + r'["\'][^>]+\bcontent=["\']([^"\'<>]+)',
        r'<meta\b[^>]+\bcontent=["\']([^"\'<>]+)["\'][^>]+\bproperty=["\']' + re.escape(prop) + r'["\']',
    ]:
        m = re.search(pat, html, re.I | re.S)
        if m:
            return m.group(1).strip()
    return ''


def _build_picksly(source_url):
    """Derive the picks.ly QC URL from a Weidian / Taobao / 1688 source URL."""
    if not source_url:
        return ''
    if 'weidian.com' in source_url:
        m = re.search(r'itemID[=\s]*(\d+)', source_url)
        if m:
            return f'https://picks.ly/item/WD{m.group(1)}'
    elif 'taobao.com' in source_url or 'tmall.com' in source_url:
        m = re.search(r'[?&]id=(\d+)', source_url)
        if m:
            return f'https://picks.ly/item/TB{m.group(1)}'
    elif '1688.com' in source_url:
        m = re.search(r'/offer/(\d+)', source_url)
        if m:
            return f'https://picks.ly/item/ALI{m.group(1)}'
    return ''


# ── Main scraper ──────────────────────────────────────────────────────────────

def scrape_ikako(url):
    """
    Paste any ikako.vip / kakobuy link.
    Returns a dict: title, price, img, kakobuy, picksly, category
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    result = {
        'kakobuy':  url,
        'picksly':  '',
        'title':    '',
        'img':      '',
        'price':    '',
        'category': '',
    }

    driver = _get_driver()
    try:
        driver.get(url)

        # ── 1. Extract source URL for picks.ly QC link ────────────────────────
        final_url  = driver.current_url
        parsed     = urlparse(final_url)
        qs         = parse_qs(parsed.query)
        source_url = unquote(qs.get('url', [''])[0])

        result['picksly'] = _build_picksly(source_url)

        # ── 2. Wait for the product page to fully render ──────────────────────
        wait = WebDriverWait(driver, 20)
        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR,
                 'h1, [class*="price"], [class*="title"], [class*="detail"], [class*="goods"]')))
        except Exception:
            time.sleep(8)

        html = driver.page_source

        # ── 3. Title ──────────────────────────────────────────────────────────
        for sel in [
            'h1',
            '[class*="goods-title"]', '[class*="item-title"]',
            '[class*="product-title"]', '[class*="detail-title"]',
            '[class*="commodity-title"]', '[class*="sku-title"]',
        ]:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                t  = el.text.strip()
                if t and len(t) > 3 and 'kakobuy' not in t.lower():
                    result['title'] = t
                    break
            except Exception:
                continue

        if not result['title']:
            t = _og('og:title', html)
            if t and 'kakobuy' not in t.lower() and 'taobao agent' not in t.lower():
                result['title'] = t

        # ── 4. Image ──────────────────────────────────────────────────────────
        img = _og('og:image', html)
        if img and 'logo' not in img.lower():
            result['img'] = img.split('?')[0]

        if not result['img']:
            m = CDN_RE.search(html)
            if m:
                result['img'] = m.group(0).split('!')[0].split('?')[0]

        if not result['img']:
            for sel in [
                '[class*="cover"] img', '[class*="main-img"] img',
                '[class*="gallery"] img', '[class*="preview"] img',
                '[class*="goods"] img', '[class*="product"] img',
            ]:
                try:
                    el  = driver.find_element(By.CSS_SELECTOR, sel)
                    src = el.get_attribute('src') or el.get_attribute('data-src') or ''
                    if src.startswith('http') and 'logo' not in src.lower():
                        result['img'] = src.split('?')[0]
                        break
                except Exception:
                    continue

        # ── 5. Price ──────────────────────────────────────────────────────────
        price = _og('og:price:amount', html) or _og('product:price:amount', html)

        if not price:
            for sel in ['[class*="price"]', '[class*="Price"]',
                        '[class*="amount"]', '[class*="cost"]']:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        t = el.text.strip()
                        m = re.search(r'[\d]+\.?\d*', t.replace(',', ''))
                        if m:
                            val = float(m.group())
                            if 0.5 < val < 99999:
                                price = m.group()
                                break
                    if price:
                        break
                except Exception:
                    continue

        if not price:
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
            price = m.group(1) if m else ''

        if price:
            result['price'] = price

    finally:
        driver.quit()

    return result
