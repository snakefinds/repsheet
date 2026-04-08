import os
import re
import json
import threading
import subprocess
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk

from scraper import scrape_ikako, cache_image

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')

# Keys stored for each find (bulk JSON may include extra keys)
_BULK_ITEM_KEYS = ('title', 'price', 'img', 'kakobuy', 'category', 'picksly')

# During URL bulk import, write data.json every N items so a crash mid-run does not lose everything.
_BULK_FLUSH_EVERY = 8


def _relax_json_trailing_commas(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r',(\s*[}\]])', r'\1', s)
    return s


def _parse_bulk_json_structure(raw: str):
    """
    Parse pasted JSON for bulk import. Returns the decoded value, or None if
    every attempt failed.
    """
    s = raw.lstrip('\ufeff').strip()
    if not s:
        return None
    attempts = [s, _relax_json_trailing_commas(s)]
    if s.startswith('{') and not s.startswith('['):
        inner = s.rstrip().rstrip(',').strip()
        if inner.startswith('{'):
            wrapped = '[' + inner + ']'
            attempts.extend([wrapped, _relax_json_trailing_commas(wrapped)])
    for a in attempts:
        try:
            return json.loads(a)
        except json.JSONDecodeError:
            continue
    return None


def _looks_like_product_dict(d: dict) -> bool:
    for k in ('kakobuy', 'title', 'picksly', 'img', 'price', 'category'):
        v = d.get(k)
        if v is None:
            continue
        if isinstance(v, str):
            if v.strip():
                return True
        elif v:
            return True
    return False


def _extract_bulk_item_dicts(parsed) -> list[dict]:
    """Turn a JSON document into a flat list of product dicts."""
    if isinstance(parsed, list):
        out = []
        for el in parsed:
            if not isinstance(el, dict):
                continue
            if isinstance(el.get('items'), list):
                out.extend(d for d in el['items'] if isinstance(d, dict))
            elif _looks_like_product_dict(el):
                out.append(el)
        return out
    if isinstance(parsed, dict):
        if isinstance(parsed.get('items'), list):
            return [d for d in parsed['items'] if isinstance(d, dict)]
        if _looks_like_product_dict(parsed):
            return [parsed]
    return []


