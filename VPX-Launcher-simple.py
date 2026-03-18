#!/usr/bin/env python3
"""VPX Launcher - simple edition with search, A-Z jump bar, wheel preview, and media scan."""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import platform
PLATFORM       = platform.system()  # "Darwin", "Linux", "Windows"
IS_MAC         = PLATFORM == "Darwin"
IS_LINUX       = PLATFORM == "Linux"

CONFIG_FILE    = Path.home() / ".vpx_launcher_config.json"

# Executable paths differ by platform
if IS_MAC:
    VPX_EXECUTABLE = Path("/Applications/VPinballX_BGFX.app/Contents/MacOS/VPinballX_BGFX")
    VPX_APP        = Path("/Applications/VPinballX_BGFX.app")
else:
    # Linux: look for VPinballX_BGFX in common locations, fall back to PATH
    _linux_candidates = [
        Path.home() / "vpinball" / "VPinballX_BGFX",
        Path.home() / "VPX" / "VPinballX_BGFX",
        Path("/usr/local/bin/VPinballX_BGFX"),
        Path("/opt/vpinball/VPinballX_BGFX"),
    ]
    VPX_EXECUTABLE = next((p for p in _linux_candidates if p.exists()),
                          Path("VPinballX_BGFX"))  # fallback: rely on PATH
    VPX_APP        = VPX_EXECUTABLE  # not used on Linux

LETTERS        = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ#")
PREV_W         = 160
PREV_H         = 160

# Cross-platform font names
if IS_MAC:
    FONT_UI   = "SF Pro Display"
    FONT_MONO = "JetBrains Mono"
else:
    FONT_UI   = "DejaVu Sans"   # widely available on Linux
    FONT_MONO = "DejaVu Sans Mono"
VPINMDB_URL    = "https://github.com/superhac/vpinmediadb/raw/refs/heads/main/vpinmdb.json"
VPSDB_URL      = "https://github.com/VirtualPinballSpreadsheet/vps-db/raw/refs/heads/main/db/vpsdb.json"
FUZZY_MIN      = 0.45
NO_MATCH       = "(no match found)"

# Local DB cache — stored next to the config file
VPINMDB_LOCAL  = Path.home() / ".vpx_vpinmdb.json"
VPSDB_LOCAL    = Path.home() / ".vpx_vpsdb.json"

# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_folder() -> Path:
    try:
        d = json.loads(CONFIG_FILE.read_text())
        p = Path(d.get("folder", "")).expanduser()
        if p.is_dir():
            return p
    except Exception:
        pass
    return Path.home()

def save_folder(folder: Path) -> None:
    try:
        CONFIG_FILE.write_text(json.dumps({"folder": str(folder)}, indent=2))
    except Exception:
        pass

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def find_vpx_files(root: Path) -> list[Path]:
    files = []
    for cur, _, names in os.walk(root, followlinks=False):
        for name in names:
            if name.lower().endswith(".vpx"):
                files.append(Path(cur) / name)
    files.sort(key=lambda p: p.name.lower())
    return files

