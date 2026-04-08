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

# UI (matches site catalog categories in finds.html)
_UI_FONT = ("Segoe UI", 10) if os.name == "nt" else ("Helvetica", 11)
_UI_FONT_SM = ("Segoe UI", 9) if os.name == "nt" else ("Helvetica", 10)
_UI_FONT_TT = ("Segoe UI", 11, "bold") if os.name == "nt" else ("Helvetica", 11, "bold")
_UI_MONO = ("Consolas", 10) if os.name == "nt" else ("Courier", 10)
CATEGORIES = (
    "Shoes",
    "T-Shirts",
    "Pants",
    "Jackets/Coats",
    "Sweaters/Hoodies",
    "Bags",
    "Hats/Caps",
    "Accessories",
    "Other",
)
# Primary actions (darker than site accent for contrast on buttons)
_BTN_GO = "#6a9a38"
_BTN_GO_ACTIVE = "#5d8630"


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
        self.root.title("SnakeFinds — Admin")
        self.root.geometry("1040x760")
        self.root.minsize(900, 640)

        self._filter_after_id = None
        self._filtered_indices = []

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

    def setup_styles(self):
        st = ttk.Style()
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", font=_UI_FONT)
        st.configure("TNotebook.Tab", padding=(12, 5))
        st.configure("TLabelFrame.Label", font=_UI_FONT_SM)
        st.configure("Dim.TLabel", font=_UI_FONT_SM, foreground="#5a5a5a")
        st.configure("Head.TLabel", font=_UI_FONT_TT, foreground="#222")

    def setup_ui(self):
        self.setup_styles()

        top = ttk.Frame(self.root, padding=(12, 10))
        top.pack(fill=tk.X)
        ttk.Label(top, text="SnakeFinds Admin", style="Head.TLabel").pack(side=tk.LEFT)
        ttk.Label(top, text="  ·  edits data.json on disk", style="Dim.TLabel").pack(side=tk.LEFT)
        ttk.Button(top, text="Reload", command=self.fetch_data).pack(side=tk.LEFT, padx=(14, 0))
        self.use_git_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Auto-push to GitHub after sync", variable=self.use_git_var).pack(side=tk.RIGHT)

        self.notebook = ttk.Notebook(self.root, padding=(0, 4))
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=(0, 4))

        foot = ttk.Frame(self.root, padding=(12, 6))
        foot.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar(value="")
        ttk.Label(foot, textvariable=self.status_var, style="Dim.TLabel").pack(side=tk.LEFT)

        self._build_items_tab()
        self._build_bulk_tab()
        self._build_theme_tab()
        self._build_popup_tab()
        self._build_website_json_tab()
        self._build_weight_json_tab()

        self.root.bind("<Control-s>", self._on_ctrl_s)
        self.root.bind("<Control-n>", self._on_ctrl_n)
        self.root.bind("<Control-Shift-T>", self._on_ctrl_shift_t)
        self.root.bind("<Control-Shift-t>", self._on_ctrl_shift_t)

    def _set_status(self, text: str):
        if getattr(self, "status_var", None) is not None:
            self.status_var.set(text)

    def _on_ctrl_s(self, _event=None):
        self.save_current_item()
        return "break"

    def _on_ctrl_n(self, _event=None):
        self.save_and_next_item()
        return "break"

    def _on_ctrl_shift_t(self, _event=None):
        self.title_capitalize_words()
        return "break"

    def title_capitalize_words(self):
        """Uppercase first letter of each whitespace-separated word in the title field."""
        raw = self.f_title.get()
        if not raw.strip():
            self._set_status("Title is empty — nothing to change")
            return
        parts = []
        for w in raw.split():
            if len(w) == 1:
                parts.append(w.upper())
            else:
                parts.append(w[0].upper() + w[1:].lower())
        self.f_title.set(" ".join(parts))
        self._set_status("Title: capitalised first letter of each word")

    # ── Items tab ─────────────────────────────────────────────────────────────

    def _build_items_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Items")

        paned = ttk.Panedwindow(tab, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        left = ttk.Frame(paned, padding=(0, 0, 10, 0))
        paned.add(left, weight=1)

        ttk.Label(left, text="Search (title, category, links…)", style="Dim.TLabel").pack(anchor="w")
        self.item_search_var = tk.StringVar()
        ent_search = ttk.Entry(left, textvariable=self.item_search_var)
        ent_search.pack(fill=tk.X, pady=(2, 6))
        self.item_search_var.trace_add("write", lambda *_: self._schedule_item_filter_update())

        lf = ttk.Frame(left)
        lf.pack(fill=tk.BOTH, expand=True)
        yscroll = ttk.Scrollbar(lf, orient=tk.VERTICAL)
        self.item_listbox = tk.Listbox(
            lf,
            width=36,
            font=_UI_FONT,
            yscrollcommand=yscroll.set,
            activestyle="none",
            borderwidth=1,
            relief=tk.SOLID,
            highlightthickness=0,
            selectbackground="#cfe9b5",
            selectforeground="#000000",
            exportselection=False,
        )
        yscroll.config(command=self.item_listbox.yview)
        yscroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.item_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.item_listbox.bind("<<ListboxSelect>>", self.on_item_select)

        bf = ttk.Frame(left)
        bf.pack(fill=tk.X, pady=(8, 0))
        ttk.Button(bf, text="New item", command=self.new_item).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(bf, text="Delete", command=self.delete_item).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 4))
        ttk.Button(bf, text="Delete all", command=self.delete_all_items).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        right = ttk.Frame(paned, padding=(4, 0, 0, 0))
        paned.add(right, weight=3)

        url_frame = ttk.LabelFrame(right, text="Paste Kakobuy / ikako link", padding=(8, 6))
        url_frame.pack(fill=tk.X, pady=(0, 8))
        row_u = ttk.Frame(url_frame)
        row_u.pack(fill=tk.X)
        self.f_url = tk.StringVar()
        ttk.Entry(row_u, textvariable=self.f_url, font=_UI_FONT).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 8))
        self.autofill_btn = tk.Button(
            row_u,
            text="Fetch",
            command=self.autofill_from_url,
            font=_UI_FONT_SM,
            bg="#e8eef5",
            relief=tk.FLAT,
            padx=12,
            pady=4,
            cursor="hand2",
        )
        self.autofill_btn.pack(side=tk.LEFT)
        self.status_label = tk.Label(url_frame, text="", font=_UI_FONT_SM, fg="#666666", anchor="w")
        self.status_label.pack(anchor="w", pady=(6, 0))

        self.current_id = None
        fields = ttk.Frame(right)
        fields.pack(fill=tk.BOTH, expand=True)
        title_row = ttk.Frame(fields)
        title_row.pack(fill=tk.X, pady=3)
        ttk.Label(title_row, text="Title", width=18, anchor="w").pack(side=tk.LEFT, padx=(0, 8))
        self.f_title = tk.StringVar()
        ttk.Entry(title_row, textvariable=self.f_title).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 6))
        ttk.Button(title_row, text="Capitalise", command=self.title_capitalize_words).pack(side=tk.LEFT)
        self.f_cat = self.make_category_field("Category", fields)
        self.f_price = self.make_field("Price", fields)
        self.f_kakobuy = self.make_field("Kakobuy link", fields)
        self.f_picksly = self.make_field("Picksly QC", fields)
        self.f_img = self.make_field("Image (URL or path)", fields)

        hint = ttk.Label(
            right,
            text="Shortcuts: Ctrl+S save  ·  Ctrl+N save & next  ·  Ctrl+Shift+T capitalise title words",
            style="Dim.TLabel",
        )
        hint.pack(anchor="w", pady=(4, 0))

        btn_row = ttk.Frame(right)
        btn_row.pack(fill=tk.X, pady=(12, 0))
        tk.Button(
            btn_row,
            text="Save",
            command=self.save_current_item,
            font=_UI_FONT,
            bg=_BTN_GO,
            fg="white",
            activebackground=_BTN_GO_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            padx=16,
            pady=6,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            btn_row,
            text="Save & next",
            command=self.save_and_next_item,
            font=_UI_FONT,
            bg="#5b6b7a",
            fg="white",
            activebackground="#4a5866",
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side=tk.LEFT, padx=(0, 6))
        tk.Button(
            btn_row,
            text="Sync items to web",
            command=self.sync_items,
            font=_UI_FONT,
            bg="#3d6b8a",
            fg="white",
            activebackground="#325a75",
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side=tk.LEFT)

    # ── Bulk Import tab ────────────────────────────────────────────────────────

    def _build_bulk_tab(self):
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Bulk import")

        ttk.Label(
            tab,
            text="One Kakobuy / ikako URL per line — the app fills title, price, image, and QC link.",
            wraplength=720,
        ).pack(anchor="w")
        ttk.Label(
            tab,
            text="Or paste JSON: one object, an array, or {\"items\": [...] } with title, kakobuy, picksly, category, etc.",
            style="Dim.TLabel",
            wraplength=720,
        ).pack(anchor="w", pady=(4, 8))

        self.bulk_text = tk.Text(tab, height=14, font=_UI_MONO, wrap=tk.NONE, relief=tk.SOLID, borderwidth=1)
        self.bulk_text.pack(expand=True, fill=tk.BOTH)

        self.bulk_headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            tab,
            text="Headless browser during URL import (less window flashing)",
            variable=self.bulk_headless_var,
        ).pack(anchor="w", pady=(8, 0))
        ttk.Label(
            tab,
            text="Each URL may take ~30–90s. Rows appear in Items as they finish.",
            style="Dim.TLabel",
            wraplength=720,
        ).pack(anchor="w", pady=(2, 0))

        self.bulk_progress = tk.Label(tab, text="", font=_UI_FONT_SM, fg="#666666", anchor="w")
        self.bulk_progress.pack(anchor="w", pady=(6, 0))

        bf = ttk.Frame(tab)
        bf.pack(fill=tk.X, pady=10)
        ttk.Button(bf, text="Clear", command=self.bulk_clear).pack(side=tk.LEFT, padx=(0, 8))
        tk.Button(
            bf,
            text="Import (then sync if enabled)",
            command=self.bulk_import,
            font=_UI_FONT,
            bg=_BTN_GO,
            fg="white",
            activebackground=_BTN_GO_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side=tk.LEFT)

    # ── Theme tab ─────────────────────────────────────────────────────────────

    def _build_theme_tab(self):
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Theme")
        self.t_sitename = self.make_field("Site name", tab)
        self.t_tagline = self.make_field("Tagline", tab)
        self.t_accent = self.make_field("Accent (#hex)", tab)
        self.t_bg = self.make_field("Background (#hex)", tab)
        self.t_surface = self.make_field("Surface (#hex)", tab)
        tk.Button(
            tab,
            text="Sync theme to web",
            command=self.sync_theme,
            font=_UI_FONT,
            bg=_BTN_GO,
            fg="white",
            activebackground=_BTN_GO_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(anchor="w", pady=(16, 0))

    # ── Popup tab ─────────────────────────────────────────────────────────────

    def _build_popup_tab(self):
        tab = ttk.Frame(self.notebook, padding=16)
        self.notebook.add(tab, text="Promo popup")
        self.p_enabled = tk.BooleanVar()
        ttk.Checkbutton(tab, text="Enable promo popup on site", variable=self.p_enabled).pack(anchor="w", pady=(0, 8))
        self.p_title = self.make_field("Title", tab)
        self.p_brand = self.make_field("Brand name", tab)
        self.p_badge = self.make_field("Badge", tab)
        self.p_desc = self.make_field("Description", tab)
        self.p_code = self.make_field("Promo code", tab)
        self.p_btn = self.make_field("Button text", tab)
        self.p_link = self.make_field("Affiliate link", tab)
        tk.Button(
            tab,
            text="Sync popup to web",
            command=self.sync_popup,
            font=_UI_FONT,
            bg=_BTN_GO,
            fg="white",
            activebackground=_BTN_GO_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(anchor="w", pady=(16, 0))

    # ── Website copy (JSON) tab ───────────────────────────────────────────────

    def _build_website_json_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Website copy")

        wrap = ttk.Frame(tab, padding=12)
        wrap.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            wrap,
            text="Landing (index.html): JSON object — heroPart1, heroPart2, heroSub, CTAs, logoText, …",
            style="Dim.TLabel",
            wraplength=880,
        ).pack(anchor="w")
        self.txt_landing = tk.Text(wrap, height=10, font=_UI_MONO, wrap=tk.NONE, relief=tk.SOLID, borderwidth=1)
        self.txt_landing.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        ttk.Label(
            wrap,
            text="Nav bar: JSON array e.g. [ {\"label\": \"Home\", \"href\": \"index.html\"}, … ]",
            style="Dim.TLabel",
            wraplength=880,
        ).pack(anchor="w")
        self.txt_nav = tk.Text(wrap, height=7, font=_UI_MONO, wrap=tk.NONE, relief=tk.SOLID, borderwidth=1)
        self.txt_nav.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        ttk.Label(
            wrap,
            text="Pages: footer, finds hero, howToBuy (see how-to-buy.html + pages.howToBuy in data.json).",
            style="Dim.TLabel",
            wraplength=880,
        ).pack(anchor="w")
        self.txt_pages = tk.Text(wrap, height=9, font=_UI_MONO, wrap=tk.NONE, relief=tk.SOLID, borderwidth=1)
        self.txt_pages.pack(fill=tk.BOTH, expand=True, pady=(2, 10))

        tk.Button(
            wrap,
            text="Apply website copy to data.json",
            command=self.sync_website_json,
            font=_UI_FONT,
            bg=_BTN_GO,
            fg="white",
            activebackground=_BTN_GO_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(anchor="w", pady=8)

    # ── Weight estimator (JSON) tab ──────────────────────────────────────────

    def _build_weight_json_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Weight estimator")

        wrap = ttk.Frame(tab, padding=12)
        wrap.pack(fill=tk.BOTH, expand=True)

        ttk.Label(
            wrap,
            text="weightEstimator JSON: packaging[], categories[] with items {name, grams}, UI strings, defaultPackagingId.",
            style="Dim.TLabel",
            wraplength=880,
        ).pack(anchor="w")
        self.txt_weight = tk.Text(wrap, height=22, font=_UI_MONO, wrap=tk.NONE, relief=tk.SOLID, borderwidth=1)
        self.txt_weight.pack(fill=tk.BOTH, expand=True, pady=(2, 8))

        tk.Button(
            wrap,
            text="Apply weight estimator to data.json",
            command=self.sync_weight_json,
            font=_UI_FONT,
            bg=_BTN_GO,
            fg="white",
            activebackground=_BTN_GO_ACTIVE,
            activeforeground="white",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(anchor="w", pady=6)

    # ── widget helpers ────────────────────────────────────────────────────────

    def make_field(self, label, parent):
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, pady=3)
        ttk.Label(f, text=label, width=18, anchor="w").pack(side=tk.LEFT, padx=(0, 8))
        var = tk.StringVar()
        ttk.Entry(f, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X)
        return var

    def make_category_field(self, label, parent):
        f = ttk.Frame(parent)
        f.pack(fill=tk.X, pady=3)
        ttk.Label(f, text=label, width=18, anchor="w").pack(side=tk.LEFT, padx=(0, 8))
        var = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=var, values=CATEGORIES, width=40)
        cb.pack(side=tk.LEFT, expand=True, fill=tk.X)
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
        self._set_status(f"Loaded {len(self.items)} items · Ctrl+S save · Ctrl+N save & next")

    def _schedule_item_filter_update(self):
        if self._filter_after_id is not None:
            self.root.after_cancel(self._filter_after_id)
        self._filter_after_id = self.root.after(120, self._apply_item_filter)

    def _apply_item_filter(self):
        self._filter_after_id = None
        self._refresh_item_list()

    def refresh_listbox(self):
        self._refresh_item_list()

    def _refresh_item_list(self):
        if not getattr(self, "item_listbox", None):
            return
        keep_id = self.current_id
        q = ""
        if getattr(self, "item_search_var", None) is not None:
            q = self.item_search_var.get().strip().lower()
        self._filtered_indices = []
        for i, it in enumerate(self.items):
            if not q:
                self._filtered_indices.append(i)
                continue
            blob = " ".join(
                str(it.get(k, "")) for k in ("title", "category", "price", "kakobuy", "picksly", "img")
            ).lower()
            if q in blob:
                self._filtered_indices.append(i)

        self.item_listbox.delete(0, tk.END)
        for i in self._filtered_indices:
            it = self.items[i]
            title = str(it.get("title", "Unnamed"))
            if len(title) > 50:
                title = title[:47] + "…"
            cat = (it.get("category") or "").strip()
            line = f"{title}  ·  {cat}" if cat else title
            self.item_listbox.insert(tk.END, line)

        n = len(self.items)
        m = len(self._filtered_indices)
        self._set_status(f"{n} items · {m} shown" + (f' · filter: "{q}"' if q else ""))

        if keep_id is not None:
            for pos, idx in enumerate(self._filtered_indices):
                if self.items[idx].get("id") == keep_id:
                    self.item_listbox.selection_clear(0, tk.END)
                    self.item_listbox.selection_set(pos)
                    self.item_listbox.see(pos)
                    break

    # ── single-item editor logic ───────────────────────────────────────────────

    def on_item_select(self, event):
        sel = self.item_listbox.curselection()
        if not sel:
            return
        pos = sel[0]
        if pos < 0 or pos >= len(self._filtered_indices):
            return
        it = self.items[self._filtered_indices[pos]]
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
        if getattr(self, "item_listbox", None):
            self.item_listbox.selection_clear(0, tk.END)
        self.f_url.set("")
        self.f_title.set("")
        self.f_cat.set("Shoes")
        self.f_price.set("0")
        self.f_kakobuy.set("https://")
        self.f_picksly.set("")
        self.f_img.set("")
        self.status_label.config(text="", fg="#666666")
        self._set_status("New item — fill fields, then Save")

    def _persist_current_item(self) -> bool:
        new_it = {
            "title": self.f_title.get().strip(),
            "category": self.f_cat.get().strip(),
            "price": self.f_price.get().strip(),
            "kakobuy": self.f_kakobuy.get().strip(),
            "picksly": self.f_picksly.get().strip(),
            "img": cache_image(self.f_img.get().strip()),
        }
        if self.current_id is not None:
            for i, it in enumerate(self.items):
                if it.get("id") == self.current_id:
                    new_it["id"] = self.current_id
                    self.items[i] = new_it
                    break
        else:
            new_id = max([i.get("id", 0) for i in self.items] + [0]) + 1
            new_it["id"] = new_id
            self.current_id = new_id
            self.items.append(new_it)

        if not self._write_data():
            return False
        self._refresh_item_list()
        return True

    def save_current_item(self):
        if not self._persist_current_item():
            return
        self._set_status("Saved to data.json — use “Sync items to web” when you want GitHub/Vercel updated.")

    def save_and_next_item(self):
        if not self._persist_current_item():
            return
        try:
            pos = next(
                i
                for i, idx in enumerate(self._filtered_indices)
                if self.items[idx].get("id") == self.current_id
            )
        except StopIteration:
            pos = -1
        next_pos = pos + 1
        if next_pos < self.item_listbox.size():
            self.item_listbox.selection_clear(0, tk.END)
            self.item_listbox.selection_set(next_pos)
            self.item_listbox.see(next_pos)
            self.on_item_select(None)
            self._set_status("Saved — next item")
        else:
            self._set_status("Saved — at end of list (or filter)")

    def delete_item(self):
        if self.current_id is not None:
            self.items = [i for i in self.items if i.get('id') != self.current_id]
            self.current_id = None
            self.refresh_listbox()
            self.new_item()
            self._write_data()

    def delete_all_items(self):
        count = len(self.items)
        if count == 0:
            self._set_status("No items to delete")
            return
        ok = messagebox.askyesno(
            "Delete all items?",
            f"This will permanently remove all {count} item(s) from data.json.\n\nContinue?",
        )
        if not ok:
            self._set_status("Delete all cancelled")
            return
        self.items = []
        self.current_id = None
        self.refresh_listbox()
        self.new_item()
        if self._write_data():
            self._set_status(f"Deleted all {count} item(s)")

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
        cwd = os.path.dirname(os.path.abspath(__file__))
        cf_local = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        # New console + inherited stdio: piped stdout/stderr breaks Git Credential Manager
        # when pull/push run from Thonny/tkinter. A real console lets sign-in / errors show.
        cf_net = subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0

        def _run_local(args, check=True):
            return subprocess.run(
                args,
                check=check,
                cwd=cwd,
                capture_output=True,
                text=True,
                creationflags=cf_local,
            )

        def _run_net_inherit_stdio(args):
            return subprocess.run(args, cwd=cwd, creationflags=cf_net)

        def _git_captured_output(args):
            r = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                creationflags=0,
            )
            parts = []
            if r.stdout and r.stdout.strip():
                parts.append(r.stdout.strip())
            if r.stderr and r.stderr.strip():
                parts.append(r.stderr.strip())
            return "\n\n".join(parts) if parts else f"(exit {r.returncode}, no output)"

        try:
            r_pull = _run_net_inherit_stdio(["git", "pull", "--rebase", "--autostash"])
            if r_pull.returncode != 0:
                messagebox.showwarning(
                    "Git Sync Failed",
                    "git pull --rebase failed.\n\n"
                    + _git_captured_output(["git", "pull", "--rebase", "--autostash"])
                    + "\n\nIf you still see errors, open a terminal here and run:\n"
                    "  git stash -u\n  git pull --rebase\n  git stash pop",
                )
                return

            _run_local(["git", "add", "-A"], check=True)
            res = subprocess.run(
                ["git", "commit", "-m", "Admin Panel Update: content synced"],
                cwd=cwd,
                capture_output=True,
                text=True,
                creationflags=cf_local,
            )

            commit_out = ((res.stdout or "") + (res.stderr or "")).lower()
            if res.returncode != 0:
                if "nothing to commit" in commit_out or "working tree clean" in commit_out:
                    messagebox.showinfo(
                        "No Changes",
                        "No new changes to push (already in sync).",
                    )
                    return
                msg = "\n\n".join(
                    x.strip()
                    for x in (res.stdout, res.stderr)
                    if x and x.strip()
                )
                messagebox.showwarning(
                    "Git Sync Failed",
                    "Commit step failed:\n\n" + (msg or "(no output)"),
                )
                return

            r_push = _run_net_inherit_stdio(["git", "push", "origin", "HEAD"])
            if r_push.returncode != 0:
                messagebox.showwarning(
                    "Git Push Failed",
                    "Saved locally but push failed.\n\nDetails:\n"
                    + _git_captured_output(["git", "push", "origin", "HEAD"]),
                )
                return

            messagebox.showinfo(
                "Vercel Sync Success",
                "Pushed to GitHub!\n\nVercel will deploy your changes automatically.",
            )

        except subprocess.CalledProcessError as e:
            parts = []
            if e.stdout and e.stdout.strip():
                parts.append(e.stdout.strip())
            if e.stderr and e.stderr.strip():
                parts.append(e.stderr.strip())
            detail = "\n\n".join(parts) if parts else str(e)
            messagebox.showwarning(
                "Git Sync Failed",
                f"Sync failed during: {' '.join(e.cmd)}\n\nDetails:\n{detail}",
            )
        except Exception as e:
            messagebox.showwarning("Sync Error", f"An unexpected error occurred:\n{e}")

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