def _normalize_bulk_item(it: dict) -> dict:
    """Keep only site fields; coerce values to trimmed strings."""
    out = {}
    for k in _BULK_ITEM_KEYS:
        v = it.get(k, '')
        if v is None:
            out[k] = ''
        elif isinstance(v, (dict, list)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = str(v).strip()
    return out


# ── helpers ──────────────────────────────────────────────────────────────────

def _get_driver():
    """Create a headless Chrome driver, auto-downloading ChromeDriver if needed."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-blink-features=AutomationControlled')
    opts.add_argument('--window-size=1280,800')
    opts.add_argument('--lang=en-US')
    opts.add_experimental_option('excludeSwitches', ['enable-automation'])
    opts.add_experimental_option('useAutomationExtension', False)
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36')

    svc = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=svc, options=opts)


def scrape_url(url):
    """
    Use headless Chrome (Selenium) to fully render the Kakobuy product page,
    then extract title, price, image, and auto-build the picks.ly QC URL.
    """
    import time
    from urllib.parse import urlparse, parse_qs, unquote
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    CDN_RE = re.compile(
        r'https://[^\s"\'<>]*'
        r'(?:geilicdn\.com|alicdn\.com|weidianimg\.com|gw\.alicdn\.com|'
        r'img\.alicdn\.com|yupoo\.com|tbcdn\.cn|oss-cn)[^\s"\'<>]*'
        r'\.(?:jpg|jpeg|png|webp)',
        re.I)

    def og(prop, html):
        for pat in [
            r'<meta\b[^>]+\bproperty=["\']' + re.escape(prop) + r'["\'][^>]+\bcontent=["\']([^"\'<>]+)',
            r'<meta\b[^>]+\bcontent=["\']([^"\'<>]+)["\'][^>]+\bproperty=["\']' + re.escape(prop) + r'["\']',
        ]:
            m = re.search(pat, html, re.I | re.S)
            if m: return m.group(1).strip()
        return ''

    result = {'kakobuy': url, 'picksly': '', 'title': '', 'img': '', 'price': '', 'category': ''}

    driver = _get_driver()
    try:
        driver.get(url)

        # ── Get picks.ly QC URL from the redirected Kakobuy URL params ────────
        final_url  = driver.current_url
        parsed     = urlparse(final_url)
        qs         = parse_qs(parsed.query)
        source_url = unquote(qs.get('url', [''])[0])

        if source_url:
            if 'weidian.com' in source_url:
                m = re.search(r'itemID[=\s]*(\d+)', source_url)
                if m: result['picksly'] = f'https://picks.ly/item/WD{m.group(1)}'
            elif 'taobao.com' in source_url or 'tmall.com' in source_url:
                m = re.search(r'[?&]id=(\d+)', source_url)
                if m: result['picksly'] = f'https://picks.ly/item/TB{m.group(1)}'
            elif '1688.com' in source_url:
                m = re.search(r'/offer/(\d+)', source_url)
                if m: result['picksly'] = f'https://picks.ly/item/ALI{m.group(1)}'

        # ── Wait for the product to render ────────────────────────────────────
        wait = WebDriverWait(driver, 20)
        # Wait for h1 OR any price element OR main image — whichever comes first
        try:
            wait.until(EC.presence_of_element_located(
                (By.CSS_SELECTOR, 'h1, [class*="price"], [class*="title"], [class*="detail"]')))
        except Exception:
            time.sleep(8)  # Last resort: just wait

        html = driver.page_source

        # ── Title ─────────────────────────────────────────────────────────────
        # 1. Try h1 element text (most reliable after JS render)
        for sel in ['h1', '[class*="goods-title"]', '[class*="item-title"]',
                    '[class*="product-title"]', '[class*="detail-title"]']:
            try:
                el = driver.find_element(By.CSS_SELECTOR, sel)
                t = el.text.strip()
                if t and len(t) > 3 and 'kakobuy' not in t.lower():
                    result['title'] = t
                    break
            except Exception:
                continue

        # 2. og:title fallback (Nuxt may set this dynamically)
        if not result['title']:
            t = og('og:title', html)
            if t and 'kakobuy' not in t.lower() and 'taobao agent' not in t.lower():
                result['title'] = t

        # ── Image ─────────────────────────────────────────────────────────────
        # 1. og:image (set by Nuxt after load)
        img = og('og:image', html)
        if img and 'logo' not in img.lower() and 'kakobuy' not in img.lower():
            result['img'] = img.split('?')[0]

        # 2. CDN image URL from the rendered HTML
        if not result['img']:
            m = CDN_RE.search(html)
            if m:
                result['img'] = m.group(0).split('!')[0].split('?')[0]

        # 3. Try the main product img element src
        if not result['img']:
            for sel in ['[class*="cover"] img', '[class*="main-img"] img',
                        '[class*="gallery"] img', '[class*="product"] img']:
                try:
                    el = driver.find_element(By.CSS_SELECTOR, sel)
                    src = el.get_attribute('src') or ''
                    if src.startswith('http') and 'logo' not in src.lower():
                        result['img'] = src.split('?')[0]
                        break
                except Exception:
                    continue

        # ── Price ─────────────────────────────────────────────────────────────
        price = og('og:price:amount', html) or og('product:price:amount', html)
        if not price:
            for sel in ['[class*="price"]', '[class*="Price"]']:
                try:
                    els = driver.find_elements(By.CSS_SELECTOR, sel)
                    for el in els:
                        t = el.text.strip()
                        # Extract a number like 32.51 or 130
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
            # Last resort: regex scan the HTML for price patterns
            m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
            price = m.group(1) if m else ''

        if price:
            result['price'] = price

    finally:
        driver.quit()

    return result


# ── main app ─────────────────────────────────────────────────────────────────


class AdminApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SnakeFinds – Secure Desktop Admin")
        self.root.geometry("960x720")

        self.items = []
        self.theme = {}
        self.popup = {}
        self.landing = {}
        self.nav = []
        self.pages = {}
        self.weightEstimator = {}

        self.setup_ui()
        self.fetch_data()

    # ── UI setup ──────────────────────────────────────────────────────────────

    def setup_ui(self):
        top = tk.Frame(self.root, padx=10, pady=10)
        top.pack(fill=tk.X)
        tk.Label(top, text="🔒 Editing Securely Offline", font=("Arial", 10, "bold"), fg="green").pack(side=tk.LEFT, padx=10)
        tk.Button(top, text="Reload", command=self.fetch_data).pack(side=tk.LEFT)
        self.use_git_var = tk.BooleanVar(value=True)
        tk.Checkbutton(top, text="Auto-Push to GitHub (Vercel Deploy)", variable=self.use_git_var).pack(side=tk.RIGHT, padx=10)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        self._build_items_tab()
        self._build_bulk_tab()
        self._build_theme_tab()
        self._build_popup_tab()
        self._build_website_json_tab()
        self._build_weight_json_tab()

    # ── Items tab ─────────────────────────────────────────────────────────────

    def _build_items_tab(self):
        tab = tk.Frame(self.notebook)
        self.notebook.add(tab, text="Items")

        # Left — list
        left = tk.Frame(tab)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        self.item_listbox = tk.Listbox(left, width=32)
        self.item_listbox.pack(expand=True, fill=tk.Y)
        self.item_listbox.bind('<<ListboxSelect>>', self.on_item_select)
        bf = tk.Frame(left)
        bf.pack(fill=tk.X, pady=5)
        tk.Button(bf, text="New Item", command=self.new_item).pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(bf, text="Delete", command=self.delete_item).pack(side=tk.LEFT, expand=True, fill=tk.X)

        # Right — editor
        right = tk.Frame(tab, padx=10, pady=10)
        right.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)

        # ── Auto-fill row ──
        url_frame = tk.LabelFrame(right, text="  🔗 Paste Kakobuy / ikako link to auto-fill  ", padx=8, pady=6)
        url_frame.pack(fill=tk.X, pady=(0, 10))
        self.f_url = tk.StringVar()
        tk.Entry(url_frame, textvariable=self.f_url, font=("Arial", 11)).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))
        self.autofill_btn = tk.Button(url_frame, text="🔍 Fetch Info", command=self.autofill_from_url, bg="#e8f4fd")
        self.autofill_btn.pack(side=tk.LEFT)
        self.status_label = tk.Label(url_frame, text="", fg="gray", font=("Arial", 9))
        self.status_label.pack(side=tk.LEFT, padx=6)

        # ── Fields ──
        self.current_id = None
        self.f_title   = self.make_field("Title:",           right)
        self.f_cat     = self.make_field("Category:",        right)
        self.f_price   = self.make_field("Price ($):",       right)
        self.f_kakobuy = self.make_field("Kakobuy Link:",    right)
        self.f_picksly = self.make_field("Picksly QC Link:", right)
        self.f_img     = self.make_field("Image URL:",       right)

        tk.Button(right, text="Save Locally",      command=self.save_current_item, bg="lightblue").pack(pady=8)
        tk.Button(right, text="Sync Items to Web", command=self.sync_items, bg="lightgreen", font=("Arial", 10, "bold")).pack(pady=4)

    # ── Bulk Import tab ────────────────────────────────────────────────────────

    def _build_bulk_tab(self):
        tab = tk.Frame(self.notebook, padx=20, pady=20)
        self.notebook.add(tab, text="⚡ Bulk Import")

        tk.Label(tab, text="Paste one Kakobuy / ikako link per line — the app fetches everything for you.",
                 font=("Arial", 10), wraplength=600, justify="left").pack(anchor="w")
        tk.Label(
            tab,
            text="Or paste JSON: a single object, an array [ {...}, {...} ], or {\"items\": [...] } "
                 "(title, price, img, kakobuy, category, picksly). Trailing commas are tolerated.",
            font=("Arial", 9), fg="gray", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(2, 8))

        self.bulk_text = tk.Text(tab, height=14, font=("Courier", 10), wrap=tk.NONE)
        self.bulk_text.pack(expand=True, fill=tk.BOTH)

        self.bulk_headless_var = tk.BooleanVar(value=True)
        tk.Checkbutton(
            tab,
            text="Hide browser during URL import (recommended — less flashing; uses scraper headless mode)",
            variable=self.bulk_headless_var,
            font=("Arial", 9),
            anchor="w",
        ).pack(anchor="w", pady=(6, 0))
        tk.Label(
            tab,
            text="Each link can take ~30–90s (page load + image cache). Items appear in the Items tab as each "
                 "finishes — you do not need to wait for the whole list.",
            font=("Arial", 9),
            fg="gray",
            wraplength=600,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        self.bulk_progress = tk.Label(tab, text="", fg="gray", font=("Arial", 9))
        self.bulk_progress.pack(anchor="w", pady=(4, 0))

        bf = tk.Frame(tab)
        bf.pack(fill=tk.X, pady=8)
        tk.Button(bf, text="Clear",                command=self.bulk_clear).pack(side=tk.LEFT, padx=(0, 10))
        tk.Button(bf, text="⚡ Import & Sync",     command=self.bulk_import,
                  bg="lightgreen", font=("Arial", 10, "bold")).pack(side=tk.LEFT)

    # ── Theme tab ─────────────────────────────────────────────────────────────

    def _build_theme_tab(self):
        tab = tk.Frame(self.notebook, padx=20, pady=20)
        self.notebook.add(tab, text="Theme & Branding")
        self.t_sitename = self.make_field("Site Name:",             tab)
        self.t_tagline  = self.make_field("Tagline:",               tab)
        self.t_accent   = self.make_field("Accent Color (#hex):",   tab)
        self.t_bg       = self.make_field("Background Color (#hex):", tab)
        self.t_surface  = self.make_field("Surface Color (#hex):",  tab)
        tk.Button(tab, text="Sync Theme to Web", command=self.sync_theme,
                  bg="lightgreen", font=("Arial", 10, "bold")).pack(pady=20)

    # ── Popup tab ─────────────────────────────────────────────────────────────

    def _build_popup_tab(self):
        tab = tk.Frame(self.notebook, padx=20, pady=20)
        self.notebook.add(tab, text="Promo Popup")
        self.p_enabled = tk.BooleanVar()
        tk.Checkbutton(tab, text="Enable Promo Popup", variable=self.p_enabled).pack(anchor="w", pady=5)
        self.p_title = self.make_field("Top Title:",     tab)
        self.p_brand = self.make_field("Brand Name:",    tab)
        self.p_badge = self.make_field("Badge Text:",    tab)
        self.p_desc  = self.make_field("Description:",   tab)
        self.p_code  = self.make_field("Promo Code:",    tab)
        self.p_btn   = self.make_field("Button Text:",   tab)
        self.p_link  = self.make_field("Affiliate Link:", tab)
        tk.Button(tab, text="Sync Popup to Web", command=self.sync_popup,
                  bg="lightgreen", font=("Arial", 10, "bold")).pack(pady=20)

    # ── Website copy (JSON) tab ───────────────────────────────────────────────

    def _build_website_json_tab(self):
        tab = tk.Frame(self.notebook)
        self.notebook.add(tab, text="Website copy")

        wrap = tk.Frame(tab, padx=12, pady=10)
        wrap.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            wrap,
            text="Landing page (index.html): heroPart1, heroPart2, heroSub, CTAs, logoText, etc. — valid JSON object.",
            font=("Arial", 9), fg="gray", wraplength=880, justify="left",
        ).pack(anchor="w")
        self.txt_landing = tk.Text(wrap, height=10, font=("Courier", 9), wrap=tk.NONE)
        self.txt_landing.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        tk.Label(
            wrap,
            text="Navigation bar: JSON array like [ {\"label\": \"Home\", \"href\": \"index.html\"}, … ]",
            font=("Arial", 9), fg="gray", wraplength=880, justify="left",
        ).pack(anchor="w")
        self.txt_nav = tk.Text(wrap, height=7, font=("Courier", 9), wrap=tk.NONE)
        self.txt_nav.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        tk.Label(
            wrap,
            text="Other pages: footer, finds (catalog hero), howToBuy (guide: hero lines, steps[] with title, image, checklist/tips/bullets, cta, couponBox, finalCta). See how-to-buy.html + data.json pages.howToBuy.",
            font=("Arial", 9), fg="gray", wraplength=880, justify="left",
        ).pack(anchor="w")
        self.txt_pages = tk.Text(wrap, height=9, font=("Courier", 9), wrap=tk.NONE)
        self.txt_pages.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        tk.Button(
            wrap, text="Sync Website Copy to data.json", command=self.sync_website_json,
            bg="lightgreen", font=("Arial", 10, "bold"),
        ).pack(pady=8)

    # ── Weight estimator (JSON) tab ──────────────────────────────────────────

    def _build_weight_json_tab(self):
        tab = tk.Frame(self.notebook)
        self.notebook.add(tab, text="Weight estimator")

        wrap = tk.Frame(tab, padx=12, pady=10)
        wrap.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            wrap,
            text="Full weightEstimator object: UI strings, defaultPackagingId, packaging[], categories[] (each category: id, label, icon, color, items[{name, grams}]).",
            font=("Arial", 9), fg="gray", wraplength=880, justify="left",
        ).pack(anchor="w")
        self.txt_weight = tk.Text(wrap, height=22, font=("Courier", 9), wrap=tk.NONE)
        self.txt_weight.pack(fill=tk.BOTH, expand=True, pady=(2, 8))

        tk.Button(
            wrap, text="Sync Weight Estimator to data.json", command=self.sync_weight_json,
            bg="lightgreen", font=("Arial", 10, "bold"),
        ).pack(pady=6)

    # ── widget helpers ────────────────────────────────────────────────────────

    def make_field(self, label, parent):
        f = tk.Frame(parent, pady=3)
        f.pack(fill=tk.X)
        tk.Label(f, text=label, width=22, anchor="e").pack(side=tk.LEFT)
        var = tk.StringVar()
        tk.Entry(f, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10)
        return var

    # ── data I/O ──────────────────────────────────────────────────────────────

    def _read_data(self):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as fh:
                return json.load(fh)
        except Exception as e:
            messagebox.showerror("File Error", f"Could not read data.json\n{e}")
            return {"items": [], "theme": {}, "popup": {}, "landing": {}, "nav": [], "pages": {}, "weightEstimator": {}}

    def _write_data(self):
        try:
            payload = {
                "items": self.items,
                "theme": self.theme,
                "popup": self.popup,
                "landing": self.landing,
                "nav": self.nav,
                "pages": self.pages,
                "weightEstimator": self.weightEstimator,
            }
            with open(DATA_FILE, 'w', encoding='utf-8') as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
            return True
        except Exception as e:
            messagebox.showerror("File Error", f"Could not write data.json\n{e}")
            return False

    def fetch_data(self):
        d = self._read_data()
        self.items = d.get('items', [])
        self.theme = d.get('theme', {})
        self.popup = d.get('popup', {})
        self.landing = d.get('landing') or {}
        self.nav = d.get('nav') or []
        self.pages = d.get('pages') or {}
        self.weightEstimator = d.get('weightEstimator') or {}
        self.refresh_listbox()
        self.update_theme_inputs()
        self.update_popup_inputs()
        self.update_website_json_inputs()
        self.update_weight_json_inputs()

    def refresh_listbox(self):
        self.item_listbox.delete(0, tk.END)
        for it in self.items:
            self.item_listbox.insert(tk.END, it.get('title', 'Unnamed'))

    # ── single-item editor logic ───────────────────────────────────────────────

    def on_item_select(self, event):
        sel = self.item_listbox.curselection()
        if not sel:
            return
        it = self.items[sel[0]]
        self.current_id = it.get('id')
        self.f_url.set(it.get('kakobuy', ''))
        self.f_title.set(it.get('title', ''))
        self.f_cat.set(it.get('category', ''))
        self.f_price.set(str(it.get('price', '')))
        self.f_kakobuy.set(it.get('kakobuy', ''))
        self.f_picksly.set(it.get('picksly', ''))
        self.f_img.set(it.get('img', ''))

    def new_item(self):
        self.current_id = None
        self.f_url.set('')
        self.f_title.set('')
        self.f_cat.set('Shoes')
        self.f_price.set('0')
        self.f_kakobuy.set('https://')
        self.f_picksly.set('')
        self.f_img.set('')
        self.status_label.config(text='')

    def save_current_item(self):
        new_it = {
            "title":    self.f_title.get(),
            "category": self.f_cat.get(),
            "price":    self.f_price.get(),
            "kakobuy":  self.f_kakobuy.get(),
            "picksly":  self.f_picksly.get(),
            "img":      cache_image(self.f_img.get()),
        }
        if self.current_id is not None:
            for i, it in enumerate(self.items):
                if it.get('id') == self.current_id:
                    new_it['id'] = self.current_id
                    self.items[i] = new_it
                    break
        else:
            new_id = max([i.get('id', 0) for i in self.items] + [0]) + 1
            new_it['id'] = new_id
            self.current_id = new_id
            self.items.append(new_it)

        self.refresh_listbox()
        idx = next((i for i, it in enumerate(self.items) if it.get('id') == self.current_id), -1)
        if idx >= 0:
            self.item_listbox.selection_clear(0, tk.END)
            self.item_listbox.selection_set(idx)

        self._write_data()
        messagebox.showinfo("Saved", "Item saved locally. Click 'Sync Items to Web' to push to GitHub.")

    def delete_item(self):
        if self.current_id is not None:
            self.items = [i for i in self.items if i.get('id') != self.current_id]
            self.current_id = None
            self.refresh_listbox()
            self.new_item()
            self._write_data()

    # ── auto-fill from URL ────────────────────────────────────────────────────

    def autofill_from_url(self):
        url = self.f_url.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Paste a Kakobuy or ikako link first.")
            return
        self.status_label.config(text="⏳ Fetching…")
        self.autofill_btn.config(state=tk.DISABLED)
        threading.Thread(target=self._autofill_worker, args=(url,), daemon=True).start()

    def _autofill_worker(self, url):
        try:
            info = scrape_ikako(url)
            self.root.after(0, self._autofill_apply, info)
        except Exception as e:
            self.root.after(0, self._autofill_error, str(e))

    def _autofill_apply(self, info):
        self.autofill_btn.config(state=tk.NORMAL)
        if info.get('title'):
            self.f_title.set(info['title'])
        if info.get('img'):
            self.f_img.set(info['img'])
        if info.get('price'):
            self.f_price.set(info['price'])
        if info.get('picksly'):
            self.f_picksly.set(info['picksly'])
        self.f_kakobuy.set(info.get('kakobuy', self.f_url.get().strip()))
        fetched = []
        if info.get('title'): fetched.append('title')
        if info.get('img'):   fetched.append('img')
        if info.get('price'): fetched.append('price')
        if info.get('picksly'): fetched.append('QC link')
        self.status_label.config(
            text=f"✅ Got: {', '.join(fetched)}" if fetched else "⚠ Nothing found — fill manually",
            fg="green" if fetched else "orange"
        )

    def _autofill_error(self, msg):
        self.autofill_btn.config(state=tk.NORMAL)
        self.status_label.config(text="⚠ Fetch failed — fill manually", fg="red")
        messagebox.showwarning("Fetch Failed",
            f"Couldn't automatically pull data.\nFill in the fields manually.\n\nDetails: {msg}")

    # ── bulk import ───────────────────────────────────────────────────────────

    def bulk_clear(self):
        self.bulk_text.delete('1.0', tk.END)

    def bulk_import(self):
        raw = self.bulk_text.get('1.0', tk.END).strip()
        if not raw:
            messagebox.showwarning("Empty", "Paste some URLs or JSON first.")
            return

        first = raw.lstrip('\ufeff').lstrip()[:1]
        if first in '{[':
            parsed = _parse_bulk_json_structure(raw)
            if parsed is None:
                messagebox.showerror(
                    "Invalid JSON",
                    "Couldn't parse JSON. Check brackets/quotes, or use one URL per line.",
                )
                return
            items = _extract_bulk_item_dicts(parsed)
            if not items:
                messagebox.showerror(
                    "No items in JSON",
                    "Found no product objects. Include title/kakobuy/picksly (or use {\"items\": [...] }).",
                )
                return
            self._bulk_add_items(items)
            return

        # Otherwise treat as one URL per line
        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        if not urls:
            return
        if self.bulk_headless_var.get():
            os.environ["SNAKEFINDS_HEADLESS"] = "1"
        else:
            os.environ["SNAKEFINDS_HEADLESS"] = "0"
        self._bulk_next_id = max([i.get("id", 0) for i in self.items] + [0]) + 1
        self._bulk_error_count = 0
        self._bulk_import_total = len(urls)
        self.bulk_progress.config(text=f"0 / {len(urls)} — working…", fg="gray")
        self.notebook.tab(1, state=tk.DISABLED)
        threading.Thread(target=self._bulk_url_worker, args=(urls,), daemon=True).start()

    def _bulk_url_worker(self, urls):
        total = len(urls)
        for idx, url in enumerate(urls, 1):
            had_err = False
            try:
                raw = scrape_ikako(url)
            except Exception as e:
                raw = {
                    "title": url,
                    "kakobuy": url,
                    "img": "",
                    "price": "",
                    "picksly": "",
                    "category": "",
                    "error": str(e),
                }
                had_err = True
            d = dict(raw)
            d.pop("error", None)
            d.pop("final_url", None)
            it = _normalize_bulk_item(d)
            if it.get("img"):
                try:
                    it["img"] = cache_image(it["img"])
                except Exception:
                    pass
            self.root.after(0, self._bulk_append_one_item, it, idx, total, had_err)
        self.root.after(0, self._bulk_url_finished)

    def _bulk_append_one_item(self, it: dict, idx: int, total: int, had_error: bool):
        row = dict(it)
        row["id"] = self._bulk_next_id
        self._bulk_next_id += 1
        self.items.append(row)
        if had_error:
            self._bulk_error_count += 1
        self.refresh_listbox()
        self.bulk_progress.config(
            text=f"{idx} / {total} — saved to list (open Items tab to see)",
            fg="gray",
        )
        if idx % _BULK_FLUSH_EVERY == 0:
            self._write_data()

    def _bulk_url_finished(self):
        self.notebook.tab(1, state=tk.NORMAL)
        total = getattr(self, "_bulk_import_total", 0)
        errs = getattr(self, "_bulk_error_count", 0)
        ok = total - errs
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                msg = f"✅ {ok} item(s) imported and saved!"
                if errs:
                    msg += f"\n⚠ {errs} URL(s) couldn't be scraped fully — check those rows."
                messagebox.showinfo("Done", msg)
        self.bulk_progress.config(
            text=f"✅ {ok} / {total} done." + (f" ({errs} with errors)" if errs else ""),
            fg="green",
        )

    def _bulk_add_items(self, items):
        next_id = max([i.get('id', 0) for i in self.items] + [0]) + 1
        for it in items:
            if not isinstance(it, dict):
                continue
            it.pop('error', None)
            it.pop('final_url', None)
            it = _normalize_bulk_item(it)
            if it.get('img'):
                it['img'] = cache_image(it.get('img', ''))
            it['id'] = next_id
            next_id += 1
            self.items.append(it)
        self.refresh_listbox()
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Done", f"{len(items)} item(s) imported and saved!")

    # ── git ───────────────────────────────────────────────────────────────────

    def git_auto_push(self):
        if not self.use_git_var.get():
            return
        try:
            cwd = os.path.dirname(os.path.abspath(__file__))
            cf = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            subprocess.run(["git", "add", "-A"], check=True, cwd=cwd, creationflags=cf)
            res = subprocess.run(["git", "commit", "-m", "💻 Admin Panel Update: content synced"],
                                 cwd=cwd, capture_output=True, text=True, creationflags=cf)
            if "working tree clean" not in res.stdout and "nothing to commit" not in res.stdout:
                subprocess.run(["git", "pull", "--rebase"], check=True, cwd=cwd, creationflags=cf)
                subprocess.run(["git", "push"], check=True, cwd=cwd, creationflags=cf)
                messagebox.showinfo("Vercel Sync Success",
                    "Pushed to GitHub!\n\nVercel will deploy your changes automatically.")
            else:
                messagebox.showinfo("No Changes", "No new changes to push.")
        except Exception as e:
            messagebox.showwarning("Git Push Failed",
                f"Saved locally but push failed.\n\nDetails: {e}")

    # ── sync helpers ──────────────────────────────────────────────────────────

    def sync_items(self):
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Items saved to data.json!")

    def update_theme_inputs(self):
        t = self.theme
        self.t_sitename.set(t.get('siteName', ''))
        self.t_tagline.set(t.get('tagline', ''))
        self.t_accent.set(t.get('accent', ''))
        self.t_bg.set(t.get('bg', ''))
        self.t_surface.set(t.get('surface', ''))

    def sync_theme(self):
        self.theme = {
            "siteName": self.t_sitename.get(),
            "tagline":  self.t_tagline.get(),
            "accent":   self.t_accent.get(),
            "bg":       self.t_bg.get(),
            "surface":  self.t_surface.get(),
        }
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Theme saved!")

    def update_popup_inputs(self):
        p = self.popup
        self.p_enabled.set(p.get('enabled', False))
        self.p_title.set(p.get('title', ''))
        self.p_brand.set(p.get('brand', ''))
        self.p_badge.set(p.get('badge', ''))
        self.p_desc.set(p.get('desc', ''))
        self.p_code.set(p.get('code', ''))
        self.p_btn.set(p.get('btn_text', ''))
        self.p_link.set(p.get('link', ''))

    def sync_popup(self):
        self.popup = {
            "enabled":  self.p_enabled.get(),
            "title":    self.p_title.get(),
            "brand":    self.p_brand.get(),
            "badge":    self.p_badge.get(),
            "desc":     self.p_desc.get(),
            "code":     self.p_code.get(),
            "btn_text": self.p_btn.get(),
            "link":     self.p_link.get(),
        }
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Popup settings saved!")

    def update_website_json_inputs(self):
        self.txt_landing.delete('1.0', tk.END)
        self.txt_landing.insert('1.0', json.dumps(self.landing, indent=2, ensure_ascii=False))
        self.txt_nav.delete('1.0', tk.END)
        self.txt_nav.insert('1.0', json.dumps(self.nav, indent=2, ensure_ascii=False))
        self.txt_pages.delete('1.0', tk.END)
        self.txt_pages.insert('1.0', json.dumps(self.pages, indent=2, ensure_ascii=False))

    def update_weight_json_inputs(self):
        self.txt_weight.delete('1.0', tk.END)
        self.txt_weight.insert('1.0', json.dumps(self.weightEstimator, indent=2, ensure_ascii=False))

    def sync_website_json(self):
        raw_l = self.txt_landing.get('1.0', tk.END).strip()
        raw_n = self.txt_nav.get('1.0', tk.END).strip()
        raw_p = self.txt_pages.get('1.0', tk.END).strip()
        try:
            landing = json.loads(_relax_json_trailing_commas(raw_l)) if raw_l else {}
            if not isinstance(landing, dict):
                raise ValueError('First block must be a JSON object { } (landing page).')
            nav = json.loads(_relax_json_trailing_commas(raw_n)) if raw_n else []
            if not isinstance(nav, list):
                raise ValueError('Navigation must be a JSON array [ ].')
            pages = json.loads(_relax_json_trailing_commas(raw_p)) if raw_p else {}
            if not isinstance(pages, dict):
                raise ValueError('Pages block must be a JSON object { }.')
        except json.JSONDecodeError as e:
            messagebox.showerror("Invalid JSON", f"Check your syntax.\n{e}")
            return
        except ValueError as e:
            messagebox.showerror("Invalid data", str(e))
            return
        self.landing = landing
        self.nav = nav
        self.pages = pages
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Website copy saved to data.json!")

    def sync_weight_json(self):
        raw = self.txt_weight.get('1.0', tk.END).strip()
        try:
            we = json.loads(_relax_json_trailing_commas(raw)) if raw else {}
            if not isinstance(we, dict):
                raise ValueError("Must be a JSON object { }.")
        except json.JSONDecodeError as e:
            messagebox.showerror("Invalid JSON", f"Check your syntax.\n{e}")
            return
        except ValueError as e:
            messagebox.showerror("Invalid data", str(e))
            return
        self.weightEstimator = we
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Weight estimator saved to data.json!")


if __name__ == "__main__":
    root = tk.Tk()
    app = AdminApp(root)
    root.mainloop()
