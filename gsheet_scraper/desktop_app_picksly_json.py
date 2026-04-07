"""
Desktop UI: add picks.ly to an existing scraped JSON (no spreadsheet).
"""

from __future__ import annotations

import json
import os
import threading
import traceback
import tkinter as tk
from dataclasses import dataclass
from tkinter import filedialog, messagebox, ttk

from .picksly_json import add_picksly_to_items


@dataclass(frozen=True)
class Config:
    input_path: str
    output_path: str


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Add picks.ly to JSON")
        self.minsize(720, 420)

        cwd = os.path.abspath(os.getcwd())
        self.var_in = tk.StringVar(value=os.path.join(cwd, "sheet_scrape.json"))
        self.var_out = tk.StringVar(value=os.path.join(cwd, "sheet_scrape_with_picksly.json"))
        self.var_busy = tk.BooleanVar(value=False)

        self._build()

    def _build(self) -> None:
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(
            root,
            text="Use your scraped JSON (e.g. sheet_scrape.json). No Google Sheet or API key.",
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w")

        form = ttk.Frame(root)
        form.pack(fill="x", pady=(14, 8))
        form.columnconfigure(1, weight=1)

        def row(r: int, label: str, var: tk.StringVar, browse: str) -> None:
            ttk.Label(form, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=6)
            f = ttk.Frame(form)
            f.grid(row=r, column=1, sticky="ew", pady=6)
            f.columnconfigure(0, weight=1)
            ttk.Entry(f, textvariable=var).grid(row=0, column=0, sticky="ew")
            ttk.Button(f, text="Browse…", command=browse).grid(row=0, column=1, padx=(8, 0))

        row(0, "Input JSON", self.var_in, self._pick_in)
        row(1, "Output JSON", self.var_out, self._pick_out)

        actions = ttk.Frame(root)
        actions.pack(fill="x", pady=(8, 10))
        actions.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(actions, mode="indeterminate")
        self.progress.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.btn = ttk.Button(actions, text="Add picks.ly", command=self._on_run)
        self.btn.grid(row=0, column=1, sticky="e")

        ttk.Label(root, text="Log").pack(anchor="w")
        self.txt = tk.Text(root, height=12, wrap="word")
        self.txt.pack(fill="both", expand=True, pady=(6, 0))
        self.txt.configure(state="disabled")

        ttk.Label(
            root,
            text="picks.ly is derived from the marketplace URL inside each kakobuy ?url= parameter.",
            foreground="#555555",
        ).pack(anchor="w", pady=(10, 0))

    def _pick_in(self) -> None:
        p = filedialog.askopenfilename(
            title="Open scraped JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if p:
            self.var_in.set(p)

    def _pick_out(self) -> None:
        p = filedialog.asksaveasfilename(
            title="Save JSON with picks.ly",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if p:
            self.var_out.set(p)

    def _log(self, msg: str) -> None:
        self.txt.configure(state="normal")
        self.txt.insert("end", msg + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _busy(self, on: bool) -> None:
        self.var_busy.set(on)
        self.btn.configure(state=("disabled" if on else "normal"))
        if on:
            self.progress.start(10)
        else:
            self.progress.stop()

    def _on_run(self) -> None:
        inp = self.var_in.get().strip()
        out = self.var_out.get().strip()
        if not inp or not os.path.isfile(inp):
            messagebox.showerror("Missing file", "Choose a valid input JSON file.")
            return
        if not out:
            messagebox.showerror("Missing file", "Choose an output path.")
            return
        if self.var_busy.get():
            return

        self._busy(True)
        self._log("Reading JSON…")

        def worker() -> None:
            try:
                with open(inp, encoding="utf-8") as f:
                    data = json.load(f)
                updated = add_picksly_to_items(data)
                n = len(updated.get("items") or [])
                picks = sum(
                    1
                    for it in (updated.get("items") or [])
                    if isinstance(it, dict) and (it.get("picksly") or "").strip()
                )
                os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
                with open(out, "w", encoding="utf-8") as f:
                    json.dump(updated, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                self.after(0, lambda: self._log(f"Wrote {n} items ({picks} with picks.ly) → {out}"))
                self.after(
                    0,
                    lambda: messagebox.showinfo("Done", f"{n} items saved.\n{picks} picks.ly links added.\n\n{out}"),
                )
            except Exception as e:
                self.after(0, lambda: self._log("ERROR: " + str(e)))
                self.after(0, lambda: self._log(traceback.format_exc()))
                self.after(0, lambda: messagebox.showerror("Error", str(e)))
            finally:
                self.after(0, lambda: self._busy(False))

        threading.Thread(target=worker, daemon=True).start()


def main() -> int:
    App().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
