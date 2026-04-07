import os
import tkinter as tk
from tkinter import messagebox, ttk
import json
import subprocess

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data.json')

class AdminApp:
    def __init__(self, root):
        self.root = root
        self.root.title("RepFinds - Secure Desktop Admin")
        self.root.geometry("850x650")
        
        self.items = []
        self.theme = {}
        
        self.setup_ui()
        self.fetch_data()

    def setup_ui(self):
        # Top frame for sync/url
        top_frame = tk.Frame(self.root, padx=10, pady=10)
        top_frame.pack(fill=tk.X)
        tk.Label(top_frame, text="🔒 Editing Securely Offline", font=("Arial", 10, "bold"), fg="green").pack(side=tk.LEFT, padx=10)
        tk.Button(top_frame, text="Reload Data.json", command=self.fetch_data).pack(side=tk.LEFT)
        
        # Git config toggle
        self.use_git_var = tk.BooleanVar(value=True)
        tk.Checkbutton(top_frame, text="Auto-Push to GitHub (Vercel Deploy)", variable=self.use_git_var).pack(side=tk.RIGHT, padx=10)

        # Notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        
        # Items Tab
        self.items_tab = tk.Frame(self.notebook)
        self.notebook.add(self.items_tab, text="Items")
        
        # Split items into left list, right editor
        self.item_list_frame = tk.Frame(self.items_tab)
        self.item_list_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)
        
        self.item_listbox = tk.Listbox(self.item_list_frame, width=30)
        self.item_listbox.pack(expand=True, fill=tk.Y)
        self.item_listbox.bind('<<ListboxSelect>>', self.on_item_select)
        
        btn_frame = tk.Frame(self.item_list_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        tk.Button(btn_frame, text="New Item", command=self.new_item).pack(side=tk.LEFT, expand=True, fill=tk.X)
        tk.Button(btn_frame, text="Delete", command=self.delete_item).pack(side=tk.LEFT, expand=True, fill=tk.X)
        
        self.editor_frame = tk.Frame(self.items_tab, padx=10, pady=10)
        self.editor_frame.pack(side=tk.LEFT, expand=True, fill=tk.BOTH)
        
        self.current_id = None
        self.f_name = self.make_field("Name:", self.editor_frame)
        self.f_cat = self.make_field("Category:", self.editor_frame)
        self.f_price = self.make_field("Price ($):", self.editor_frame)
        self.f_link = self.make_field("Buy Link:", self.editor_frame)
        self.f_qc = self.make_field("QC Pics Link:", self.editor_frame)
        self.f_img = self.make_field("Image URL:", self.editor_frame)
        self.f_badge = self.make_field("Badge:", self.editor_frame)
        
        tk.Button(self.editor_frame, text="Save Locally", command=self.save_current_item, bg="lightblue").pack(pady=10)
        tk.Button(self.editor_frame, text="Sync Items to Web", command=self.sync_items, bg="lightgreen", font=("Arial", 10, "bold")).pack(pady=20)
        
        # Theme Tab
        self.theme_tab = tk.Frame(self.notebook, padx=20, pady=20)
        self.notebook.add(self.theme_tab, text="Theme & Branding")
        
        self.t_sitename = self.make_field("Site Name:", self.theme_tab)
        self.t_tagline = self.make_field("Tagline:", self.theme_tab)
        self.t_accent = self.make_field("Accent Color (#hex):", self.theme_tab)
        self.t_bg = self.make_field("Background Color (#hex):", self.theme_tab)
        self.t_surface = self.make_field("Surface Color (#hex):", self.theme_tab)
        
        tk.Button(self.theme_tab, text="Sync Theme to Web", command=self.sync_theme, bg="lightgreen", font=("Arial", 10, "bold")).pack(pady=20)

    def make_field(self, label, parent):
        f = tk.Frame(parent, pady=3)
        f.pack(fill=tk.X)
        tk.Label(f, text=label, width=20, anchor="e").pack(side=tk.LEFT)
        var = tk.StringVar()
        tk.Entry(f, textvariable=var).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=10)
        return var

    def _read_data(self):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            messagebox.showerror("File Error", f"Could not read data.json\n{e}")
            return {"items": [], "theme": {}}
            
    def _write_data(self):
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump({"items": self.items, "theme": self.theme}, f, indent=2)
            return True
        except Exception as e:
            messagebox.showerror("File Error", f"Could not write to data.json\n{e}")
            return False

    def fetch_data(self):
        data = self._read_data()
        self.items = data.get('items', [])
        self.theme = data.get('theme', {})
        self.refresh_listbox()
        self.update_theme_inputs()

    def refresh_listbox(self):
        self.item_listbox.delete(0, tk.END)
        for it in self.items:
            self.item_listbox.insert(tk.END, it.get('name', 'Unnamed'))

    def on_item_select(self, event):
        sel = self.item_listbox.curselection()
        if not sel: return
        it = self.items[sel[0]]
        self.current_id = it.get('id')
        self.f_name.set(it.get('name', ''))
        self.f_cat.set(it.get('cat', ''))
        self.f_price.set(str(it.get('price', '')))
        self.f_link.set(it.get('link', ''))
        self.f_qc.set(it.get('qc_link', ''))
        self.f_img.set(it.get('img', ''))
        self.f_badge.set(it.get('badge', ''))

    def new_item(self):
        self.current_id = None
        self.f_name.set("")
        self.f_cat.set("Shoes")
        self.f_price.set("0")
        self.f_link.set("https://")
        self.f_qc.set("")
        self.f_img.set("")
        self.f_badge.set("")

    def save_current_item(self):
        try: price = float(self.f_price.get())
        except: price = 0.0
            
        new_it = {
            "name": self.f_name.get(),
            "cat": self.f_cat.get(),
            "price": price,
            "link": self.f_link.get(),
            "qc_link": self.f_qc.get(),
            "img": self.f_img.get(),
            "badge": self.f_badge.get()
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
        idx = next((i for i, item in enumerate(self.items) if item.get('id') == self.current_id), -1)
        if idx >= 0:
            self.item_listbox.selection_clear(0, tk.END)
            self.item_listbox.selection_set(idx)
            
        # Natively save it right here
        self._write_data()
        messagebox.showinfo("Saved", "Item saved locally. Click 'Sync Items to Web' to push via GitHub!")

    def delete_item(self):
        if self.current_id is not None:
            self.items = [i for i in self.items if i.get('id') != self.current_id]
            self.current_id = None
            self.refresh_listbox()
            self.new_item()
            self._write_data()

    def git_auto_push(self):
        if not self.use_git_var.get():
            return
        try:
            cwd = os.path.dirname(os.path.abspath(__file__))
            subprocess.run(["git", "add", "data.json"], check=True, cwd=cwd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            res = subprocess.run(["git", "commit", "-m", "💻 Admin Panel Update: content synced"], cwd=cwd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            if "working tree clean" not in res.stdout and "nothing to commit" not in res.stdout:
                subprocess.run(["git", "push"], check=True, cwd=cwd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                messagebox.showinfo("Vercel Sync Success", "Successfully auto-pushed data.json to GitHub! \n\nVercel will now deploy your site changes automatically.")
            else:
                messagebox.showinfo("No Changes", "No new changes detected in data.json to push.")
        except Exception as e:
            messagebox.showwarning("Git Auto-Push Failed", f"Your changes were saved locally to data.json, but GitHub auto-push failed.\n\nAre you sure Git is initialized?\nError details: {e}")

    def sync_items(self):
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Items successfully saved to local data.json!")

    def update_theme_inputs(self):
        t = self.theme
        self.t_sitename.set(t.get('siteName', ''))
        self.t_tagline.set(t.get('tagline', ''))
        self.t_accent.set(t.get('accent', ''))
        self.t_bg.set(t.get('bg', ''))
        self.t_surface.set(t.get('surface', ''))

    def sync_theme(self):
        data = {
            "siteName": self.t_sitename.get(),
            "tagline": self.t_tagline.get(),
            "accent": self.t_accent.get(),
            "bg": self.t_bg.get(),
            "surface": self.t_surface.get()
        }
        self.theme = data
        if self._write_data():
            if self.use_git_var.get():
                self.git_auto_push()
            else:
                messagebox.showinfo("Saved", "Theme successfully saved to local data.json!")

if __name__ == "__main__":
    root = tk.Tk()
    app = AdminApp(root)
    root.mainloop()
