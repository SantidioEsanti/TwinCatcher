import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import os
import time
import hashlib
import stat as _stat
import sqlite3
import sys as _sys
import threading
import datetime
import subprocess
try:
    import i18n
    _t = i18n.t
except Exception:
    import types as _types
    i18n = _types.SimpleNamespace(
        t=lambda k, **kw: k, set_lang=lambda c: None,
        get_lang=lambda: 'en', detect_lang=lambda: 'en',
        translate_literal=lambda s, lang=None: s)
    _t = i18n.t

try:
    from send2trash import send2trash
    HAS_SEND2TRASH = True
except ImportError:
    HAS_SEND2TRASH = False

try:
    from PIL import Image, ImageTk
    PILLOW_OK = True
    try:
        _LANCZOS = Image.Resampling.LANCZOS
    except AttributeError:
        _LANCZOS = Image.LANCZOS
except ImportError:
    PILLOW_OK = False
    _LANCZOS = None

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".bmp",
              ".tif", ".tiff", ".webp", ".heic", ".heif"}
THUMB_SIZE = (220, 220)


def is_image_path(path):
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def pixel_hash_sha256(path):
    """SHA-256 от декодированных RGB-пикселей. None, если Pillow нет / файл битый."""
    if not PILLOW_OK:
        return None
    try:
        with Image.open(path) as im:
            im.load()
            data = im.convert("RGB").tobytes()
    except Exception:
        return None
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def _thumb_cache_dir():
    if os.name == "nt":
        base = (os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.path.expanduser("~"))
    else:
        base = (os.environ.get("XDG_CACHE_HOME")
                or os.path.expanduser("~/.cache"))
    d = os.path.join(base, "DupFinder", "thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def _thumb_cache_path(src):
    try:
        st = os.stat(src)
        key = f"{os.path.abspath(src)}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        key = os.path.abspath(src)
    return os.path.join(
        _thumb_cache_dir(),
        hashlib.sha1(key.encode("utf-8", "surrogatepass")).hexdigest() + ".png")


def ensure_thumbnail(src, size=THUMB_SIZE):
    """Вернуть путь к PNG-превью, создать при необходимости. None при ошибке."""
    if not PILLOW_OK:
        return None
    dst = _thumb_cache_path(src)
    if os.path.isfile(dst):
        return dst
    try:
        with Image.open(src) as im:
            im.thumbnail(size, _LANCZOS)
            im.convert("RGB").save(dst, "PNG", optimize=True)
        return dst
    except Exception:
        return None


CHUNK_SIZE = 65536
PROGRESS_COLOR = "#3498DB"

LIGHT = {
    "bg": "#FFFFFF",
    "fg": "#000000",
    "group_even": "#FFE5CC",
    "group_odd": "#FFFFFF",
    "selected": "#FFC28C",
    "entry_bg": "#FFFFFF",
    "entry_fg": "#000000",
    "trough": "#EEEEEE",
    "btn_outline": "#888888",
    "btn_hover": "#F0F0F0",
}
DARK = {
    "bg": "#1E1E1E",
    "fg": "#E0E0E0",
    "group_even": "#2A2A2A",
    "group_odd": "#1E1E1E",
    "selected": "#3A3A3A",
    "entry_bg": "#2D2D2D",
    "entry_fg": "#E0E0E0",
    "trough": "#333333",
    "btn_outline": "#666666",
    "btn_hover": "#2A2A2A",
}

CRITERIA = [
    ("oldest",        "Самый старый"),
    ("newest",        "Самый новый"),
    ("shortest_path", "Самый короткий путь"),
    ("longest_path",  "Самый длинный путь"),
    ("in_folder",     "В указанной папке"),
]
CRITERIA_KEYS = [c[0] for c in CRITERIA]
CRITERIA_LABELS = [c[1] for c in CRITERIA]
DEFAULT_CRITERION_INDEX = 1

HASH_ALGO = "blake2b_256"
CACHE_FILENAME = "hash_cache.sqlite"
CACHE_TTL_SECONDS = 90 * 24 * 3600


def _default_cache_dir():
    """Каталог для базы кэша: профиль пользователя, а не папка данных."""
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return os.path.join(base, "DupFinder")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
        return os.path.join(base, "dupfinder")
    return os.path.expanduser("~/.dupfinder")


class HashCache:
    """Простой SQLite-кэш: path -> (size, mtime_ns, algo) -> full_hash.

    Инвалидация: запись считается валидной, только если совпали
    size, mtime_ns и hash_algo. Иначе — промах, файл пересчитывается.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS hashes (
        path      TEXT PRIMARY KEY,
        size      INTEGER NOT NULL,
        mtime_ns  INTEGER NOT NULL,
        hash_algo TEXT NOT NULL,
        full_hash TEXT NOT NULL,
        last_seen INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_hashes_algo_seen
        ON hashes(hash_algo, last_seen);
    """

    def __init__(self, db_path):
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(self.SCHEMA)
        self.conn.commit()
        self._pending = 0

    @staticmethod
    def _norm(path):
        return os.path.normcase(os.path.normpath(os.path.abspath(path)))

    def get(self, path, size, mtime_ns):
        cur = self.conn.execute(
            "SELECT full_hash FROM hashes "
            "WHERE path=? AND size=? AND mtime_ns=? AND hash_algo=?",
            (self._norm(path), size, mtime_ns, HASH_ALGO),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def put(self, path, size, mtime_ns, full_hash, now):
        self.conn.execute(
            "INSERT OR REPLACE INTO hashes"
            "(path,size,mtime_ns,hash_algo,full_hash,last_seen) "
            "VALUES(?,?,?,?,?,?)",
            (self._norm(path), size, mtime_ns, HASH_ALGO, full_hash, now),
        )
        self._pending += 1
        if self._pending >= 200:
            self.conn.commit()
            self._pending = 0

    def touch(self, path, now):
        self.conn.execute(
            "UPDATE hashes SET last_seen=? "
            "WHERE path=? AND hash_algo=?",
            (now, self._norm(path), HASH_ALGO),
        )

    def commit(self):
        self.conn.commit()
        self._pending = 0

    def prune(self, older_than_ts):
        self.conn.execute(
            "DELETE FROM hashes WHERE hash_algo=? AND last_seen < ?",
            (HASH_ALGO, older_than_ts),
        )
        self.conn.commit()

    def close(self):
        try:
            self.conn.commit()
            self.conn.close()
        except Exception:
            pass



class RoundedButton(tk.Canvas):
    def __init__(self, parent, text, command,
                 width=200, height=32, radius=10,
                 fill="#FFFFFF", fg="#000000", outline="#888888",
                 hover="#F0F0F0",
                 disabled_fill=None, disabled_outline=None,
                 font=("Segoe UI", 9, "bold"), parent_bg="#FFFFFF"):
        super().__init__(parent, width=width, height=height,
                         highlightthickness=0, bg=parent_bg, cursor="hand2")
        self._btn_command = command
        self._btn_w = width
        self._btn_h = height
        self._btn_r = radius
        self._btn_fill = fill
        self._btn_fg = fg
        self._btn_outline = outline
        self._btn_hover = hover
        self._btn_disabled_fill = disabled_fill if disabled_fill is not None else fill
        self._btn_disabled_outline = disabled_outline if disabled_outline is not None else outline
        self._btn_font = font
        self._btn_text = text
        self._btn_enabled = True
        self._draw(self._btn_fill, self._btn_fg, self._btn_outline)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _draw(self, fill, fg, outline):
        self.delete("all")
        r = self._btn_r
        w = self._btn_w - 2
        h = self._btn_h - 2
        pts = [
            2 + r, 2, w - r, 2, w, 2, w, 2 + r,
            w, h - r, w, h, w - r, h, 2 + r, h,
            2, h, 2, h - r, 2, 2 + r, 2, 2,
        ]
        self.create_polygon(pts, smooth=True, fill=fill,
                            outline=outline, width=1)
        self.create_text(self._btn_w / 2, self._btn_h / 2,
                         text=self._btn_text, fill=fg, font=self._btn_font)

    def _on_click(self, e):
        if self._btn_enabled and self._btn_command:
            self._btn_command()

    def _on_enter(self, e):
        if self._btn_enabled:
            self._draw(self._btn_hover, self._btn_fg, self._btn_outline)

    def _on_leave(self, e):
        if self._btn_enabled:
            self._draw(self._btn_fill, self._btn_fg, self._btn_outline)

    def set_theme(self, parent_bg, fill, fg, outline, hover,
                  disabled_fill=None, disabled_outline=None):
        self._btn_fill = fill
        self._btn_fg = fg
        self._btn_outline = outline
        self._btn_hover = hover
        self._btn_disabled_fill = disabled_fill if disabled_fill is not None else fill
        self._btn_disabled_outline = disabled_outline if disabled_outline is not None else outline
        self.configure(bg=parent_bg)
        if self._btn_enabled:
            self._draw(fill, fg, outline)
        else:
            self._draw(self._btn_disabled_fill, "#888888", self._btn_disabled_outline)

    def get_text(self):
        return self._btn_text

    def set_text(self, text):
        """Установить текст кнопки (для i18n). Перерисовывает Canvas."""
        if text == self._btn_text:
            return
        self._btn_text = text
        if self._btn_enabled:
            self._draw(self._btn_fill, self._btn_fg, self._btn_outline)
        else:
            self._draw(self._btn_disabled_fill, "#888888",
                       self._btn_disabled_outline)

    def enable(self):
        self._btn_enabled = True
        self.configure(cursor="hand2")
        self._draw(self._btn_fill, self._btn_fg, self._btn_outline)

    def disable(self):
        self._btn_enabled = False
        self.configure(cursor="")
        self._draw(self._btn_disabled_fill, "#888888", self._btn_disabled_outline)




class CircleButton(tk.Canvas):
    """Круглая кнопка (About «?»). Совместима с overlay-i18n."""

    def __init__(self, parent, text, command, size=30,
                 fill="#FFFFFF", fg="#000000", outline="#888888",
                 hover="#F0F0F0", parent_bg="#FFFFFF",
                 font=("Segoe UI", 11, "bold")):
        super().__init__(parent, width=size, height=size,
                         highlightthickness=0, bg=parent_bg, cursor="hand2")
        self._cb_size = size
        self._cb_text = text
        self._cb_fill = fill
        self._cb_fg = fg
        self._cb_outline = outline
        self._cb_hover = hover
        self._cb_font = font
        self._cb_cmd = command
        self._cb_enabled = True
        self._draw(self._cb_fill, self._cb_fg, self._cb_outline)
        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    def _draw(self, fill, fg, outline):
        self.delete("all")
        s = self._cb_size
        self.create_oval(1, 1, s - 1, s - 1, fill=fill,
                         outline=outline, width=1)
        self.create_text(s / 2, s / 2, text=self._cb_text,
                         fill=fg, font=self._cb_font)

    def _on_click(self, e):
        if self._cb_enabled and self._cb_cmd:
            self._cb_cmd()

    def _on_enter(self, e):
        if self._cb_enabled:
            self._draw(self._cb_hover, self._cb_fg, self._cb_outline)

    def _on_leave(self, e):
        if self._cb_enabled:
            self._draw(self._cb_fill, self._cb_fg, self._cb_outline)

    def set_theme(self, parent_bg, fill, fg, outline, hover):
        self._cb_fill = fill
        self._cb_fg = fg
        self._cb_outline = outline
        self._cb_hover = hover
        self.configure(bg=parent_bg)
        self._draw(fill, fg, outline)

    def get_text(self):
        return self._cb_text

    def set_text(self, text):
        if self._cb_text == text:
            return
        self._cb_text = text
        self._draw(self._cb_fill, self._cb_fg, self._cb_outline)


class ImageLightbox(tk.Toplevel):
    """Полноэкранный просмотр одного изображения: зум колесом, панорама ЛКМ, Esc — закрыть."""

    def __init__(self, master, path, bg="#000000"):
        super().__init__(master)
        self.path = path
        self.title(os.path.basename(path))
        self.configure(bg=bg)
        self.geometry("1000x750")

        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        self._img = None
        self._photo = None
        self._scale = 1.0
        self._base_scale = 1.0
        self._offset = [0, 0]
        self._drag = None

        try:
            self._img = Image.open(path)
            self._img.load()
        except Exception as e:
            messagebox.showerror(_t("lb_open_failed"), f"{path}\n\n{e}",
                                 parent=self)
            self.destroy()
            return

        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Configure>", self._on_resize)
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", self._on_wheel)
        self.canvas.bind("<Button-5>", self._on_wheel)
        self.after(50, self._fit)

    def _fit(self):
        cw = self.canvas.winfo_width() or 1000
        ch = self.canvas.winfo_height() or 750
        self._base_scale = min(cw / self._img.width,
                               ch / self._img.height, 1.0)
        self._scale = self._base_scale
        self._offset = [0, 0]
        self._render()

    def _on_resize(self, e):
        if e.widget is self and self._img is not None:
            self.after(80, self._fit)

    def _render(self):
        if self._img is None:
            return
        w = max(1, int(self._img.width * self._scale))
        h = max(1, int(self._img.height * self._scale))
        disp = self._img.resize((w, h), _LANCZOS)
        self._photo = ImageTk.PhotoImage(disp)
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        x = cw // 2 + self._offset[0]
        y = ch // 2 + self._offset[1]
        self.canvas.delete("all")
        self.canvas.create_image(x, y, image=self._photo, anchor="center")

    def _drag_start(self, e):
        self._drag = (e.x, e.y, self._offset[0], self._offset[1])

    def _drag_move(self, e):
        if not self._drag:
            return
        ox, oy, dx, dy = self._drag
        self._offset = [dx + (e.x - ox), dy + (e.y - oy)]
        self._render()

    def _drag_end(self, e):
        self._drag = None

    def _on_wheel(self, e):
        num = getattr(e, "num", None)
        delta = getattr(e, "delta", 0)
        if num == 4 or delta > 0:
            factor = 1.15
        elif num == 5 or delta < 0:
            factor = 1 / 1.15
        else:
            return
        self._scale = max(self._base_scale * 0.2,
                          min(self._base_scale * 20.0,
                              self._scale * factor))
        self._render()


class GroupViewer(tk.Toplevel):
    """Сетка превью с чекбоксами «Удалить» + кнопки группового действия.

    on_delete(paths, parent) вызывается при подтверждении удаления.
    """

    THUMB_COLS = 4

    def __init__(self, master, paths, on_delete, theme=None):
        super().__init__(master)
        self.paths = list(paths)
        self.on_delete = on_delete
        self._checks = {}
        self._thumbs = {}
        self._theme = theme or {}

        n = len(self.paths)
        self.title(_t("gv_title", n=n))
        self.geometry("1100x780")

        bg = self._theme.get("bg", "#F5F5F5")
        fg = self._theme.get("fg", "#222222")
        self.configure(bg=bg)

        top = tk.Frame(self, bg=bg)
        top.pack(fill="x", padx=12, pady=(10, 6))
        tk.Label(top, text=_t("gv_group", n=n), bg=bg, fg=fg,
                 font=("TkDefaultFont", 11, "bold")).pack(side="left")
        if not PILLOW_OK:
            tk.Label(top, text=_t("gv_no_pillow"),
                     bg=bg, fg=fg).pack(side="left")

        wrap = tk.Frame(self, bg=bg)
        wrap.pack(fill="both", expand=True, padx=8, pady=4)

        self._canvas = tk.Canvas(wrap, bg=bg, highlightthickness=0)
        vbar = ttk.Scrollbar(wrap, orient="vertical",
                             command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=vbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        self._grid = tk.Frame(self._canvas, bg=bg)
        self._canvas.create_window((0, 0), window=self._grid, anchor="nw")
        self._grid.bind(
            "<Configure>",
            lambda e: self._canvas.configure(
                scrollregion=self._canvas.bbox("all")))
        self._canvas.bind_all(
            "<MouseWheel>",
            lambda e: self._canvas.yview_scroll(
                int(-1 * (e.delta / 120)), "units"))
        self._canvas.bind_all(
            "<Button-4>",
            lambda e: self._canvas.yview_scroll(-1, "units"))
        self._canvas.bind_all(
            "<Button-5>",
            lambda e: self._canvas.yview_scroll(1, "units"))

        for i, path in enumerate(self.paths):
            self._add_card(i, path, bg, fg)

        bottom = tk.Frame(self, bg=bg)
        bottom.pack(fill="x", padx=12, pady=10)
        ttk.Button(bottom, text=_t("gv_keep_first"),
                   command=self._mark_all_but_first).pack(side="left")
        ttk.Button(bottom, text=_t("gv_delete_marked"),
                   command=self._delete_marked).pack(side="left", padx=(8, 0))
        ttk.Button(bottom, text=_t("gv_close"),
                   command=self.destroy).pack(side="right")

    def _add_card(self, idx, path, bg, fg):
        card = tk.Frame(self._grid, bg=bg, bd=1, relief="solid")
        r, c = divmod(idx, self.THUMB_COLS)
        card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
        self._grid.grid_columnconfigure(c, weight=1)

        thumb_lbl = tk.Label(card, bg="#DDDDDD",
                             width=THUMB_SIZE[0] // 8,
                             height=THUMB_SIZE[1] // 16)
        thumb_lbl.pack(padx=4, pady=4)

        if PILLOW_OK:
            tp = ensure_thumbnail(path)
            if tp:
                try:
                    im = Image.open(tp)
                    im.load()
                    ph = ImageTk.PhotoImage(im)
                    self._thumbs[path] = ph
                    thumb_lbl.configure(image=ph,
                                        width=im.width, height=im.height)
                except Exception:
                    thumb_lbl.configure(text=_t("gv_no_preview"))
            else:
                thumb_lbl.configure(text="(нет превью)")
        else:
            thumb_lbl.configure(text=_t("gv_no_pillow_short"))

        thumb_lbl.bind("<Button-1>",
                       lambda e, p=path: self._open_lightbox(p))

        name = os.path.basename(path)
        if len(name) > 34:
            name = name[:31] + "…"
        tk.Label(card, text=name, bg=bg, fg=fg, anchor="w").pack(
            fill="x", padx=6)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = -1
        tk.Label(card, text=_t("gv_size_fmt", size=f"{size:,}".replace(",", " ")),
                 bg=bg, fg=fg).pack(fill="x", padx=6)

        var = tk.BooleanVar(value=False)
        self._checks[path] = var
        ttk.Checkbutton(card, text=_t("gv_delete"), variable=var).pack(
            anchor="w", padx=6, pady=(0, 4))

    def _open_lightbox(self, path):
        if not PILLOW_OK:
            try:
                if os.name == "nt":
                    os.startfile(path)
                elif _sys.platform == "darwin":
                    subprocess.Popen(["open", path])
                else:
                    subprocess.Popen(["xdg-open", path])
            except Exception as e:
                messagebox.showerror(_t("msg_error"), str(e), parent=self)
            return
        ImageLightbox(self, path, bg="#000000")

    def _mark_all_but_first(self):
        if not self.paths:
            return
        for p in self.paths:
            self._checks[p].set(p != self.paths[0])

    def _delete_marked(self):
        marked = [p for p, v in self._checks.items() if v.get()]
        if not marked:
            messagebox.showinfo(_t("msg_no_marks"),
                                _t("msg_mark_hint"), parent=self)
            return
        if len(marked) >= len(self.paths):
            messagebox.showwarning(
                _t("msg_all_marked"),
                _t("msg_cannot_all"), parent=self)
            return
        if not messagebox.askyesno(
                _t("msg_confirm"),
                _t("msg_confirm_del_n", n=len(marked)), parent=self):
            return
        try:
            self.on_delete(marked, self)
        except Exception as e:
            messagebox.showerror(_t("msg_del_error"), str(e), parent=self)
            return
        self.destroy()


class DuplicateFinderApp:
    def __init__(self, root):
        self.root = root
        self.root.title("TwinCatcher")
        self.root.geometry("1150x740")
        self.root.minsize(760, 480)

        self.folder = None
        self.priority_folder = None
        self.scanning = False
        self.stop_flag = False
        self.scan_thread = None

        self.results = []
        self.groups = {}
        self._originals = {}
        self._sort_column = None
        self._sort_reverse = False

        self._hash_start_time = None
        self._hash_done = 0
        self._hash_total = 0

        self._dark_mode = False
        self._cache = None
        self._cache_hits = 0
        self._cache_misses = 0
        self._current_step = "folder"
        self._image_group_ids = set()
        try:
            i18n.set_lang(i18n.detect_lang())
        except Exception:
            pass
        self._build_ui()
        self._apply_theme()
        self._open_cache()

    def _build_ui(self):
        self._setup_branding()
        self._setup_lang_menu()
        toolbar = ttk.Frame(self.root, padding=(8, 6, 8, 0))
        toolbar.pack(side="top", fill="x")

        self.btn_folder = RoundedButton(
            toolbar, text="Выбрать папку…", command=self._choose_folder,
            width=180, height=32, radius=10,
            fill=LIGHT["bg"], fg=LIGHT["fg"], outline=LIGHT["btn_outline"],
            hover=LIGHT["btn_hover"], parent_bg=LIGHT["bg"])
        self.btn_folder.pack(side="left")

        self.lbl_folder = ttk.Label(toolbar, text="(папка не выбрана)")
        self.lbl_folder.pack(side="left", padx=(12, 12), fill="x", expand=True)

        self.btn_scan = RoundedButton(
            toolbar, text="Найти дубликаты", command=self._start_scan,
            width=180, height=32, radius=10,
            fill="#3498DB", fg="#FFFFFF", outline="#2980B9", hover="#2980B9",
            disabled_fill=LIGHT["group_even"], disabled_outline=LIGHT["btn_outline"],
            parent_bg=LIGHT["bg"])
        self.btn_scan.pack(side="left", padx=(0, 6))
        self.btn_scan.disable()

        self.btn_stop = RoundedButton(
            toolbar, text="Остановить", command=self._stop_scan,
            width=130, height=32, radius=10,
            fill=LIGHT["bg"], fg=LIGHT["fg"], outline=LIGHT["btn_outline"],
            hover=LIGHT["btn_hover"], parent_bg=LIGHT["bg"])
        self.btn_stop.pack(side="left")
        self.btn_stop.disable()

        self.var_dark = tk.BooleanVar(value=False)
        self.chk_dark = tk.Checkbutton(
            toolbar, text="Тёмная тема",
            variable=self.var_dark, command=self._toggle_theme,
            bg=LIGHT["bg"], fg=LIGHT["fg"],
            activebackground=LIGHT["bg"], activeforeground=LIGHT["fg"],
            selectcolor=LIGHT["entry_bg"],
            bd=0, highlightthickness=0, cursor="hand2",
        )
        self.chk_dark.pack(side="right", padx=(8, 0))

        # Чекбокс «Eng» — правее «Темная тема».
        self.var_eng = tk.BooleanVar(value=(i18n.get_lang() == "en"))
        self.chk_eng = tk.Checkbutton(
            toolbar, text="Eng",
            variable=self.var_eng,
            command=self._on_eng_toggle,
            bg=LIGHT["bg"], fg=LIGHT["fg"],
            activebackground=LIGHT["bg"], activeforeground=LIGHT["fg"],
            selectcolor=LIGHT["entry_bg"],
            bd=0, highlightthickness=0, cursor="hand2",
        )
        self.chk_eng.pack(side="right", padx=(8, 0), before=self.chk_dark)

        # Круглая кнопка «?» (About) — правее «Eng».
        self.btn_about = CircleButton(
            toolbar, text="?", command=self._show_about,
            size=32,
            fill=LIGHT["bg"], fg=LIGHT["fg"],
            outline=LIGHT["btn_outline"], hover=LIGHT["btn_hover"],
            parent_bg=LIGHT["bg"])
        self.btn_about.pack(side="right", padx=(6, 0), before=self.chk_eng)

        toolbar2 = ttk.Frame(self.root, padding=(8, 4, 8, 4))
        toolbar2.pack(side="top", fill="x")

        ttk.Label(toolbar2, text="Оригинал оставлять:").pack(side="left")

        self.cmb_criterion = ttk.Combobox(toolbar2, state="readonly",
                                          width=22, values=CRITERIA_LABELS)
        self.cmb_criterion.current(DEFAULT_CRITERION_INDEX)
        self.cmb_criterion.pack(side="left", padx=(6, 10))
        self.cmb_criterion.bind("<<ComboboxSelected>>",
                                self._on_criterion_change)

        self.btn_priority = RoundedButton(
            toolbar2, text="Папка приоритета…",
            command=self._choose_priority_folder,
            width=180, height=32, radius=10,
            fill=LIGHT["bg"], fg=LIGHT["fg"], outline=LIGHT["btn_outline"],
            hover=LIGHT["btn_hover"], parent_bg=LIGHT["bg"])
        self.btn_priority.pack(side="left")

        self.lbl_priority = ttk.Label(toolbar2, text="")
        self.lbl_priority.pack(side="left", padx=(8, 0), fill="x", expand=True)

        # Опциональное «умное» сравнение изображений по содержимому.
        # По умолчанию выключено: пиксельный SHA-256 декодирует каждую
        # картинку и заметно замедляет скан. Включается вручную.
        self.var_pixel = tk.BooleanVar(value=False)
        self.chk_pixel = tk.Checkbutton(
            toolbar2,
            text="Сравнивать изображения по содержимому (медленно)",
            variable=self.var_pixel,
            bg=LIGHT["bg"], fg=LIGHT["fg"],
            activebackground=LIGHT["bg"], activeforeground=LIGHT["fg"],
            selectcolor=LIGHT["entry_bg"],
            bd=0, highlightthickness=0, cursor="hand2",
        )
        self.chk_pixel.pack(side="right")

        progress_frame = ttk.Frame(self.root, padding=(8, 2, 8, 0))
        progress_frame.pack(side="top", fill="x")

        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.lbl_progress = ttk.Label(progress_frame, text="Готов", width=22,
                                      anchor="e")
        self.lbl_progress.pack(side="left")

        tree_frame = ttk.Frame(self.root, padding=(8, 6))
        tree_frame.pack(side="top", fill="both", expand=True)

        columns = ("status", "name", "size", "mtime", "folder", "group")
        self.tree = ttk.Treeview(tree_frame, columns=columns,
                                 show="headings", selectmode="extended")

        headings = {
            "status": "★", "name": "Имя файла", "size": "Размер",
            "mtime": "Изменён", "folder": "Папка", "group": "Группа",
        }
        widths = {"status": 30, "name": 260, "size": 100, "mtime": 160,
                  "folder": 430, "group": 70}
        anchors = {"status": "center", "size": "e"}

        for col in columns:
            self.tree.heading(col, text=headings[col],
                              command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=widths[col],
                             anchor=anchors.get(col, "w"),
                             stretch=(col == "folder"))

        vsb = ttk.Scrollbar(tree_frame, orient="vertical",
                            command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal",
                            command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Открыть папку с файлом",
                              command=self._open_folder)
        self.menu.add_command(label="Скопировать путь",
                              command=self._copy_path)
        self.menu.add_separator()
        self.menu.add_command(label="Сделать оригиналом в группе",
                              command=self._mark_selected_as_original)
        self.menu.add_command(label="Информация о группе",
                              command=self._show_group_info)
        self.menu.add_separator()
        self.menu.add_command(label="Удалить выбранные файлы…",
                              command=self._delete_selected)

        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        status_frame = ttk.Frame(self.root, padding=(8, 4))
        status_frame.pack(side="bottom", fill="x")

        self.lbl_timer = ttk.Label(status_frame, text="", anchor="w")
        self.lbl_timer.pack(side="left", padx=(0, 14))

        btn_frame = ttk.Frame(status_frame)
        btn_frame.pack(side="right")

        self.btn_del_dups = RoundedButton(
            btn_frame, text="Удалить все дубликаты",
            command=self._delete_all_duplicates,
            width=200, height=32, radius=10,
            fill=LIGHT["bg"], fg=LIGHT["fg"], outline=LIGHT["btn_outline"],
            hover=LIGHT["btn_hover"],
            disabled_fill=LIGHT["group_even"],
            disabled_outline=LIGHT["btn_outline"],
            parent_bg=LIGHT["bg"])
        self.btn_del_dups.pack(side="left", padx=(0, 6))
        self.btn_del_dups.disable()

        self.btn_del_empty = RoundedButton(
            btn_frame, text="Удалить пустые папки",
            command=self._delete_empty_folders,
            width=200, height=32, radius=10,
            fill=LIGHT["bg"], fg=LIGHT["fg"], outline=LIGHT["btn_outline"],
            hover=LIGHT["btn_hover"],
            disabled_fill=LIGHT["group_even"],
            disabled_outline=LIGHT["btn_outline"],
            parent_bg=LIGHT["bg"])
        self.btn_del_empty.pack(side="left")
        self.btn_del_empty.disable()

        self.status = ttk.Label(status_frame, text="Выберите папку для поиска",
                                anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        # Синхронно переводим всё дерево виджетов — без мигания.
        self._retranslate_ui()

    def _open_cache(self):
        try:
            cache_dir = _default_cache_dir()
            db_path = os.path.join(cache_dir, CACHE_FILENAME)
            self._cache = HashCache(db_path)
            self.status.config(text=_t("status_cache", path=db_path))
        except Exception as e:
            print(f"[dup] cache disabled: {e}")
            self._cache = None

    def _close_cache(self):
        if self._cache is not None:
            try:
                self._cache.close()
            except Exception:
                pass
            self._cache = None

    def _toggle_theme(self):
        self._dark_mode = self.var_dark.get()
        self._apply_theme()


    # ------------------------------------------------------------------
    # TwinCatcher: брендинг и About
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # i18n: переключение RU/EN
    # ------------------------------------------------------------------
    def _on_eng_toggle(self):
        """Переключение RU/EN по чекбоксу «Eng»."""
        lang = "en" if self.var_eng.get() else "ru"
        i18n.set_lang(lang)
        self._retranslate_ui()
        if self.folder:
            self.status.config(
                text=i18n.t("status_folder", folder=self.folder))
        else:
            self.status.config(text=i18n.t("lbl_choose_folder"))

    def _setup_lang_menu(self):
        # На старте всегда язык системы.
        i18n.set_lang(i18n.detect_lang())

        # UI-переключатель языка — чекбокс «Eng» в _build_ui.

    def _i18n_cfg(self):
        return os.path.join(os.path.expanduser("~"), ".twincatcher.json")

    def _switch_lang(self, code):
        i18n.set_lang(code)
        try:
            import json as _json
            with open(self._i18n_cfg(), "w", encoding="utf-8") as f:
                _json.dump({"lang": code}, f)
        except Exception:
            pass
        self._retranslate_ui()
        self.status.config(text=i18n.t("lbl_choose_folder")
                           if not self.folder else
                           i18n.t("status_folder", folder=self.folder))

    def _retranslate_ui(self, target=None):
        """Обход виджетов: text, menu label, treeview heading, combobox values."""
        lang = i18n.get_lang()

        def _tr(s):
            return i18n.translate_literal(s, lang)

        def _menu(m):
            try:
                end = m.index("end")
            except Exception:
                return
            if end is None:
                return
            for i in range(int(end) + 1):
                try:
                    lbl = m.entrycget(i, "label")
                    if lbl:
                        m.entryconfigure(i, label=_tr(lbl))
                except Exception:
                    pass
                try:
                    sub = m.nametowidget(m.entrycget(i, "menu"))
                    _menu(sub)
                except Exception:
                    pass

        def walk(w):
            # Кастомные виджеты (RoundedButton на Canvas) — get_text/set_text.
            if hasattr(w, "get_text") and hasattr(w, "set_text"):
                try:
                    txt = w.get_text()
                    if txt:
                        new = _tr(txt)
                        if new != txt:
                            w.set_text(new)
                except Exception:
                    pass
            else:
                try:
                    txt = w.cget("text")
                    if txt:
                        new = _tr(txt)
                        if new != txt:
                            w.configure(text=new)
                except Exception:
                    pass
            # Toplevel.title — переводим и его.
            if isinstance(w, tk.Toplevel):
                try:
                    t0 = w.title()
                    nt = _tr(t0)
                    if t0 and nt != t0:
                        w.title(nt)
                except Exception:
                    pass
            if isinstance(w, tk.Menu):
                _menu(w)
            if isinstance(w, ttk.Treeview):
                for col in w["columns"]:
                    try:
                        h = w.heading(col, "text")
                        if h:
                            nh = _tr(h)
                            if nh != h:
                                w.heading(col, text=nh)
                    except Exception:
                        pass
            if isinstance(w, ttk.Combobox):
                try:
                    vals = list(w["values"])
                    nv = [_tr(v) for v in vals]
                    if nv != vals:
                        w["values"] = nv
                except Exception:
                    pass
            for c in w.winfo_children():
                walk(c)

        walk(target if target is not None else self.root)
        try:
            self.root.title(i18n.t("window_title"))
        except Exception:
            pass
        try:
            if hasattr(self, "_lang_menu"):
                self._lang_menu.entryconfigure(
                    0, label=i18n.t("lang_en"))
                self._lang_menu.entryconfigure(
                    1, label=i18n.t("lang_ru"))
        except Exception:
            pass

    def _setup_branding(self):
        """Заголовок, иконка, меню «Справка», горячая клавиша F1."""
        import os as _os
        try:
            self.root.title("TwinCatcher")
        except Exception:
            pass

        _here = _os.path.dirname(_os.path.abspath(__file__))
        _png = _os.path.join(_here, "Dup_logo_2.png")
        _ico = _os.path.join(_here, "Dup_logo_2.ico")

        # Иконка окна: PNG (кроссплатформенно) + ICO (Windows).
        try:
            if _os.path.exists(_png):
                self._logo_png = tk.PhotoImage(file=_png)
                self.root.iconphoto(True, self._logo_png)
        except Exception:
            pass
        try:
            if _os.name == "nt" and _os.path.exists(_ico):
                self.root.iconbitmap(_ico)
        except Exception:
            pass

        # Menubar не создаём: About — кнопка «?» в toolbar и F1.
        try:
            self.root.bind_all("<F1>", lambda e: self._show_about())
        except Exception:
            pass

    def _show_about(self):
        """Диалог «О программе TwinCatcher»."""
        import os as _os
        win = tk.Toplevel(self.root)
        win.title("О программе TwinCatcher")
        win.transient(self.root)
        win.resizable(False, False)

        # Палитра: берём из LIGHT, если доступна; иначе — системная.
        try:
            bg = LIGHT["bg"]
            fg = LIGHT["fg"]
            entry_bg = LIGHT["entry_bg"]
        except Exception:
            bg = fg = entry_bg = None

        if bg is not None:
            try:
                win.configure(bg=bg)
            except Exception:
                pass

        pad = tk.Frame(win, padx=24, pady=20)
        if bg is not None:
            try:
                pad.configure(bg=bg)
            except Exception:
                pass
        pad.pack(fill="both", expand=True)

        # Логотип (если файл рядом с dup_finder.py).
        _here = _os.path.dirname(_os.path.abspath(__file__))
        _png = _os.path.join(_here, "Dup_logo_2.png")
        if _os.path.exists(_png):
            try:
                img = tk.PhotoImage(file=_png)
                factor = max(1, img.width() // 112)
                img = img.subsample(factor)
                lbl_img = tk.Label(pad, image=img)
                if bg is not None:
                    try:
                        lbl_img.configure(bg=bg)
                    except Exception:
                        pass
                lbl_img.image = img  # удерживаем ссылку
                lbl_img.pack(pady=(0, 12))
            except Exception:
                pass

        def _label(text, size=10, bold=False, pady=2):
            f = ("TkDefaultFont", size, "bold") if bold else ("TkDefaultFont", size)
            l = tk.Label(pad, text=text, font=f, justify="center")
            if bg is not None:
                try:
                    l.configure(bg=bg, fg=fg)
                except Exception:
                    pass
            l.pack(pady=pady)

        _label("TwinCatcher", size=16, bold=True, pady=(0, 2))
        _label("Версия 1.0", size=10, pady=(0, 10))
        _label("Быстрый поиск дубликатов файлов.", size=10, pady=(0, 6))
        _label("Проект бесплатный, с открытым исходным кодом.", size=10, pady=(0, 4))
        _label("Если он вам помог — поставьте звезду на GitHub", size=10, pady=(0, 2))
        _label("или угостите автора чашкой кофе. Спасибо!", size=10, pady=(0, 14))

        btn = tk.Button(
            pad, text="Закрыть", command=win.destroy,
            bd=0, highlightthickness=0, cursor="hand2", padx=16, pady=4,
        )
        if bg is not None:
            try:
                btn.configure(
                    bg=entry_bg, fg=fg,
                    activebackground=bg, activeforeground=fg,
                )
            except Exception:
                pass
        btn.pack()

        # Центрируем относительно главного окна.
        win.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() - win.winfo_width()) // 2
            y = self.root.winfo_rooty() + (self.root.winfo_height() - win.winfo_height()) // 2
            win.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

        try:
            self._retranslate_ui(target=win)
        except Exception:
            pass
        try:
            win.grab_set()
            win.focus_set()
        except Exception:
            pass


    def _ensure_help_menu(self):
        """Гарантирует, что в menubar есть каскад Help/Справка."""
        try:
            cur = self.root.cget("menu")
            menubar = self.nametowidget(cur) if cur else None
        except Exception:
            menubar = None
        if menubar is None:
            try:
                menubar = tk.Menu(self.root)
                self.root.config(menu=menubar)
            except Exception as e:
                print(f"[i18n] ensure_help: no menubar: {e}")
                return
        labels = []
        try:
            end = menubar.index("end")
            if end is not None:
                for i in range(int(end) + 1):
                    try:
                        labels.append(menubar.entrycget(i, "label"))
                    except Exception:
                        pass
        except Exception:
            pass
        help_labels = {i18n.t("menu_help", lang="en"),
                       i18n.t("menu_help", lang="ru")}
        if set(labels) & help_labels:
            return  # уже есть
        try:
            helpmenu = tk.Menu(menubar, tearoff=0)
            helpmenu.add_command(label=i18n.t("menu_about"),
                                 command=self._show_about)
            menubar.add_cascade(label=i18n.t("menu_help"), menu=helpmenu)
            print("[i18n] help menu (re)created")
        except Exception as e:
            print(f"[i18n] ensure_help: cannot add help: {e}")

    def _apply_theme(self):
        c = DARK if self._dark_mode else LIGHT
        style = ttk.Style()
        style.theme_use("clam")

        self.root.configure(bg=c["bg"])
        style.configure("TFrame", background=c["bg"])
        style.configure("TLabel", background=c["bg"], foreground=c["fg"])
        style.configure("TButton",
                        background=c["entry_bg"], foreground=c["fg"],
                        borderwidth=0)
        style.map("TButton",
                  background=[("active", c["selected"]),
                              ("pressed", c["selected"]),
                              ("disabled", c["group_even"])],
                  foreground=[("disabled", "#888888")])

        style.configure("TEntry",
                        fieldbackground=c["entry_bg"],
                        foreground=c["entry_fg"],
                        insertcolor=c["fg"])
        style.configure("TCombobox",
                        fieldbackground=c["entry_bg"],
                        foreground=c["entry_fg"],
                        background=c["entry_bg"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", c["entry_bg"])],
                  foreground=[("readonly", c["entry_fg"])])

        style.configure("Horizontal.TProgressbar",
                        background=PROGRESS_COLOR,
                        troughcolor=c["trough"],
                        bordercolor=c["trough"],
                        lightcolor=PROGRESS_COLOR,
                        darkcolor=PROGRESS_COLOR)

        style.configure("Treeview",
                        background=c["bg"], foreground=c["fg"],
                        fieldbackground=c["bg"], bordercolor=c["bg"],
                        lightcolor=c["bg"], darkcolor=c["bg"])
        style.map("Treeview",
                  background=[("selected", c["selected"])],
                  foreground=[("selected", c["fg"])])
        style.configure("Treeview.Heading",
                        background=c["entry_bg"], foreground=c["fg"],
                        relief="flat")

        self.tree.tag_configure("even", background=c["group_even"],
                                foreground=c["fg"])
        self.tree.tag_configure("odd", background=c["group_odd"],
                                foreground=c["fg"])

        if hasattr(self, "chk_dark"):
            self.chk_dark.configure(
                bg=c["bg"], fg=c["fg"],
                activebackground=c["bg"], activeforeground=c["fg"],
                selectcolor=c["entry_bg"],
            )
        if hasattr(self, "chk_pixel"):
            self.chk_pixel.configure(
                bg=c["bg"], fg=c["fg"],
                activebackground=c["bg"], activeforeground=c["fg"],
                selectcolor=c["entry_bg"],
            )
        if hasattr(self, "chk_eng"):
            self.chk_eng.configure(
                bg=c["bg"], fg=c["fg"],
                activebackground=c["bg"], activeforeground=c["fg"],
                selectcolor=c["entry_bg"],
            )

        # Единая нейтральная палитра для всех кнопок, без красного.
        # Активный шаг подсвечивается через _highlight_step.
        for btn in (self.btn_folder, self.btn_priority, self.btn_stop,
                    self.btn_scan, self.btn_del_dups, self.btn_del_empty):
            btn.set_theme(c["bg"], c["bg"], c["fg"],
                          c["btn_outline"], c["btn_hover"],
                          disabled_fill=c["bg"],
                          disabled_outline=c["btn_outline"])
        if hasattr(self, "btn_about"):
            self.btn_about.set_theme(
                c["bg"], c["bg"], c["fg"],
                c["btn_outline"], c["btn_hover"])
        self._highlight_step(getattr(self, "_current_step", "folder"))

        for attr in ("status", "lbl_folder", "lbl_priority", "lbl_timer"):
            w = getattr(self, attr, None)
            if w is not None:
                w.configure(foreground=c["fg"])

    def _highlight_step(self, step):
        """Подсветить кнопку текущего шага, остальные — нейтральные."""
        self._current_step = step
        c = DARK if self._dark_mode else LIGHT

        if self._dark_mode:
            # Тёмная тема: активный шаг — янтарный, с тёмным текстом.
            hl_fill    = "#F39C12"
            hl_fg      = "#1A1A1A"
            hl_outline = "#D68910"
            hl_hover   = "#E67E22"
        else:
            # Светлая тема: активный шаг — голубой, текст белый.
            hl_fill    = "#3498DB"
            hl_fg      = "#FFFFFF"
            hl_outline = "#2980B9"
            hl_hover   = "#2980B9"

        # Неактивные кнопки — на системном фоне темы в обоих случаях.
        neutral_fill = c["bg"]

        mapping = {
            "folder": self.btn_folder,
            "scan": self.btn_scan,
            "stop": self.btn_stop,
            "delete_dups": self.btn_del_dups,
            "delete_empty": self.btn_del_empty,
        }
        for name, btn in mapping.items():
            if name == step:
                btn.set_theme(
                    c["bg"], hl_fill, hl_fg,
                    hl_outline, hl_hover,
                    disabled_fill=c["bg"],
                    disabled_outline=c["btn_outline"])
            else:
                btn.set_theme(
                    c["bg"], neutral_fill, c["fg"],
                    c["btn_outline"], c["btn_hover"],
                    disabled_fill=c["bg"],
                    disabled_outline=c["btn_outline"])
        # Кнопка «Папка приоритета» всегда нейтральна.
        self.btn_priority.set_theme(
            c["bg"], neutral_fill, c["fg"],
            c["btn_outline"], c["btn_hover"],
            disabled_fill=c["bg"],
            disabled_outline=c["btn_outline"])

    def _format_duration(self, seconds):
        seconds = int(max(0, seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _update_timer_label(self):
        if not self._hash_start_time:
            self.lbl_timer.config(text="")
            return
        elapsed = time.time() - self._hash_start_time
        if self._hash_done > 0 and self._hash_total > 0:
            avg = elapsed / self._hash_done
            eta_total = avg * self._hash_total
            self.lbl_timer.config(
                text=_t("timer_hashing",
                        elapsed=self._format_duration(elapsed),
                        eta=self._format_duration(eta_total)))
        else:
            self.lbl_timer.config(
                text=_t("timer_hashing_unk", elapsed=self._format_duration(elapsed)))

    def _reset_timer(self):
        self._hash_start_time = None
        self._hash_done = 0
        self._hash_total = 0
        self.lbl_timer.config(text="")

    def _current_criterion(self):
        idx = self.cmb_criterion.current()
        return CRITERIA_KEYS[idx] if 0 <= idx < len(CRITERIA_KEYS) else "oldest"

    def _current_criterion_label(self):
        idx = self.cmb_criterion.current()
        return CRITERIA_LABELS[idx] if 0 <= idx < len(CRITERIA_LABELS) else ""

    def _on_criterion_change(self, event=None):
        self._recompute_originals()
        if self.results:
            self._populate_tree()

    def _choose_priority_folder(self):
        folder = filedialog.askdirectory(
            title="Папка, файлы в которой считаются оригиналами")
        if folder:
            self.priority_folder = folder
            self.lbl_priority.config(text=folder)
            idx = CRITERIA_KEYS.index("in_folder")
            self.cmb_criterion.current(idx)
            self._recompute_originals()
            if self.results:
                self._populate_tree()

    def _mtime(self, path):
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def _pick_original(self, paths):
        if not paths:
            return None
        crit = self._current_criterion()
        if crit == "oldest":
            return min(paths, key=self._mtime)
        if crit == "newest":
            return max(paths, key=self._mtime)
        if crit == "shortest_path":
            return min(paths, key=len)
        if crit == "longest_path":
            return max(paths, key=len)
        if crit == "in_folder":
            pf = self.priority_folder
            if pf:
                pf_norm = os.path.normcase(os.path.normpath(pf))
                in_pool = []
                for p in paths:
                    try:
                        pn = os.path.normcase(os.path.normpath(p))
                    except Exception:
                        continue
                    if pn == pf_norm or pn.startswith(pf_norm + os.sep):
                        in_pool.append(p)
                if in_pool:
                    return min(in_pool, key=self._mtime)
            return min(paths, key=self._mtime)
        return paths[0]

    def _recompute_originals(self):
        self._originals = {}
        for gid, paths in self.groups.items():
            if len(paths) >= 2:
                try:
                    self._originals[gid] = self._pick_original(paths)
                except Exception as e:
                    print(f"[dup] pick original failed for group {gid}: {e}")

    def _choose_folder(self):
        folder = filedialog.askdirectory(
            title="Выберите папку для поиска дубликатов")
        if folder:
            self.folder = folder
            self.lbl_folder.config(text=folder)
            self.btn_scan.enable()
            self.status.config(text=_t("status_folder", folder=folder))
            self._highlight_step("scan")

    def _start_scan(self):
        if not self.folder or self.scanning:
            return
        self.scanning = True
        self.stop_flag = False
        self.btn_scan.disable()
        self.btn_stop.enable()
        self.btn_folder.disable()
        self.btn_del_dups.disable()
        self.btn_del_empty.disable()
        self._highlight_step("stop")
        self.progress["value"] = 0
        self.progress["maximum"] = 1
        self.tree.delete(*self.tree.get_children())
        self.results.clear()
        self.groups.clear()
        self._originals.clear()
        self._image_group_ids.clear()
        self._reset_timer()
        self._cache_hits = 0
        self._cache_misses = 0
        self.status.config(text=_t("status_scan"))
        self.lbl_progress.config(text=_t("lbl_finding"))

        self.scan_thread = threading.Thread(target=self._scan_worker,
                                            daemon=True)
        self.scan_thread.start()

    def _stop_scan(self):
        self.stop_flag = True
        self.lbl_progress.config(text=_t("lbl_stopping"))

    def _scan_worker(self):
        try:
            self._do_scan()
        except Exception as e:
            self.root.after(0, lambda: self.status.config(text=_t("status_scan_error", err=e)))
        finally:
            self.root.after(0, self._on_scan_finished)

    def _walk_files(self, root):
        """Рекурсивный обход каталога через os.scandir.

        На каждую запись — ровно один stat(). Симлинки пропускаются,
        не-регулярные файлы пропускаются, размер берётся из того же
        stat-результата (без повторных системных вызовов).
        """
        stack = [root]
        while stack:
            if self.stop_flag:
                return
            cur = stack.pop()
            try:
                entries = list(os.scandir(cur))
            except OSError:
                continue
            for entry in entries:
                if self.stop_flag:
                    return
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                mode = st.st_mode
                if _stat.S_ISLNK(mode):
                    continue
                if _stat.S_ISDIR(mode):
                    stack.append(entry.path)
                    continue
                if not _stat.S_ISREG(mode):
                    continue
                if st.st_size > 0:
                    yield entry.path, st.st_size

    def _do_scan(self):
        all_files = list(self._walk_files(self.folder))
        if self.stop_flag:
            return

        total = len(all_files)
        if total == 0:
            self.root.after(0, lambda: self.status.config(text=_t("status_no_files")))
            return

        self.root.after(0, lambda: self.status.config(
            text=_t("status_found_groups", total=total)))

        # Разделяем изображения и прочие файлы:
        # для изображений считаем пиксельный SHA-256 (ловит перекодированные
        # копии), для всех остальных — обычный файловый.
        image_files = [(p, s) for p, s in all_files if is_image_path(p)]
        non_image_files = [(p, s) for p, s in all_files if not is_image_path(p)]

        by_size = {}
        for path, size in non_image_files:
            by_size.setdefault(size, []).append(path)

        candidates = [paths for paths in by_size.values() if len(paths) > 1]
        total_candidates = sum(len(g) for g in candidates) + len(image_files)

        if total_candidates == 0:
            self.root.after(0, lambda: self.status.config(
                text=_t("status_no_cands")))
            return

        self.root.after(0, self._start_hash_timer, total_candidates)

        hashed = 0
        hash_groups = {}
        now = int(time.time())

        # --- 1) Не-изображения: файловый хэш + кэш ---
        for paths in candidates:
            for path in paths:
                if self.stop_flag:
                    return
                try:
                    st = os.stat(path)
                    size_val = st.st_size
                    mtime_ns = st.st_mtime_ns
                except OSError:
                    continue

                h = None
                if self._cache is not None:
                    try:
                        h = self._cache.get(path, size_val, mtime_ns)
                    except Exception as e:
                        print(f"[dup] cache get failed: {e}")
                        h = None

                if h is None:
                    if self._cache is not None:
                        self._cache_misses += 1
                    h = self._hash_file(path)
                    if h and self._cache is not None:
                        try:
                            self._cache.put(path, size_val, mtime_ns, h, now)
                        except Exception as e:
                            print(f"[dup] cache put failed: {e}")
                else:
                    self._cache_hits += 1
                    if self._cache is not None:
                        try:
                            self._cache.touch(path, now)
                        except Exception:
                            pass

                hashed += 1
                if h:
                    hash_groups.setdefault(h, []).append(path)
                self.root.after(0, self._update_progress,
                                hashed, total_candidates)
                if hashed % 25 == 0:
                    self.root.after(
                        0,
                        lambda hh=hashed, tt=total_candidates, ch=self._cache_hits:
                        self.status.config(
                            text=_t("status_hash_cached", done=hh, total=tt, hits=ch)))

        # --- 2) Изображения ---
        # Пиксельный SHA-256 — только если пользователь включил чекбокс
        # «Сравнивать изображения по содержимому». Иначе — обычный
        # файловый хэш (быстро, как для всех прочих файлов).
        use_pixel = bool(PILLOW_OK and self.var_pixel.get())
        for path, _size in image_files:
            if self.stop_flag:
                return
            key = None
            if use_pixel:
                px = pixel_hash_sha256(path)
                if px:
                    key = "px:" + px
            if key is None:
                # Флаг выключен, Pillow нет или файл битый — файловый хэш.
                h = self._hash_file(path)
                if h:
                    key = h
            hashed += 1
            if key:
                hash_groups.setdefault(key, []).append(path)
            self.root.after(0, self._update_progress,
                            hashed, total_candidates)
            if hashed % 25 == 0:
                self.root.after(
                    0,
                    lambda hh=hashed, tt=total_candidates:
                    self.status.config(
                        text=_t("status_hash", done=hh, total=tt)))

        if self._cache is not None:
            try:
                self._cache.commit()
                self._cache.prune(now - CACHE_TTL_SECONDS)
            except Exception as e:
                print(f"[dup] cache commit/prune failed: {e}")

        results = []
        group_num = 0
        for key, paths in hash_groups.items():
            if len(paths) > 1:
                group_num += 1
                self.groups[group_num] = paths
                if isinstance(key, str) and key.startswith("px:"):
                    self._image_group_ids.add(group_num)
                for p in paths:
                    try:
                        st = os.stat(p)
                        results.append((group_num, p, st.st_size, st.st_mtime))
                    except OSError:
                        continue

        self.results = results
        wasted = 0
        for gid, paths in self.groups.items():
            if paths:
                try:
                    s = os.path.getsize(paths[0])
                    wasted += s * (len(paths) - 1)
                except OSError:
                    pass

        self.root.after(0, lambda: self._on_scan_done(
            len(results), group_num, wasted))

    def _start_hash_timer(self, total_candidates):
        self._hash_start_time = time.time()
        self._hash_done = 0
        self._hash_total = total_candidates
        self.progress.configure(maximum=max(1, total_candidates), value=0)
        self._update_timer_label()

    def _hash_file(self, path):
        try:
            h = hashlib.blake2b(digest_size=32)
            with open(path, "rb") as f:
                while True:
                    if self.stop_flag:
                        return None
                    chunk = f.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    h.update(chunk)
            return h.hexdigest()
        except OSError:
            return None

    def _update_progress(self, value, maximum):
        self.progress["value"] = value
        self.progress["maximum"] = maximum
        self._hash_done = value
        self._hash_total = maximum
        self._update_timer_label()

    def _on_scan_done(self, count, group_num, wasted):
        self.progress["value"] = self.progress["maximum"]
        self.lbl_progress.config(text=_t("lbl_done"))
        if self._hash_start_time:
            elapsed = time.time() - self._hash_start_time
            self.lbl_timer.config(
                text=_t("timer_hashing_done",
                        elapsed=self._format_duration(elapsed)))
        cache_note = ""
        if self._cache is not None:
            cache_note = (f" Кэш: попаданий {self._cache_hits}, "
                          f"промахов {self._cache_misses}.")
        self.status.config(
            text=(f"Найдено {count} файлов в {group_num} группах. "
                  f"Можно освободить: {self._format_size(wasted)} "
                  f"(при сохранении одной копии в каждой группе)."
                  + cache_note))
        self._recompute_originals()
        self._populate_tree()

    def _on_scan_finished(self):
        self.scanning = False
        self.btn_stop.disable()
        self.btn_folder.enable()
        if self.folder:
            self.btn_scan.enable()
        else:
            self.btn_scan.disable()

        has_dups = bool(self.groups) and any(
            len(p) >= 2 for p in self.groups.values())
        if has_dups:
            self.btn_del_dups.enable()
            self.btn_del_empty.enable()
            self._highlight_step("delete_dups")
        else:
            self.btn_del_dups.disable()
            if self.folder:
                self.btn_del_empty.enable()
                self._highlight_step("delete_empty")
            else:
                self.btn_del_empty.disable()
                self._highlight_step("folder")

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for group, path, size, mtime in self.results:
            name = os.path.basename(path)
            folder = os.path.dirname(path)
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime(
                "%Y-%m-%d %H:%M:%S")
            size_str = self._format_size(size)
            tag = "even" if group % 2 == 0 else "odd"
            is_orig = (self._originals.get(group) == path)
            status = "★" if is_orig else ""
            self.tree.insert("", "end",
                             values=(status, name, size_str, mtime_str,
                                     folder, group),
                             tags=(tag,))

    def _format_size(self, size):
        if size < 1024:
            return f"{size} Б"
        for unit in ("КБ", "МБ", "ГБ", "ТБ"):
            size /= 1024.0
            if size < 1024:
                return f"{size:.1f} {unit}"
        return f"{size:.1f} ПБ"

    def _sort_by(self, column):
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False

        key_map = {
            "status": lambda r: 0 if self._originals.get(r[0]) == r[1] else 1,
            "name":   lambda r: os.path.basename(r[1]).lower(),
            "size":   lambda r: r[2],
            "mtime":  lambda r: r[3],
            "folder": lambda r: os.path.dirname(r[1]).lower(),
            "group":  lambda r: r[0],
        }
        self.results.sort(key=key_map[column], reverse=self._sort_reverse)
        self._populate_tree()

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            if row not in self.tree.selection():
                self.tree.selection_set(row)
            self.menu.post(event.x_root, event.y_root)

    def _get_selected_paths(self):
        paths = []
        for item in self.tree.selection():
            vals = self.tree.item(item, "values")
            try:
                paths.append(os.path.join(vals[4], vals[1]))
            except IndexError:
                continue
        return paths

    def _open_folder(self):
        paths = self._get_selected_paths()
        if not paths:
            return
        folder = os.path.dirname(paths[0])
        try:
            if os.name == "nt":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as e:
            messagebox.showerror(_t("msg_error"),
                             _t("msg_open_folder_err", err=e))

    def _on_tree_double_click(self, event):
        """Двойной клик: для групп-изображений — GroupViewer, иначе — папка."""
        row = self.tree.identify_row(event.y)
        if not row:
            return
        vals = self.tree.item(row, "values")
        try:
            gid = int(vals[5])
        except (ValueError, IndexError):
            return
        paths = self.groups.get(gid)
        if not paths:
            return
        if gid in self._image_group_ids:
            c = DARK if self._dark_mode else LIGHT
            GroupViewer(self.root, paths,
                        self._delete_paths_from_viewer, theme=c)
        else:
            self.tree.selection_set(row)
            self._open_folder()

    def _delete_paths_from_viewer(self, paths, parent=None):
        """Удалить набор файлов (из GroupViewer) и обновить группы/дерево."""
        if not paths:
            return
        deleted = 0
        errors = []
        deleted_set = set()
        for p in paths:
            try:
                self._remove_file(p)
                deleted += 1
                deleted_set.add(p)
            except Exception as e:
                errors.append(f"{p}: {e}")

        if deleted:
            self.status.config(text=_t("status_del_images", n=deleted))
            for gid in list(self.groups.keys()):
                new_paths = [p for p in self.groups[gid]
                             if p not in deleted_set]
                if len(new_paths) < 2:
                    del self.groups[gid]
                    self._image_group_ids.discard(gid)
                    self._originals.pop(gid, None)
                else:
                    self.groups[gid] = new_paths

        if errors and parent is not None:
            messagebox.showerror(_t("msg_del_errors"),
                                 "\n".join(errors[:10]), parent=parent)

        self.results = [r for r in self.results
                        if r[1] not in deleted_set and r[0] in self.groups]
        self._populate_tree()

    def _copy_path(self):
        paths = self._get_selected_paths()
        if paths:
            self.root.clipboard_clear()
            self.root.clipboard_append("\n".join(paths))
            self.status.config(text=_t("status_copied", n=len(paths)))

    def _mark_selected_as_original(self):
        sel = self.tree.selection()
        if not sel:
            return
        for item in sel:
            vals = self.tree.item(item, "values")
            try:
                group = int(vals[5])
                path = os.path.join(vals[4], vals[1])
            except (ValueError, IndexError):
                continue
            if group in self.groups and path in self.groups[group]:
                self._originals[group] = path
        self._populate_tree()
        self.status.config(text=_t("status_orig_set"))

    def _show_group_info(self):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], "values")
        try:
            group = int(vals[5])
        except (ValueError, IndexError):
            return
        group_files = [r for r in self.results if r[0] == group]
        total_size = sum(r[2] for r in group_files)
        wasted = total_size - group_files[0][2] if group_files else 0
        orig = self._originals.get(group, "—")
        messagebox.showinfo(
            _t("msg_group_info"),
            _t("gi_body", g=group, n=len(group_files),
               total=self._format_size(total_size),
               wasted=self._format_size(wasted), orig=orig))

    def _remove_file(self, path):
        if HAS_SEND2TRASH:
            send2trash(path)
        else:
            os.remove(path)

    def _delete_selected(self):
        paths = self._get_selected_paths()
        if not paths:
            return
        if not messagebox.askyesno(
                _t("msg_confirm"),
                _t("msg_confirm_del", n=len(paths),
                   trash_note=(_t("msg_trash_ok") if HAS_SEND2TRASH
                               else _t("msg_trash_warn")))):
            return
        deleted = 0
        errors = []
        for p in paths:
            try:
                self._remove_file(p)
                deleted += 1
            except Exception as e:
                errors.append(f"{p}: {e}")
        self.status.config(text=_t("status_del_count", deleted=deleted, errors=len(errors)))
        if errors:
            messagebox.showerror(_t("msg_del_errors"), "\n".join(errors[:10]))
        self._start_scan()

    def _delete_all_duplicates(self):
        if self.scanning:
            messagebox.showinfo(_t("msg_wait"), _t("msg_scan_running"))
            return
        if not self.groups:
            messagebox.showinfo(_t("msg_info"), _t("msg_no_dups_yet"))
            return

        if self._current_criterion() == "in_folder" and not self.priority_folder:
            if not messagebox.askyesno(
                    _t("msg_no_folder"),
                    _t("msg_priority_warn")):
                return

        self._recompute_originals()

        to_delete = []
        deleted_groups = set()
        for gid, paths in self.groups.items():
            if len(paths) < 2:
                continue
            original = self._originals.get(gid) or self._pick_original(paths)
            for p in paths:
                if p != original:
                    to_delete.append(p)
            deleted_groups.add(gid)

        if not to_delete:
            messagebox.showinfo(_t("msg_info"), _t("msg_no_dups_del"))
            return

        total_size = 0
        for p in to_delete:
            try:
                total_size += os.path.getsize(p)
            except OSError:
                pass

        kept = len(deleted_groups)
        crit_label = self._current_criterion_label()
        trash_note = (_t("msg_trash_ok") if HAS_SEND2TRASH
                      else _t("msg_trash_warn"))

        if not messagebox.askyesno(
                _t("msg_confirm"),
                _t("msg_confirm_all", n=len(to_delete),
                   crit=crit_label, kept=kept,
                   size=self._format_size(total_size),
                   trash_note=trash_note)):
            return

        deleted = 0
        errors = []
        for p in to_delete:
            try:
                self._remove_file(p)
                deleted += 1
            except Exception as e:
                errors.append(f"{p}: {e}")

        self._remove_groups_from_view(deleted_groups)

        self.status.config(text=_t("status_del_count", deleted=deleted, errors=len(errors)))
        if errors:
            messagebox.showerror(_t("msg_del_errors"),
                                 _t("msg_del_summary",
                                    deleted=deleted, errors=len(errors),
                                    tail="\n".join(errors[:10])))

        # Дубликаты удалены — следующий логический шаг.
        self.btn_del_dups.disable()
        if self.folder:
            self.btn_del_empty.enable()
            self._highlight_step("delete_empty")
        else:
            self._highlight_step("folder")

    def _remove_groups_from_view(self, deleted_groups):
        if not deleted_groups:
            return
        for item in self.tree.get_children():
            vals = self.tree.item(item, "values")
            try:
                if int(vals[5]) in deleted_groups:
                    self.tree.delete(item)
            except (ValueError, IndexError):
                continue
        for gid in deleted_groups:
            self.groups.pop(gid, None)
            self._originals.pop(gid, None)
            self._image_group_ids.discard(gid)
        self.results = [r for r in self.results
                        if r[0] not in deleted_groups]

    def _find_empty_folders(self):
        empty = []
        root_norm = os.path.normpath(self.folder)
        for dirpath, _dirnames, _filenames in os.walk(self.folder,
                                                      topdown=False):
            if os.path.normpath(dirpath) == root_norm:
                continue
            try:
                if not os.listdir(dirpath):
                    empty.append(dirpath)
            except OSError:
                continue
        return empty

    def _delete_empty_folders(self):
        if self.scanning:
            messagebox.showinfo("Подождите", "Сканирование ещё идёт")
            return
        if not self.folder:
            messagebox.showinfo(_t("msg_info"), _t("msg_choose_folder"))
            return

        self.status.config(text=_t("status_finding_empty"))
        self.root.update_idletasks()

        empty = self._find_empty_folders()
        if not empty:
            self.status.config(text=_t("status_no_empty"))
            messagebox.showinfo(_t("msg_done"), _t("msg_no_empty"))
            return

        if not messagebox.askyesno(
                _t("msg_confirm"),
                _t("msg_confirm_empty", n=len(empty),
                   folder=self.folder)):
            self.status.config(text=_t("msg_empty_cancelled"))
            return

        deleted = 0
        errors = []
        root_norm = os.path.normpath(self.folder)

        for dirpath, _dirnames, _filenames in os.walk(self.folder,
                                                      topdown=False):
            if os.path.normpath(dirpath) == root_norm:
                continue
            try:
                if not os.listdir(dirpath):
                    os.rmdir(dirpath)
                    deleted += 1
            except OSError as e:
                errors.append(f"{dirpath}: {e}")

        self.status.config(text=_t("status_empty_done", n=deleted))
        if errors:
            messagebox.showerror(
                _t("msg_del_errors"),
                _t("msg_del_summary",
                   deleted=deleted, errors=len(errors),
                   tail="\n".join(errors[:10])))
        else:
            messagebox.showinfo(_t("msg_done"),
                                _t("msg_empty_done", n=deleted))

        # Цикл завершён. Если ещё остались группы дубликатов —
        # вернуть фокус на «Удалить все дубликаты».
        self.btn_del_empty.disable()
        has_dups = bool(self.groups) and any(
            len(p) >= 2 for p in self.groups.values())
        if has_dups:
            self.btn_del_dups.enable()
            self._highlight_step("delete_dups")
        else:
            self._highlight_step("folder")


def main():
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    root = tk.Tk()
    app = DuplicateFinderApp(root)

    def _on_close():
        app._close_cache()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", _on_close)
    root.mainloop()


if __name__ == "__main__":
    main()