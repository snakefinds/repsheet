from __future__ import annotations

import json
import os
import threading
import traceback
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk

from .gsheet_scraper import build_picksly_from_kakobuy_url, scrape_sheet


@dataclass(frozen=True)
class ScrapeConfig:
    sheet_url_or_id: str
    api_key: str
    out_path: str
    affcode: str


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Google Sheet Scraper")
        self.minsize(760, 520)

        self.var_sheet = tk.StringVar()
        self.var_api_key = tk.StringVar()
        self.var_out = tk.StringVar(value=os.path.abspath("sheet_scrape.json"))
        self.var_affcode = tk.StringVar(value="7hjf5")
        self.var_busy = tk.BooleanVar(value=False)

        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        header = ttk.Label(
            root,
            text="Scrape a Google Sheet into JSON (titles, prices, images, links).",
            font=("Segoe UI", 12, "bold"),
        )
        header.pack(anchor="w")

        form = ttk.Frame(root)
        form.pack(fill="x", pady=(12, 8))
        form.columnconfigure(1, weight=1)

        def add_row(row: int, label: str, widget: tk.Widget) -> None:
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=6)
            widget.grid(row=row, column=1, sticky="ew", pady=6)

        sheet_entry = ttk.Entry(form, textvariable=self.var_sheet)
        add_row(0, "Spreadsheet link / ID", sheet_entry)

        key_entry = ttk.Entry(form, textvariable=self.var_api_key, show="•")
        add_row(1, "Google Sheets API key", key_entry)

        out_row = ttk.Frame(form)
        out_row.columnconfigure(0, weight=1)
        out_entry = ttk.Entry(out_row, textvariable=self.var_out)
        out_entry.grid(row=0, column=0, sticky="ew")
        ttk.Button(out_row, text="Browse…", command=self._pick_out).grid(row=0, column=1, padx=(8, 0))
        add_row(2, "Output JSON file", out_row)

        aff_entry = ttk.Entry(form, textvariable=self.var_affcode)
        add_row(3, "Affcode (Kakobuy)", aff_entry)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(0, 10))
        actions.columnconfigure(0, weight=1)

        self.btn_run = ttk.Button(actions, text="Scrape", command=self._on_run)
        self.btn_run.grid(row=0, column=1, sticky="e")

        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))

        ttk.Label(root, text="Log").pack(anchor="w")
        self.txt = tk.Text(root, height=16, wrap="word")
        self.txt.pack(fill="both", expand=True, pady=(6, 0))
        self.txt.configure(state="disabled")

        footer = ttk.Label(
            root,
            text="Tip: This uses API-key mode to preserve the real hyperlink URLs (so LINK cells work).",
            foreground="#555555",
        )
        footer.pack(anchor="w", pady=(10, 0))

        self._set_busy(False)

    def _pick_out(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Save output JSON",
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if path:
            self.var_out.set(path)

    def _append_log(self, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + ("\n" if not msg.endswith("\n") else ""))
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.var_busy.set(busy)
        state = "disabled" if busy else "normal"
        self.btn_run.configure(state=state)
        if busy:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _validate(self) -> ScrapeConfig | None:
        sheet = self.var_sheet.get().strip()
        api_key = self.var_api_key.get().strip()
        out_path = self.var_out.get().strip()
        affcode = self.var_affcode.get().strip()

        if not sheet:
            messagebox.showerror("Missing input", "Please paste the spreadsheet link or ID.")
            return None
        if not api_key:
            messagebox.showerror("Missing input", "Please paste your Google Sheets API key.")
            return None
        if not out_path:
            messagebox.showerror("Missing input", "Please choose an output file.")
            return None
        if not affcode:
            affcode = "7hjf5"
            self.var_affcode.set(affcode)

        return ScrapeConfig(sheet, api_key, out_path, affcode)

    def _on_run(self) -> None:
        cfg = self._validate()
        if not cfg or self.var_busy.get():
            return

        self._set_busy(True)
        self._append_log("Starting scrape…")

        def worker() -> None:
            try:
                items = scrape_sheet(
                    sheet=cfg.sheet_url_or_id,
                    sheet_name=None,
                    auth="api_key",
                    credentials_path=None,
                    api_key=cfg.api_key,
                    oauth_client_secret_path=None,
                    oauth_token_path=None,
                    affcode=cfg.affcode,
                    resolve_affiliate_links=True,
                )
                picksly_count = 0
                for it in items:
                    pk = build_picksly_from_kakobuy_url(it.get("kakobuy", ""))
                    it["picksly"] = pk
                    if pk:
                        picksly_count += 1

                self.after(0, lambda: self._append_log(f"Extracted {len(items)} items."))
                self.after(0, lambda: self._append_log(f"Added picks.ly to {picksly_count} items."))
                payload = {"items": items}
                os.makedirs(os.path.dirname(os.path.abspath(cfg.out_path)) or ".", exist_ok=True)
                with open(cfg.out_path, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                self.after(0, lambda: self._append_log(f"Saved {len(items)} items to: {cfg.out_path}"))
                self.after(
                    0,
                    lambda: messagebox.showinfo(
                        "Done",
                        f"Saved {len(items)} items.\nPicks.ly added to {picksly_count} items.\n\n{cfg.out_path}",
                    ),
                )
            except Exception as e:
                tb = traceback.format_exc()
                self.after(0, lambda: self._append_log("ERROR: " + str(e)))
                self.after(0, lambda: self._append_log(tb))
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    app = App()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