def normalize(s: str) -> str:
    s = s.lower().strip()
    # treat spaces, underscores, dashes, dots all as word separators
    s = re.sub(r"[\s_\-\.]+", " ", s)
    s = re.sub(r"[\'\u2019\u2018`]", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def strip_meta(stem: str) -> str:
    """Remove VPX filename noise: (Williams 1990), [mod], v2.1, nw, mod, etc."""
    s = re.sub(r"\([^)]*\)", " ", stem)
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = re.sub(r"\bv\d+[\d.]*\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\bnw\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b(mod|vpx|update|fix|final|beta|alpha)\b", " ", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip()

def find_wheel(vpx_path: Path) -> Path | None:
    media_dir = vpx_path.parent / "medias"
    if not media_dir.is_dir():
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        for name in (f"wheel{ext}", f"Wheel{ext}"):
            p = media_dir / name
            if p.exists():
                return p
    return None

def table_has_wheel(vpx_path: Path) -> bool:
    return find_wheel(vpx_path) is not None

def ensure_png(src: Path) -> Path | None:
    """Convert image to PNG if needed. Uses sips on macOS, convert (ImageMagick) on Linux."""
    if src.suffix.lower() == ".png":
        return src
    out = src.with_suffix(".png")
    if out.exists() and out.stat().st_size > 0:
        return out
    if IS_MAC:
        cmd = ["sips", "-s", "format", "png", str(src), "--out", str(out)]
    else:
        # ImageMagick convert — may not be installed, that's fine, we'll fall back
        cmd = ["convert", str(src), str(out)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        return out if proc.returncode == 0 and out.exists() and out.stat().st_size > 0 else None
    except FileNotFoundError:
        return None  # tool not available, caller falls back to original

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

def launch_table(table_path: Path) -> tuple[bool, str]:
    if IS_MAC:
        attempts = [
            [str(VPX_EXECUTABLE), str(table_path)],
            [str(VPX_EXECUTABLE), "-play", str(table_path)],
            ["open", "-a", str(VPX_APP), str(table_path)],
            ["open", "-a", str(VPX_APP), "--args", str(table_path)],
            ["open", "-a", str(VPX_APP), "--args", "-play", str(table_path)],
        ]
    else:
        # Linux: direct Popen only, no "open -a" equivalent
        attempts = [
            [str(VPX_EXECUTABLE), str(table_path)],
            [str(VPX_EXECUTABLE), "-play", str(table_path)],
        ]
    errors = []
    for cmd in attempts:
        try:
            if IS_MAC and cmd[0] == "open":
                proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if proc.returncode == 0:
                    return True, "ok"
                errors.append((proc.stderr or proc.stdout or f"exit {proc.returncode}").strip())
            else:
                proc = subprocess.Popen(cmd,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, start_new_session=True)
                if proc.poll() is None:
                    return True, "ok"
                errors.append(f"exit {proc.poll()}")
        except Exception as exc:
            errors.append(str(exc))
    return False, "\n".join(errors)

# ---------------------------------------------------------------------------
# vpinmediadb helpers
# ---------------------------------------------------------------------------

def fetch_url_json(url: str, label: str, status_cb=None) -> dict | list | None:
    try:
        if status_cb:
            status_cb(f"Downloading {label}...")
        req = urllib.request.Request(url, headers={"User-Agent": "vpx-launcher/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        if status_cb:
            status_cb(f"Loaded {label} ({len(data)} entries).")
        return data
    except Exception as e:
        if status_cb:
            status_cb(f"Failed {label}: {e}")
        return None

def load_local_json(path: Path) -> dict | list | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def save_local_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")

def db_cache_info(path: Path) -> str:
    """Return a human-readable cache status string."""
    if not path.exists():
        return "not cached"
    import datetime
    size_kb = path.stat().st_size // 1024
    mtime = datetime.datetime.fromtimestamp(path.stat().st_mtime)
    return f"{size_kb} KB  |  {mtime.strftime('%Y-%m-%d %H:%M')}"

def fetch_vpinmdb(status_cb=None) -> dict | None:
    """Load vpinmdb from local cache if available, otherwise download."""
    cached = load_local_json(VPINMDB_LOCAL)
    if cached is not None:
        if status_cb:
            status_cb(f"vpinmdb loaded from cache ({len(cached)} entries).")
        return cached
    return fetch_url_json(VPINMDB_URL, "vpinmediadb", status_cb)

def wheel_url_from_entry(entry: dict) -> str | None:
    return entry.get("wheel") or entry.get("Wheel") or None

def build_name_index(vpinmdb: dict, status_cb=None) -> list[tuple[str, str, str]]:
    """Build a (vps_id, display_title, normalized_title) index.

    vpinmdb.json has NO title fields — keys are VPS IDs only.
    We cross-reference vpsdb.json which has the actual table names.
    vpsdb entries look like: {"id": "JU4EAhCe", "name": "Starship Troopers", ...}
    """
    # Fetch VPS DB for table names — use local cache if available
    vpsdb_raw = load_local_json(VPSDB_LOCAL)
    if vpsdb_raw is not None:
        if status_cb:
            status_cb(f"vpsdb loaded from cache ({len(vpsdb_raw)} entries).")
    else:
        vpsdb_raw = fetch_url_json(VPSDB_URL, "vpsdb (names)", status_cb)

    # Build id->name map from VPS DB
    # Official VPS DB (VirtualPinballSpreadsheet/vps-db) is an array like:
    #   [{"id": "JU4EAhCe", "name": "Starship Troopers", "manufacturer": "Sega", ...}, ...]
    # xantari format may use "vpsId"/"title" instead
    id_to_name: dict[str, str] = {}
    if isinstance(vpsdb_raw, list):
        for entry in vpsdb_raw:
            if not isinstance(entry, dict):
                continue
            vid  = (entry.get("id") or entry.get("vpsId") or
                    entry.get("VPSId") or entry.get("Id") or "")
            name = (entry.get("name") or entry.get("title") or
                    entry.get("Name") or entry.get("Title") or "")
            if vid and name:
                id_to_name[str(vid)] = str(name)
    elif isinstance(vpsdb_raw, dict):
        for vid, entry in vpsdb_raw.items():
            if isinstance(entry, dict):
                name = (entry.get("name") or entry.get("title") or
                        entry.get("Name") or entry.get("Title") or "")
                if name:
                    id_to_name[str(vid)] = str(name)

    if status_cb:
        status_cb(f"Resolved {len(id_to_name)} table names from VPS DB.")

    rows = []
    for vps_id in vpinmdb.keys():
        # Use the real table name from vpsdb, fall back to the VPS ID itself
        title = id_to_name.get(vps_id, "")
        if not title:
            continue  # skip entries with no known name
        rows.append((vps_id, title, normalize(strip_meta(title))))

    return rows

def fuzzy_match(stem: str, name_index: list) -> tuple[str | None, str | None, float]:
    t1 = normalize(stem)
    t2 = normalize(strip_meta(stem))
    best_id, best_title, best_score = None, None, 0.0
    for vps_id, title, norm_title in name_index:
        if not norm_title:
            continue
        score = max(
            SequenceMatcher(None, t1, norm_title).ratio(),
            SequenceMatcher(None, t2, norm_title).ratio(),
        )
        if score > best_score:
            best_score, best_id, best_title = score, vps_id, title
    if best_score >= FUZZY_MIN:
        return best_id, best_title, best_score
    return None, None, best_score

def download_image(url: str, dest: Path) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(url, headers={"User-Agent": "vpx-launcher/1.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            dest.write_bytes(r.read())
        return dest.exists() and dest.stat().st_size > 0
    except Exception:
        return False

def fetch_image_bytes(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "vpx-launcher/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.read()
    except Exception:
        return None

# ---------------------------------------------------------------------------
# Scan result
# ---------------------------------------------------------------------------

class ScanResult:
    def __init__(self, vpx_path, vps_id, title, score, wheel_url):
        self.vpx_path  = vpx_path
        self.vps_id    = vps_id
        self.title     = title
        self.score     = score
        self.wheel_url = wheel_url

    @property
    def has_match(self):
        return self.vps_id is not None and self.wheel_url is not None

# ---------------------------------------------------------------------------
# Scan dialog
# ---------------------------------------------------------------------------

class ScanDialog(tk.Toplevel):
    PW = 200
    PH = 200

    def __init__(self, parent, results: list[ScanResult], vpinmdb: dict, name_index: list):
        super().__init__(parent)
        self.parent_app = parent
        self.results    = results
        self.vpinmdb    = vpinmdb
        self.name_index = name_index
        self.confirmed  = {i: tk.BooleanVar(value=r.has_match) for i, r in enumerate(results)}
        self._cur_idx   = None
        self._remote_img = None

        self.title("Scan for Wheel Media")
        self.geometry("900x620")
        self.minsize(700, 480)
        self.configure(bg="#1a1a1a")
        self.transient(parent)
        self.grab_set()

        self._build_ui()
        self._populate()
        if results:
            self.lst.selection_set(0)
            self._on_select()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg="#1a1a1a")
        hdr.pack(fill=tk.X, padx=14, pady=(14, 6))
        tk.Label(hdr, text="SCAN FOR WHEEL MEDIA", bg="#1a1a1a", fg="#e8872a",
                 font=(FONT_UI, 10, "bold")).pack(side=tk.LEFT)
        self.hdr_lbl = tk.Label(hdr, text="", bg="#1a1a1a", fg="#888680",
                                font=(FONT_MONO, 10))
        self.hdr_lbl.pack(side=tk.LEFT, padx=(12, 0))

        tk.Frame(self, bg="#333333", height=1).pack(fill=tk.X)

        # Main split
        main = tk.Frame(self, bg="#1a1a1a")
        main.pack(fill=tk.BOTH, expand=True, padx=14, pady=10)

        # Right detail panel (packed first)
        right = tk.Frame(main, bg="#1a1a1a", width=260)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))
        right.pack_propagate(False)

        tk.Label(right, text="MATCH PREVIEW", bg="#1a1a1a", fg="#e8872a",
                 font=(FONT_UI, 10, "bold")).pack(anchor="w", pady=(0, 6))

        # Remote wheel preview
        pf = tk.Frame(right, width=self.PW, height=self.PH, bg="#222222",
                      highlightthickness=1, highlightbackground="#444444")
        pf.pack()
        pf.pack_propagate(False)
        self.remote_box = tk.Label(pf, bg="#222222", text="", compound=tk.CENTER)
        self.remote_box.pack(fill=tk.BOTH, expand=True)

        # Match info
        info = tk.Frame(right, bg="#1a1a1a")
        info.pack(fill=tk.X, pady=(10, 0))

        for row, (lbl, var_name) in enumerate([
            ("VPX:", "vpx_var"),
            ("DB:",  "db_var"),
            ("Score:", "score_var"),
        ]):
            tk.Label(info, text=lbl, bg="#1a1a1a", fg="#888680",
                     font=(FONT_MONO, 9), anchor="w", width=6).grid(
                row=row, column=0, sticky="w")
            v = tk.StringVar()
            setattr(self, var_name, v)
            tk.Label(info, textvariable=v, bg="#1a1a1a", fg="#e8e6e1",
                     font=(FONT_MONO, 9), anchor="w",
                     wraplength=190, justify=tk.LEFT).grid(row=row, column=1, sticky="w")

        # Confirm checkbox
        self.chk_var = tk.BooleanVar()
        self.chk = tk.Checkbutton(right, text="Download this match",
                                  variable=self.chk_var,
                                  command=self._toggle_confirm,
                                  bg="#1a1a1a", fg="#e8e6e1",
                                  activebackground="#1a1a1a",
                                  activeforeground="#e8872a",
                                  selectcolor="#2e2e2e",
                                  font=(FONT_MONO, 10))
        self.chk.pack(anchor="w", pady=(12, 0))

        # Manual search
        tk.Frame(right, bg="#444444", height=1).pack(fill=tk.X, pady=(12, 6))
        tk.Label(right, text="MANUAL SEARCH", bg="#1a1a1a", fg="#e8872a",
                 font=(FONT_UI, 9, "bold")).pack(anchor="w")

        search_row = tk.Frame(right, bg="#1a1a1a")
        search_row.pack(fill=tk.X, pady=(4, 0))

        self.manual_var = tk.StringVar()
        self.manual_entry = tk.Entry(
            search_row, textvariable=self.manual_var,
            bg="#222222", fg="#e8e6e1",
            insertbackground="#e8872a",
            relief="flat", bd=0,
            font=(FONT_MONO, 10))
        self.manual_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=6,
                               padx=(0, 6))
        self.manual_entry.bind("<Return>", lambda _: self._manual_search())

        ttk.Button(search_row, text="Search", command=self._manual_search,
                   style="D.TButton", cursor="hand2").pack(side=tk.RIGHT)

        # Results listbox for manual search
        mw = tk.Frame(right, bg="#2a2a2a",
                      highlightthickness=1, highlightbackground="#444444")
        mw.pack(fill=tk.X, pady=(6, 0))
        self.manual_list = tk.Listbox(
            mw, bg="#2a2a2a", fg="#e8e6e1",
            selectbackground="#e8872a", selectforeground="#000000",
            activestyle="none", borderwidth=0, highlightthickness=0,
            font=(FONT_MONO, 9), height=5)
        self.manual_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.manual_list.bind("<<ListboxSelect>>", lambda _: self._apply_manual_match())
        msb = tk.Scrollbar(mw, orient=tk.VERTICAL, command=self.manual_list.yview,
                           bg="#2e2e2e", troughcolor="#2a2a2a",
                           bd=0, relief="flat", width=6)
        msb.pack(side=tk.RIGHT, fill=tk.Y)
        self.manual_list.config(yscrollcommand=msb.set)

        self._manual_results: list[tuple[str,str,str]] = []  # (vps_id, title, wheel_url)

        # Left list
        left = tk.Frame(main, bg="#1a1a1a")
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(left, text="TABLES WITHOUT WHEEL", bg="#1a1a1a", fg="#e8872a",
                 font=(FONT_UI, 10, "bold")).pack(anchor="w", pady=(0, 6))

        lw = tk.Frame(left, bg="#222222",
                      highlightthickness=1, highlightbackground="#333333")
        lw.pack(fill=tk.BOTH, expand=True)

        self.lst = tk.Listbox(lw, bg="#222222", fg="#e8e6e1",
                              selectbackground="#e8872a", selectforeground="#000000",
                              activestyle="none", borderwidth=0, highlightthickness=0,
                              font=(FONT_MONO, 11))
        self.lst.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.lst.bind("<<ListboxSelect>>", lambda _: self._on_select())

        sb = tk.Scrollbar(lw, orient=tk.VERTICAL, command=self.lst.yview,
                          bg="#2e2e2e", troughcolor="#222222",
                          bd=0, relief="flat", width=8)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lst.config(yscrollcommand=sb.set)

        # Bottom bar
        tk.Frame(self, bg="#333333", height=1).pack(fill=tk.X)
        bot = tk.Frame(self, bg="#1a1a1a")
        bot.pack(fill=tk.X, padx=14, pady=10)

        self.apply_btn = ttk.Button(bot, text="Apply", command=self._apply,
                                    style="A.TButton", cursor="hand2")
        self.apply_btn.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot, text="Cancel", command=self.destroy,
                   style="D.TButton", cursor="hand2").pack(side=tk.RIGHT)

        self.bot_status = tk.Label(bot, text="", bg="#1a1a1a", fg="#888680",
                                   font=(FONT_MONO, 10), anchor="w")
        self.bot_status.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _populate(self):
        matched = sum(1 for r in self.results if r.has_match)
        self.hdr_lbl.config(
            text=f"{len(self.results)} missing wheel  |  {matched} matched in DB")

        for i, r in enumerate(self.results):
            tag = "[OK]" if r.has_match else "[--]"
            self.lst.insert(tk.END, f"  {tag}  {r.vpx_path.stem}")
            self.lst.itemconfig(i, fg="#e8e6e1" if r.has_match else "#555555")

    def _on_select(self):
        sel = self.lst.curselection()
        if not sel:
            return
        idx = sel[0]
        if idx == self._cur_idx:
            return
        self._cur_idx = idx
        r = self.results[idx]

        self.vpx_var.set(r.vpx_path.stem)
        self.db_var.set(r.title if r.has_match else NO_MATCH)
        self.score_var.set(f"{r.score:.0%}" + ("" if r.has_match else "  (below threshold)"))
        self.chk_var.set(self.confirmed[idx].get())
        self.chk.config(state=tk.NORMAL if r.has_match else tk.DISABLED)

        self.remote_box.config(image="", text="loading..." if r.has_match else "no match")
        self._remote_img = None

        # Pre-fill manual search with cleaned stem
        self.manual_var.set(strip_meta(r.vpx_path.stem))
        self.manual_list.delete(0, tk.END)
        self._manual_results = []

        if r.has_match and r.wheel_url:
            threading.Thread(target=self._load_remote,
                             args=(idx, r.wheel_url), daemon=True).start()

    def _load_remote(self, idx, url):
        data = fetch_image_bytes(url)
        if not data:
            self.after(0, lambda: self.remote_box.config(text="preview failed"))
            return
        tmp = Path("/tmp/_vpx_scan_preview.png")
        try:
            tmp.write_bytes(data)
            def _show():
                if self._cur_idx != idx:
                    return
                try:
                    pic = tk.PhotoImage(file=str(tmp))
                    w, h = pic.width(), pic.height()
                    sx = max(1, (w + self.PW - 1) // self.PW)
                    sy = max(1, (h + self.PH - 1) // self.PH)
                    s  = max(sx, sy)
                    if s > 1:
                        pic = pic.subsample(s, s)
                    self._remote_img = pic
                    self.remote_box.config(image=self._remote_img, text="")
                except Exception:
                    self.remote_box.config(text="error")
            self.after(0, _show)
        except Exception:
            self.after(0, lambda: self.remote_box.config(text="error"))

    def _manual_search(self):
        query = self.manual_var.get().strip()
        if not query or not self.name_index:
            return
        # Score all entries and return top 8
        q_norm = normalize(query)
        scored = []
        for vps_id, title, norm_title in self.name_index:
            if not norm_title:
                continue
            score = max(
                SequenceMatcher(None, q_norm, norm_title).ratio(),
                SequenceMatcher(None, normalize(strip_meta(query)), norm_title).ratio(),
            )
            scored.append((score, vps_id, title))
        scored.sort(reverse=True)
        top = scored[:8]

        self.manual_list.delete(0, tk.END)
        self._manual_results = []
        for score, vps_id, title in top:
            entry = self.vpinmdb.get(vps_id, {})
            wheel_url = wheel_url_from_entry(entry)
            self._manual_results.append((vps_id, title, wheel_url))
            self.manual_list.insert(tk.END, f"  {score:.0%}  {title}")

    def _apply_manual_match(self):
        sel = self.manual_list.curselection()
        if not sel or self._cur_idx is None:
            return
        pick_idx = sel[0]
        if pick_idx >= len(self._manual_results):
            return
        vps_id, title, wheel_url = self._manual_results[pick_idx]

        # Update the result in place
        r = self.results[self._cur_idx]
        r.vps_id    = vps_id
        r.title     = title
        r.wheel_url = wheel_url
        r.score     = 1.0  # manually selected = 100%

        # Refresh info labels
        self.db_var.set(title)
        self.score_var.set("manual")
        self.chk_var.set(True)
        self.confirmed[self._cur_idx].set(True)
        self.chk.config(state=tk.NORMAL)

        # Update list row
        self.lst.delete(self._cur_idx)
        self.lst.insert(self._cur_idx, f"  [OK]  {r.vpx_path.stem}")
        self.lst.itemconfig(self._cur_idx, fg="#e8e6e1")
        self.lst.selection_set(self._cur_idx)

        # Load preview for the new match
        self.remote_box.config(image="", text="loading...")
        self._remote_img = None
        if wheel_url:
            threading.Thread(target=self._load_remote,
                             args=(self._cur_idx, wheel_url), daemon=True).start()

    def _toggle_confirm(self):
        if self._cur_idx is not None:
            self.confirmed[self._cur_idx].set(self.chk_var.get())
            r = self.results[self._cur_idx]
            tag = "[OK]" if self.chk_var.get() else "[ ]"
            self.lst.delete(self._cur_idx)
            self.lst.insert(self._cur_idx, f"  {tag}  {r.vpx_path.stem}")
            self.lst.itemconfig(self._cur_idx,
                fg="#e8e6e1" if self.chk_var.get() else "#888680")
            self.lst.selection_set(self._cur_idx)

    def _apply(self):
        to_do = [(i, self.results[i]) for i, v in self.confirmed.items()
                 if v.get() and self.results[i].has_match]
        if not to_do:
            messagebox.showinfo("Nothing selected",
                                "No confirmed matches to download.", parent=self)
            return

        self.apply_btn.config(state=tk.DISABLED)
        total = len(to_do)

        def _run():
            ok = 0
            for n, (i, r) in enumerate(to_do):
                self.after(0, lambda n=n: self.bot_status.config(
                    text=f"Downloading {n+1} / {total}..."))
                dest = r.vpx_path.parent / "medias" / "wheel.png"
                if download_image(r.wheel_url, dest):
                    ok += 1
                    self.after(0, lambda i=i: (
                        self.lst.itemconfig(i, fg="#4caf6e")))
                else:
                    self.after(0, lambda i=i: (
                        self.lst.itemconfig(i, fg="#e85555")))

            def _done():
                self.bot_status.config(text=f"Done. {ok}/{total} downloaded.")
                self.apply_btn.config(state=tk.NORMAL)
                self.parent_app.refresh()
            self.after(0, _done)

        threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("VPX Launcher")
        self.geometry("680x760")
        self.minsize(480, 500)
        self.configure(bg="#1a1a1a")

        # Start with no active tables folder; user must choose one first.
        self.last_folder = load_folder()
        self.folder: Path | None = None
        self.all_files: list[Path] = []
        self.filtered:  list[Path] = []
        self._filter_job   = None
        self._active_letter: str | None = None
        self._letter_btns:   dict[str, tk.Label] = {}
        self._preview_img  = None

        self._setup_styles()
        self._build_ui()
        self.after(100, self.refresh)

    # -----------------------------------------------------------------------
    # Styles
    # -----------------------------------------------------------------------

    def _setup_styles(self):
        s = ttk.Style(self)
        s.theme_use("default")
        s.configure("D.TButton",
            background="#2e2e2e", foreground="#ffffff",
            font=(FONT_UI, 12, "bold"),
            relief="flat", borderwidth=0, padding=(14, 8), focusthickness=0)
        s.map("D.TButton",
            background=[("active", "#444444")],
            foreground=[("active", "#ffffff")])
        s.configure("A.TButton",
            background="#e8872a", foreground="#000000",
            font=(FONT_UI, 13, "bold"),
            relief="flat", borderwidth=0, padding=(20, 10), focusthickness=0)
        s.map("A.TButton",
            background=[("active", "#7a4515")],
            foreground=[("active", "#ffffff")])

    # -----------------------------------------------------------------------
    # UI
    # -----------------------------------------------------------------------

    def _build_ui(self):
        # ── Top bar ──────────────────────────────────────────────────────────
        top = tk.Frame(self, bg="#1a1a1a")
        top.pack(fill=tk.X, padx=14, pady=(14, 8))

        ttk.Button(top, text="Select Folder", command=self.pick_folder,
                   style="D.TButton", cursor="hand2").pack(side=tk.LEFT)

        ttk.Button(top, text="Scan Media", command=self.scan_media,
                   style="D.TButton", cursor="hand2").pack(side=tk.LEFT, padx=(8, 0))

        ttk.Button(top, text="Update DB", command=self.update_db,
                   style="D.TButton", cursor="hand2").pack(side=tk.LEFT, padx=(8, 0))

        self.path_lbl = tk.Label(top, text=self._short_path(),
                                 bg="#1a1a1a", fg="#888680",
                                 font=(FONT_MONO, 10), anchor="w")
        self.path_lbl.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)

        # Wheel preview box — top right
        pf = tk.Frame(top, width=PREV_W, height=PREV_H, bg="#222222",
                      highlightthickness=1, highlightbackground="#444444")
        pf.pack(side=tk.RIGHT, padx=(8, 0))
        pf.pack_propagate(False)
        self.preview_box = tk.Label(pf, bg="#222222", text="", compound=tk.CENTER)
        self.preview_box.pack(fill=tk.BOTH, expand=True)

        tk.Frame(self, bg="#333333", height=1).pack(fill=tk.X)

        # ── Search bar ───────────────────────────────────────────────────────
        sf = tk.Frame(self, bg="#222222",
                      highlightthickness=1, highlightbackground="#333333")
        sf.pack(fill=tk.X, padx=14, pady=(10, 0))

        tk.Label(sf, text=" / ", bg="#222222", fg="#666666",
                 font=(FONT_MONO, 13)).pack(side=tk.LEFT)

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search)
        self.search_entry = tk.Entry(
            sf, textvariable=self.search_var,
            bg="#222222", fg="#e8e6e1",
            insertbackground="#e8872a",
            relief="flat", bd=0,
            font=(FONT_MONO, 13))
        self.search_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        self.search_entry.bind("<Escape>", lambda _: self._clear_search())
        self.search_entry.bind("<Down>",   lambda _: self._focus_list())

        clear = tk.Label(sf, text="  ×  ", bg="#222222", fg="#666666",
                         font=(FONT_MONO, 13), cursor="hand2")
        clear.pack(side=tk.RIGHT)
        clear.bind("<Button-1>", lambda _: self._clear_search())

        # ── A–Z bar ──────────────────────────────────────────────────────────
        az = tk.Frame(self, bg="#1a1a1a")
        az.pack(fill=tk.X, padx=14, pady=(8, 0))
        for letter in LETTERS:
            lbl = tk.Label(az, text=letter, bg="#1a1a1a", fg="#555555",
                           font=(FONT_UI, 11, "bold"),
                           width=2, cursor="hand2", padx=1)
            lbl.pack(side=tk.LEFT)
            lbl.bind("<Button-1>", lambda _e, l=letter: self._jump_to_letter(l))
            self._letter_btns[letter] = lbl

        # ── List ─────────────────────────────────────────────────────────────
        lw = tk.Frame(self, bg="#222222",
                      highlightthickness=1, highlightbackground="#333333")
        lw.pack(fill=tk.BOTH, expand=True, padx=14, pady=(8, 0))

        self.listbox = tk.Listbox(lw, bg="#222222", fg="#e8e6e1",
                                  selectbackground="#e8872a", selectforeground="#000000",
                                  activestyle="none", borderwidth=0, highlightthickness=0,
                                  font=(FONT_MONO, 13))
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", lambda _: self.launch())
        self.listbox.bind("<Return>",          lambda _: self.launch())

        sb = tk.Scrollbar(lw, orient=tk.VERTICAL, command=self.listbox.yview,
                          bg="#2e2e2e", troughcolor="#222222",
                          bd=0, relief="flat", width=8)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.config(yscrollcommand=sb.set)

        # ── Bottom bar ───────────────────────────────────────────────────────
        tk.Frame(self, bg="#333333", height=1).pack(fill=tk.X, pady=(8, 0))
        bot = tk.Frame(self, bg="#1a1a1a")
        bot.pack(fill=tk.X, padx=14, pady=12)

        ttk.Button(bot, text="Launch", command=self.launch,
                   style="A.TButton", cursor="hand2").pack(side=tk.RIGHT)

        self.status = tk.Label(bot, text="", bg="#1a1a1a", fg="#888680",
                               font=(FONT_MONO, 11), anchor="w")
        self.status.pack(side=tk.LEFT, fill=tk.X, expand=True)

    # -----------------------------------------------------------------------
    # Preview
    # -----------------------------------------------------------------------

    def _show_preview(self, vpx_path: Path):
        img_path = find_wheel(vpx_path)
        if img_path is None:
            self._clear_preview()
            return
        png = ensure_png(img_path) or img_path
        try:
            pic = tk.PhotoImage(file=str(png))
        except Exception:
            self._clear_preview()
            return
        w, h = pic.width(), pic.height()
        if w <= 0 or h <= 0:
            self._clear_preview()
            return
        sx = max(1, (w + PREV_W - 1) // PREV_W)
        sy = max(1, (h + PREV_H - 1) // PREV_H)
        s  = max(sx, sy)
        if s > 1:
            pic = pic.subsample(s, s)
        self._preview_img = pic
        self.preview_box.config(image=self._preview_img, text="")

    def _clear_preview(self):
        self._preview_img = None
        self.preview_box.config(image="", text="")

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _short_path(self) -> str:
        if self.folder is None:
            return "Select a VPX tables folder"
        try:
            return "~/" + str(self.folder.relative_to(Path.home()))
        except Exception:
            return str(self.folder)

    def _clear_search(self):
        self.search_var.set("")
        self.search_entry.focus_set()

    def _focus_list(self):
        self.listbox.focus_set()
        if not self.listbox.curselection() and self.filtered:
            self.listbox.selection_set(0)

    # -----------------------------------------------------------------------
    # Data
    # -----------------------------------------------------------------------

    def pick_folder(self):
        start_dir = self.folder or self.last_folder
        picked = filedialog.askdirectory(title="Select VPX tables folder",
                                         initialdir=str(start_dir))
        if picked:
            self.folder = Path(picked)
            self.last_folder = self.folder
            save_folder(self.folder)
            self._set_active_letter(None)
            self.search_var.set("")
            self.refresh()

    def refresh(self):
        if self.folder is None or not self.folder.is_dir():
            self.all_files = []
            self.filtered = []
            self.path_lbl.config(text=self._short_path())
            self._update_az_bar()
            self.listbox.delete(0, tk.END)
            self.listbox.insert(tk.END, "  Select Folder to load tables")
            self.status.config(text="Select a VPX tables folder to begin")
            self.title("VPX Launcher")
            self._clear_preview()
            return

        self.all_files = find_vpx_files(self.folder)
        self.path_lbl.config(text=self._short_path())
        self._update_az_bar()
        self._apply_filter()

    def _on_search(self, *_):
        if self.search_var.get():
            self._set_active_letter(None)
        if self._filter_job:
            self.after_cancel(self._filter_job)
        self._filter_job = self.after(100, self._apply_filter)

    def _apply_filter(self):
        if self.folder is None:
            self.filtered = []
            self.listbox.delete(0, tk.END)
            self.listbox.insert(tk.END, "  Select Folder to load tables")
            self.status.config(text="Select a VPX tables folder to begin")
            self.title("VPX Launcher")
            self._clear_preview()
            return

        query = normalize(self.search_var.get())
        self.filtered = (
            [p for p in self.all_files if query in normalize(p.stem)]
            if query else list(self.all_files)
        )
        self.listbox.delete(0, tk.END)
        self._clear_preview()

        if not self.filtered:
            self.listbox.insert(tk.END, "  No matches")
            self.status.config(text="0 tables")
            self.title("VPX Launcher")
            return

        for p in self.filtered:
            self.listbox.insert(tk.END, f"  {p.stem}")

        self.listbox.selection_set(0)
        self._show_preview(self.filtered[0])

        shown = len(self.filtered)
        total = len(self.all_files)
        self.status.config(text=f"{shown} / {total} tables" if query else f"{total} tables")
        self.title(f"VPX Launcher  ({shown})")

    def _on_select(self, _event=None):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.filtered):
            self._clear_preview()
            return
        self._show_preview(self.filtered[sel[0]])

    # -----------------------------------------------------------------------
    # A–Z bar
    # -----------------------------------------------------------------------

    def _update_az_bar(self):
        available: set[str] = set()
        for p in self.all_files:
            stem = p.stem.lstrip()
            if stem:
                c = stem[0].upper()
                available.add(c if c.isalpha() else "#")
        for letter, lbl in self._letter_btns.items():
            if letter in available:
                lbl.config(fg="#e8872a", cursor="hand2")
            else:
                lbl.config(fg="#444444", cursor="arrow")

    def _set_active_letter(self, letter: str | None):
        if self._active_letter and self._active_letter in self._letter_btns:
            self._letter_btns[self._active_letter].config(bg="#1a1a1a")
        self._active_letter = letter
        if letter and letter in self._letter_btns:
            self._letter_btns[letter].config(bg="#3a2810")

    def _jump_to_letter(self, letter: str):
        if not self.filtered:
            return
        if self.search_var.get():
            self.search_var.set("")
            self._apply_filter()
        self._set_active_letter(letter)
        for i, p in enumerate(self.filtered):
            stem = p.stem.lstrip()
            if not stem:
                continue
            first = stem[0].upper()
            if (letter == "#" and not first.isalpha()) or first == letter:
                self.listbox.selection_clear(0, tk.END)
                self.listbox.selection_set(i)
                self.listbox.see(i)
                self._show_preview(p)
                return

    # -----------------------------------------------------------------------
    # Update local DB cache
    # -----------------------------------------------------------------------

    def update_db(self):
        """Download both vpinmdb.json and vpsdb.json and save locally."""
        win = tk.Toplevel(self)
        win.title("Update DB Cache")
        win.geometry("480x260")
        win.minsize(380, 200)
        win.configure(bg="#1a1a1a")
        win.transient(self)
        win.grab_set()

        tk.Label(win, text="UPDATE LOCAL DB CACHE", bg="#1a1a1a", fg="#e8872a",
                 font=(FONT_UI, 10, "bold")).pack(anchor="w", padx=16, pady=(16, 4))
        tk.Frame(win, bg="#333333", height=1).pack(fill=tk.X)

        info_frame = tk.Frame(win, bg="#1a1a1a")
        info_frame.pack(fill=tk.X, padx=16, pady=(12, 0))

        # Current cache status
        for label, path in [("vpinmdb.json", VPINMDB_LOCAL), ("vpsdb.json", VPSDB_LOCAL)]:
            row = tk.Frame(info_frame, bg="#1a1a1a")
            row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=f"{label}:", bg="#1a1a1a", fg="#888680",
                     font=(FONT_MONO, 10), width=14, anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=db_cache_info(path), bg="#1a1a1a", fg="#e8e6e1",
                     font=(FONT_MONO, 10), anchor="w").pack(side=tk.LEFT)

        status_var = tk.StringVar(value="Ready to download.")
        tk.Label(win, textvariable=status_var, bg="#1a1a1a", fg="#888680",
                 font=(FONT_MONO, 10), anchor="w",
                 wraplength=440).pack(fill=tk.X, padx=16, pady=(12, 0))

        prog_frame = tk.Frame(win, bg="#1a1a1a")
        prog_frame.pack(fill=tk.X, padx=16, pady=(8, 0))

        # Simple progress bar using a label width trick
        prog_bg = tk.Frame(prog_frame, bg="#333333", height=6)
        prog_bg.pack(fill=tk.X)
        prog_bar = tk.Frame(prog_bg, bg="#e8872a", height=6, width=0)
        prog_bar.place(x=0, y=0, relheight=1.0)

        tk.Frame(win, bg="#333333", height=1).pack(fill=tk.X, pady=(12, 0))
        bot = tk.Frame(win, bg="#1a1a1a")
        bot.pack(fill=tk.X, padx=16, pady=10)

        dl_btn = ttk.Button(bot, text="Download Now", style="A.TButton", cursor="hand2")
        dl_btn.pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(bot, text="Close", command=win.destroy,
                   style="D.TButton", cursor="hand2").pack(side=tk.RIGHT)

        def _set_progress(frac: float):
            # frac 0.0 - 1.0
            win.update_idletasks()
            total_w = prog_bg.winfo_width()
            prog_bar.place(x=0, y=0, relheight=1.0, width=int(total_w * frac))

        def _do_download():
            dl_btn.config(state=tk.DISABLED)
            results = []

            tasks = [
                ("vpinmdb.json", VPINMDB_URL, VPINMDB_LOCAL),
                ("vpsdb.json",   VPSDB_URL,   VPSDB_LOCAL),
            ]

            def _run():
                for i, (name, url, local_path) in enumerate(tasks):
                    status_var.set(f"Downloading {name}...")
                    win.after(0, lambda f=(i / len(tasks)): _set_progress(f))
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "vpx-launcher/1.0"})
                        with urllib.request.urlopen(req, timeout=60) as r:
                            raw = r.read()
                        data = json.loads(raw.decode("utf-8"))
                        local_path.write_bytes(raw)
                        kb = len(raw) // 1024
                        results.append(f"  {name}: {kb} KB  ({len(data)} entries)  OK")
                    except Exception as e:
                        results.append(f"  {name}: FAILED — {e}")

                def _done():
                    _set_progress(1.0)
                    status_var.set("Done:  " + "  |  ".join(results))
                    dl_btn.config(state=tk.NORMAL, text="Download Again")
                win.after(0, _done)

            threading.Thread(target=_run, daemon=True).start()

        dl_btn.config(command=_do_download)

    # -----------------------------------------------------------------------
    # Scan for media
    # -----------------------------------------------------------------------

    def scan_media(self):
        if not self.all_files:
            messagebox.showinfo("Scan Media", "No tables loaded.", parent=self)
            return

        self.status.config(text="Scanning for missing wheel images...")
        self.update_idletasks()

        def _run():
            missing = [p for p in self.all_files if not table_has_wheel(p)]

            if not missing:
                self.after(0, lambda: self.status.config(
                    text="All tables already have wheel images."))
                self.after(0, lambda: messagebox.showinfo(
                    "Scan Media", "All tables already have wheel images.", parent=self))
                return

            self.after(0, lambda: self.status.config(
                text=f"{len(missing)} missing. Fetching DB..."))

            vpinmdb = fetch_vpinmdb(
                status_cb=lambda m: self.after(0, lambda msg=m: self.status.config(text=msg))
            )
            if vpinmdb is None:
                self.after(0, lambda: messagebox.showerror(
                    "Scan Media",
                    "Could not fetch vpinmediadb.\nCheck your internet connection.",
                    parent=self))
                return

            name_index = build_name_index(vpinmdb, status_cb=lambda m: self.after(0, lambda msg=m: self.status.config(text=msg)))
            results: list[ScanResult] = []

            for i, p in enumerate(missing):
                self.after(0, lambda i=i, t=len(missing):
                    self.status.config(text=f"Matching {i+1} / {t}..."))
                vps_id, title, score = fuzzy_match(p.stem, name_index)
                wheel_url = None
                if vps_id and vps_id in vpinmdb:
                    wheel_url = wheel_url_from_entry(vpinmdb[vps_id])
                results.append(ScanResult(p, vps_id, title, score, wheel_url))

            matched = sum(1 for r in results if r.has_match)
            self.after(0, lambda: self.status.config(
                text=f"Scan done. {matched}/{len(results)} matched."))
            self.after(0, lambda: ScanDialog(self, results, vpinmdb, name_index))

        threading.Thread(target=_run, daemon=True).start()

    # -----------------------------------------------------------------------
    # Launch
    # -----------------------------------------------------------------------

    def launch(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] >= len(self.filtered):
            return
        p = self.filtered[sel[0]]
        self.status.config(text=f"Launching {p.stem}...")
        self.update_idletasks()

        def _run():
            ok, err = launch_table(p)
            def _done():
                if ok:
                    self.status.config(text=f"Launched: {p.stem}")
                else:
                    self.status.config(text="Launch failed")
                    messagebox.showerror("VPX Launcher",
                                         f"Could not launch:\n{p.name}\n\n{err}")
            self.after(10, _done)
        threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    # On Linux allow launching even if we can't pre-verify the path
    # (it may be on PATH rather than an absolute path)
    if IS_MAC and not VPX_EXECUTABLE.exists():
        root = tk.Tk(); root.withdraw()
        messagebox.showerror("VPX Launcher", f"Not found:\n{VPX_EXECUTABLE}")
        root.destroy()
        return
    App().mainloop()

if __name__ == "__main__":
    main()
