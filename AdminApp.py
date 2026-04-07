import os
import re
import json
import threading
import subprocess
import tkinter as tk
import urllib.request
from tkinter import messagebox, ttk

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')

# ── helpers ──────────────────────────────────────────────────────────────────

def scrape_url(url):
    """Follow redirects, parse og: meta tags -> return dict or raise."""
    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36',
        'Accept-Language': 'en-US,en;q=0.9',
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        final_url = resp.url
        html = resp.read().decode('utf-8', errors='ignore')

    def meta(prop):
        # matches  property="og:title" content="..."  in any order
        m = re.search(
            r'<meta[^>]+property=["\']' + re.escape(prop) + r'["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I)
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']' + re.escape(prop) + r'["\']',
                html, re.I)
        return m.group(1).strip() if m else ''

    title = meta('og:title') or re.sub(r'<[^>]+>', '', (re.search(r'<title>(.*?)</title>', html, re.S) or type('', (), {'group': lambda *a: ''})()).group(1)).strip()
    image = meta('og:image')
    price = meta('og:price:amount') or meta('product:price:amount')

    # Kakobuy sometimes puts price in JSON-LD
    if not price:
        m = re.search(r'"price"\s*:\s*"?([\d.]+)"?', html)
        price = m.group(1) if m else ''

    return {'title': title, 'img': image, 'price': price, 'kakobuy': url, 'final_url': final_url}


# ── main app ─────────────────────────────────────────────────────────────────

class AdminApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SnakeFinds – Secure Desktop Admin")
        self.root.geometry("900x680")

        self.items = []
        self.theme = {}
        self.popup = {}

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
        tk.Label(tab, text="You can also paste a full JSON array [ {...}, {...} ] if you prefer.",
                 font=("Arial", 9), fg="gray").pack(anchor="w", pady=(2, 8))

        self.bulk_text = tk.Text(tab, height=16, font=("Courier", 10), wrap=tk.NONE)
        self.bulk_text.pack(expand=True, fill=tk.BOTH)

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
            return {"items": [], "theme": {}, "popup": {}}

    def _write_data(self):
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as fh:
                json.dump({"items": self.items, "theme": self.theme, "popup": self.popup}, fh, indent=2)
            return True
        except Exception as e:
            messagebox.showerror("File Error", f"Could not write data.json\n{e}")
            return False

    def fetch_data(self):
        d = self._read_data()
        self.items = d.get('items', [])
        self.theme = d.get('theme', {})
        self.popup = d.get('popup', {})
        self.refresh_listbox()
        self.update_theme_inputs()
        self.update_popup_inputs()

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
            "img":      self.f_img.get(),
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
            info = scrape_url(url)
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
        self.f_kakobuy.set(info.get('kakobuy', self.f_url.get().strip()))
        self.status_label.config(text="✅ Done!", fg="green")

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

        # Try JSON array first
        if raw.lstrip().startswith('['):
            try:
                items = json.loads(raw)
                if not isinstance(items, list):
                    raise ValueError
                self._bulk_add_items(items)
                return
            except Exception as e:
                messagebox.showerror("Invalid JSON", f"Couldn't parse JSON:\n{e}")
                return

        # Otherwise treat as one URL per line
        urls = [u.strip() for u in raw.splitlines() if u.strip()]
        if not urls:
            return
        self.bulk_progress.config(text=f"0 / {len(urls)} fetched…", fg="gray")
        self.notebook.tab(1, state=tk.DISABLED)
        threading.Thread(target=self._bulk_url_worker, args=(urls,), daemon=True).start()

    def _bulk_url_worker(self, urls):
        results = []
        for idx, url in enumerate(urls, 1):
            try:
                info = scrape_url(url)
                results.append(info)
                self.root.after(0, self.bulk_progress.config,
                                {'text': f"{idx} / {len(urls)} fetched…"})
            except Exception as e:
                results.append({'title': url, 'kakobuy': url, 'img': '', 'price': '', 'error': str(e)})
        self.root.after(0, self._bulk_url_done, results)

    def _bulk_url_done(self, results):
        self.notebook.tab(1, state=tk.NORMAL)
        self._bulk_add_items(results)
        errs = [r for r in results if r.get('error')]
        msg = f"✅ {len(results) - len(errs)} item(s) imported."
        if errs:
            msg += f"\n⚠ {len(errs)} URL(s) couldn't be fetched — added with blank fields."
        self.bulk_progress.config(text=msg, fg="green")

    def _bulk_add_items(self, items):
        next_id = max([i.get('id', 0) for i in self.items] + [0]) + 1
        for it in items:
            if not isinstance(it, dict):
                continue
            it.pop('error', None)
            it.pop('final_url', None)
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


if __name__ == "__main__":
    root = tk.Tk()
    app = AdminApp(root)
    root.mainloop()
