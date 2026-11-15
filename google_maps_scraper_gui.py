import ctypes
import hashlib
import json
import os
import platform
import re
import sys
import urllib.parse
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import time
import logging
from dataclasses import dataclass, replace
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import schedule

__version__ = "1.3.5"
PUBLISHER = "Pashalis Laoutaris"
COPYRIGHT = f"Copyright (c) 2025 {PUBLISHER}"

# The whole UI sticks to exactly two accent colors: PRIMARY marks section
# headers/progress so related options are easy to scan, ACCENT marks the one
# primary action (Start).
COLOR_PRIMARY = "#2F6FA5"
COLOR_ACCENT = "#D9720C"

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def resource_path(name):
    """Bundled files sit next to this script, or in PyInstaller's unpack folder in the Windows build"""
    return os.path.join(getattr(sys, "_MEIPASS", APP_DIR), name)


def documents_folder():
    """The user's real Documents folder, including when OneDrive has moved it"""
    try:
        buffer = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:  # 5 = CSIDL_PERSONAL
            return buffer.value
    except (AttributeError, OSError):
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def prepare_output_folder():
    """The installed app sits in a folder users can't write to, so the Windows build saves
    results (any file with a relative name) in Documents\\AGMS instead"""
    if not getattr(sys, "frozen", False):
        return
    folder = os.path.join(documents_folder(), "AGMS")
    os.makedirs(folder, exist_ok=True)
    os.chdir(folder)


def set_windows_app_id():
    """Give the process its own taskbar identity, so Windows shows our icon instead of python.exe's"""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("powergr.agms.gui")
        except (AttributeError, OSError):
            pass


def enable_dpi_awareness():
    """Without this, Windows stretches the whole window and the taskbar icon on screens set above 100%,
    which is what made the text and the icon look soft."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 1 = system DPI aware
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


DEFAULT_OUTPUT_NAME = "google_maps_results"
FILE_EXTENSIONS = (".csv", ".json", ".xlsx")
INVALID_NAME_CHARACTERS = '*?"<>|'


def name_from_query(query):
    """A file name made from a search query: 'Coffee shops!' becomes 'coffee_shops'"""
    name = "_".join(re.findall(r"\w+", query.lower()))[:60].strip("_")
    return name or DEFAULT_OUTPUT_NAME


def clean_output_name(typed, query):
    """The file name a job saves under, without an extension. An empty box falls back to a name made
    from the query. Raises ValueError for characters Windows does not allow in file names."""
    name = typed.strip()
    for extension in FILE_EXTENSIONS:
        if name.lower().endswith(extension):
            name = name[:-len(extension)].strip()  # people type hotels.csv, which would save hotels.csv.csv
            break
    if not name:
        return name_from_query(query)
    bad = sorted({character for character in name if character in INVALID_NAME_CHARACTERS})
    if bad:
        raise ValueError(f"A file name cannot contain these characters: {' '.join(bad)}")
    return name


def same_file(first, second):
    return os.path.normcase(os.path.abspath(first)) == os.path.normcase(os.path.abspath(second))


def strip_secrets(value):
    """Drop passwords from a proxy URL or database connection string, so they are never written to disk"""
    if '://' in value:
        parts = urllib.parse.urlsplit(value)
        netloc = parts.netloc
        if '@' in netloc:
            userinfo, _, host = netloc.rpartition('@')
            netloc = userinfo.split(':', 1)[0] + '@' + host
        query = urllib.parse.urlencode(
            [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k.lower() != 'password'])
        return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, query, parts.fragment))
    return re.sub(r"(?i)\bpassword\s*=\s*('[^']*'|\S*)\s*", "", value).strip()


FORMAT_EXTENSIONS = {"csv": ".csv", "json": ".json", "excel": ".xlsx"}
SETTINGS_EXTENSION = ".agms"
SETTINGS_VERSION = 2
MAX_SETTINGS_BYTES = 1_000_000  # a real settings file is a few KB: anything bigger is some other file
ON_EXISTING_CHOICES = ("number", "overwrite", "skip")


def output_paths(folder, name, formats):
    """The result files one job writes: its name plus the extension of each chosen format"""
    base = os.path.join(folder, name) if folder else name
    return [base + FORMAT_EXTENSIONS[kind] for kind in formats if kind in FORMAT_EXTENSIONS]


def existing_files(folder, name, formats):
    return [path for path in output_paths(folder, name, formats) if os.path.exists(path)]


def pick_output_name(folder, name, formats, policy):
    """Decide what a job saves under when its files may already exist.
    policy 'overwrite' keeps the name, 'skip' returns None, and 'number' finds the first free
    name_2, name_3 and so on. Every format of one job gets the same number, so a set stays together."""
    if not existing_files(folder, name, formats) or policy == "overwrite":
        return name
    if policy == "skip":
        return None
    for number in range(2, 10000):
        candidate = f"{name}_{number}"
        if not existing_files(folder, candidate, formats):
            return candidate
    raise RuntimeError(f"Could not find a free file name for '{name}'")


def config_folder():
    """Where AGMS keeps its own small files (preferences and the last session). AGMS_CONFIG_DIR overrides it."""
    override = os.environ.get("AGMS_CONFIG_DIR")
    if override:
        return override
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "AGMS")
    return os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "agms")


def write_text_safely(path, text):
    """Write a file so a crash or a full disk cannot leave half of it behind"""
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(temporary, path)


BOOLEAN_SETTINGS = ("csv", "json", "excel", "add_timestamp", "headless", "test_mode", "verbose")
NUMBER_SETTINGS = {"limit": (0, 100), "max_results": (0, 1000), "delay": (0, 60), "workers": (1, 20)}
TEXT_SETTINGS = ("query", "save_as", "cities_text", "cities_file", "folder", "sqlite", "postgres", "proxy")
CHOICE_SETTINGS = {"cities_mode": ("list", "file"), "schedule": ("None", "daily", "hourly", "weekly")}
OLD_TEXT_KEYS = {"query": "query", "output": "save_as", "export_csv": "csv", "export_json": "json",
                 "export_excel": "excel", "headless": "headless", "test": "test_mode", "verbose": "verbose",
                 "limit": "limit", "delay": "delay", "max_results": "max_results", "workers": "workers",
                 "proxy": "proxy", "schedule": "schedule", "sqlite": "sqlite", "postgres": "postgres"}


def normalize_settings(raw):
    """Check every value of a settings file and keep the good ones. Returns (settings, notes), where
    settings holds only valid values for known names (so a partial file changes only what it contains)
    and notes lists what was ignored and why."""
    clean, notes = {}, []
    for key in BOOLEAN_SETTINGS:
        if key in raw:
            value = raw[key]
            if isinstance(value, str) and value.strip().lower() in ("true", "false"):
                value = value.strip().lower() == "true"
            if isinstance(value, bool):
                clean[key] = value
            else:
                notes.append(f"Ignored '{key}': it should be true or false.")
    for key, (low, high) in NUMBER_SETTINGS.items():
        if key in raw:
            value = raw[key]
            try:
                if isinstance(value, bool) or (isinstance(value, float) and value != int(value)):
                    raise ValueError
                clean[key] = max(low, min(high, int(str(value).strip())))
            except (ValueError, TypeError):
                notes.append(f"Ignored '{key}': it should be a whole number.")
    for key in TEXT_SETTINGS:
        if key in raw:
            if isinstance(raw[key], str):
                clean[key] = raw[key]
            else:
                notes.append(f"Ignored '{key}': it should be text.")
    for key, allowed in CHOICE_SETTINGS.items():
        if key in raw:
            if raw[key] in allowed:
                clean[key] = raw[key]
            else:
                notes.append(f"Ignored '{key}': {raw[key]!r} is not one of {', '.join(allowed)}.")
    if "queue" in raw:
        if isinstance(raw["queue"], list):
            clean["queue"] = []
            for item in raw["queue"]:
                if isinstance(item, dict) and isinstance(item.get("query"), str):
                    save_as = item.get("save_as", "")
                    clean["queue"].append({"query": item["query"], "save_as": save_as if isinstance(save_as, str) else ""})
                else:
                    notes.append("Ignored a queue entry that has no search query.")
        else:
            notes.append("Ignored 'queue': it should be a list.")
    return clean, notes


def parse_settings_text(text):
    """Read a settings file: the current JSON format, or the older key=value text format.
    Returns (settings, notes). Raises ValueError, with a message safe to show, if this is not a settings file."""
    stripped = text.lstrip("﻿ \t\r\n")
    notes = []
    if stripped.startswith("{"):
        try:
            raw = json.loads(stripped)
        except ValueError:
            raise ValueError("This file is damaged, so it cannot be read as AGMS settings.")
        if not isinstance(raw, dict) or not isinstance(raw.get("agms_settings"), int):
            raise ValueError("This is not an AGMS settings file. Results files cannot be loaded here.")
        if raw["agms_settings"] > SETTINGS_VERSION:
            notes.append("This file was saved by a newer AGMS. Settings this version does not know are ignored.")
    else:
        raw, queue = {}, []
        for line in stripped.splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)  # split once: a Postgres URL contains '=' itself
            if key == "job":
                query, _, name = value.partition("\t")
                queue.append({"query": query, "save_as": name})
            elif key == "format":
                raw["csv"], raw["json"] = value in ("csv", "both"), value in ("json", "both")
            elif key in OLD_TEXT_KEYS:
                raw[OLD_TEXT_KEYS[key]] = value
        if queue:
            raw["queue"] = queue
        if not raw:
            raise ValueError("This does not look like an AGMS settings file.")
        notes.append("This is an older settings file. It had no cities, so your cities were not changed.")
    clean, problems = normalize_settings(raw)
    return clean, notes + problems


MAX_RECENT = 6


def prefs_path():
    return os.path.join(config_folder(), "preferences.json")


def session_path():
    return os.path.join(config_folder(), "last_session" + SETTINGS_EXTENSION)


def load_prefs():
    """Small app preferences: whether to remember the last session, and the recent settings files"""
    prefs = {"remember_session": True, "recent": []}
    try:
        with open(prefs_path(), "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            if isinstance(saved.get("remember_session"), bool):
                prefs["remember_session"] = saved["remember_session"]
            if isinstance(saved.get("recent"), list):
                prefs["recent"] = [p for p in saved["recent"] if isinstance(p, str)][:MAX_RECENT]
    except (OSError, ValueError):
        pass  # no file yet, or an unreadable one: the defaults are fine
    return prefs


def save_prefs(prefs):
    try:
        write_text_safely(prefs_path(), json.dumps(prefs, indent=2))
    except OSError:
        pass  # a read-only profile must not stop the app


CHECKPOINT_VERSION = 1
CHECKPOINT_KEEP_DAYS = 30


def progress_folder():
    return os.path.join(config_folder(), "progress")


def effective_cities(cities, limit, test_mode):
    """The cities a run really uses after Limit Cities and Test Mode are applied"""
    cities = list(cities)
    if limit > 0:
        cities = cities[:limit]
    if test_mode:
        cities = cities[:1]
    return cities


def job_key(query, cities, max_results, folder, output):
    """Names one job for progress tracking. A different query, city list, Max Results, folder or file name is
    a different job, so saved progress is never applied to a search it does not belong to."""
    raw = json.dumps([query, list(cities), max_results, os.path.normcase(os.path.abspath(folder or ".")),
                      os.path.normcase(output)], ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:24]


class Checkpoint:
    """The progress of one job, kept on disk after every finished city. If the app is stopped, closed or
    crashes, the next run can continue with the cities that are left instead of starting over.
    done: city -> its businesses, for cities that were scraped to the end.
    exported: database name -> cities already written there, so a database never gets a city twice."""

    def __init__(self, key, query, cities):
        self.key, self.query, self.cities = key, query, list(cities)
        self.name = ""      # the file name this job saves under, kept so a resume replaces its own partial files
        self.done = {}
        self.exported = {}
        self.saved_at = 0.0

    @property
    def path(self):
        return os.path.join(progress_folder(), self.key + ".json")

    @classmethod
    def load(cls, key):
        """The saved progress for this key, or None if there is none or the file cannot be trusted"""
        try:
            with open(os.path.join(progress_folder(), key + ".json"), "r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("version") != CHECKPOINT_VERSION or raw.get("key") != key:
                return None
            checkpoint = cls(key, str(raw["query"]), [str(c) for c in raw["cities"]])
            checkpoint.name = str(raw.get("name", ""))
            checkpoint.saved_at = float(raw.get("saved_at", 0))
            for city, businesses in raw["done"].items():
                if not isinstance(businesses, list) or not all(isinstance(b, dict) for b in businesses):
                    return None
                checkpoint.done[str(city)] = businesses
            checkpoint.exported = {str(k): [str(c) for c in v] for k, v in raw.get("exported", {}).items()}
            return checkpoint
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    @classmethod
    def discard(cls, key):
        try:
            os.remove(os.path.join(progress_folder(), key + ".json"))
        except OSError:
            pass

    def mark_done(self, city, businesses):
        self.done[city] = list(businesses)

    def results(self, cities=None):
        """Every saved business, in the order of the city list"""
        order = cities if cities is not None else self.cities
        return [business for city in dict.fromkeys(order) if city in self.done for business in self.done[city]]

    def save(self):
        """Write the progress. Returns False when the disk refuses, which only costs the ability to resume."""
        self.saved_at = time.time()
        data = {"version": CHECKPOINT_VERSION, "key": self.key, "query": self.query, "cities": self.cities,
                "name": self.name, "saved_at": self.saved_at, "done": self.done, "exported": self.exported}
        try:
            write_text_safely(self.path, json.dumps(data, ensure_ascii=False))
            return True
        except (OSError, TypeError, ValueError):
            return False

    def delete(self):
        self.discard(self.key)


def prune_progress():
    """Remove progress files that are old or unreadable, so the folder cannot grow forever"""
    try:
        names = os.listdir(progress_folder())
    except OSError:
        return
    limit = time.time() - CHECKPOINT_KEEP_DAYS * 86400
    for name in names:
        path = os.path.join(progress_folder(), name)
        try:
            stale = os.path.getmtime(path) < limit or (name.endswith(".json") and Checkpoint.load(name[:-5]) is None)
            if stale or name.endswith(".tmp"):
                os.remove(path)
        except OSError:
            pass


@dataclass(frozen=True)
class Job:
    """One search in the queue: what to look for, and the file name its results are saved under"""
    query: str
    output: str


@dataclass(frozen=True)
class JobResult:
    job: Job
    found: int
    failed_exports: tuple
    cancelled: bool
    icon: str
    text: str
    skipped: bool = False  # not run, because its files already existed and the user chose to skip such jobs
    saved_as: str = ""     # the file name actually used, which may differ from job.output


@dataclass(frozen=True)
class ScrapeSettings:
    """Every option, read once on the main thread when Start is pressed"""
    jobs: tuple
    queued: bool  # True when the jobs are the rows of the queue list, so their status can be shown there
    cities: tuple
    formats: tuple
    sqlite_db: str
    postgres_conn: str
    headless: bool
    verbose: bool
    test_mode: bool
    limit: int
    max_results: int
    delay: int
    workers: int
    proxy: str
    schedule_mode: str
    folder: str = ""             # where result files go; empty means the current folder
    timestamp: bool = False      # add the date and time to every file name
    on_existing: str = "number"  # what to do when a result file already exists: number, overwrite or skip
    resume: frozenset = frozenset()  # keys of the jobs that continue from saved progress (see job_key)


class LogHandler(logging.Handler):
    """Custom logging handler for GUI"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def handle(self, record):
        """Skip logging's per-handler lock. From a worker thread, emit() waits for the Tk main thread to take
        the line. If the main thread logs at that moment (pressing Stop does), it waits for the lock the worker
        holds, and the window freezes for good. Nothing here needs the lock, because Tk runs one call at a time."""
        if self.filter(record):
            self.emit(record)
            return True
        return False

    def emit(self, record):
        if self.text_widget is None:
            return

        try:
            log_entry = self.format(record)
            tag = self._tag_for(record.levelno)
            # Marshal onto the Tk mainloop thread: this handler can be invoked
            # from worker threads (multi-threaded scraping), and Tkinter widgets
            # must only be touched from the main thread.
            self.text_widget.after(0, self._append, log_entry, tag)
        except Exception:
            pass

    @staticmethod
    def _tag_for(levelno):
        if levelno >= logging.ERROR:
            return 'error'
        if levelno >= logging.WARNING:
            return 'warning'
        if levelno >= logging.INFO:
            return 'info'
        return 'debug'

    def _append(self, log_entry, tag):
        try:
            self.text_widget.config(state=tk.NORMAL)
            self.text_widget.insert(tk.END, log_entry + '\n', tag)
            self.text_widget.see(tk.END)
            self.text_widget.config(state=tk.DISABLED)
        except Exception:
            pass

class GoogleMapsScraperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Another Google Maps Scraper (AGMS) v{__version__}")
        self._set_window_icon()
        self.root.geometry("1200x800")
        self._maximize_window()

        self.scraping = False
        self.logger = None
        self.gui_handler = None
        self.stop_event = threading.Event()
        self.worker_thread = None
        self._active_scrapers = set()
        self._scrapers_lock = threading.Lock()
        self.jobs = []          # the queue: Job objects, in run order
        self.job_status = []    # one status text per job, shown in the queue list
        self._help_window = None
        self._progress_base, self._progress_span, self._job_label = 0, 1, ""
        self.prefs = load_prefs()
        prune_progress()

        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.logger.info(f"Files with a relative name are saved in: {os.getcwd()}")
        self._restore_session()
        self.root.after(250, lambda: self._apply_exact_icons(self.root))  # after Tk has mapped the window

    def _set_window_icon(self):
        try:
            self.root.iconbitmap(resource_path("icon.ico"))
        except tk.TclError as e:
            print(f"Could not load icon: {e}")

    def _apply_exact_icons(self, window):
        """Tk's iconbitmap always loads 32 and 16 pixel icons. On a screen scaled above 100% Windows asks
        for larger ones (40 and 20 at 125%), so hand it the matching frames of icon.ico directly."""
        if sys.platform != "win32":
            return
        try:
            if not window.winfo_exists():
                return
            user32 = ctypes.WinDLL("user32")  # a private copy, so these prototypes touch nothing else
            user32.GetAncestor.restype = ctypes.c_void_p
            user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            user32.SendMessageW.restype = ctypes.c_void_p
            user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
            hwnd = user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT: the frame Windows draws
            for which, metric in ((1, 11), (0, 49)):  # ICON_BIG with SM_CXICON, ICON_SMALL with SM_CXSMICON
                size = user32.GetSystemMetrics(metric)
                icon = user32.LoadImageW(None, resource_path("icon.ico"), 1, size, size, 0x10)  # IMAGE_ICON, LR_LOADFROMFILE
                if icon:
                    user32.SendMessageW(hwnd, 0x0080, which, icon)  # WM_SETICON
        except (AttributeError, OSError, tk.TclError):
            pass

    def _maximize_window(self):
        """Maximize on launch. 'zoomed' is a Windows and macOS state, so X11 gets the -zoomed attribute."""
        try:
            self.root.state('zoomed')
        except tk.TclError:
            try:
                self.root.attributes('-zoomed', True)
            except tk.TclError:
                pass  # this window manager can't maximize: keep the 1200x800 default

    def setup_logging(self):
        """Setup logging for GUI - called AFTER log_text exists"""
        self.logger = logging.getLogger('google_maps_gui')
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False  # Don't propagate to root logger

        # Clear any existing handlers
        self.logger.handlers.clear()

        # Format
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

        # GUI handler - now log_text exists
        self.gui_handler = LogHandler(self.log_text)
        self.gui_handler.setFormatter(formatter)
        self.logger.addHandler(self.gui_handler)

        # Also route the shared scraper module's own logger into this panel,
        # so the detailed per-business logs it emits show up here too.
        scraper_logger = logging.getLogger('google_maps_scraper')
        scraper_logger.addHandler(self.gui_handler)
        scraper_logger.setLevel(logging.DEBUG if self.verbose_var.get() else logging.INFO)

    def create_ui(self):
        """Create the GUI interface: a single screen, no tabs"""
        self._setup_styles()
        self.create_menu()

        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)

        self.create_options_panel(main_frame)

        # Control Panel
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 10))

        # Progress Bar
        progress_frame = ttk.Frame(control_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(progress_frame, text="Progress:").pack(side=tk.LEFT, padx=5)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100,
                                             style="Accent.Horizontal.TProgressbar")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        self.progress_label = ttk.Label(progress_frame, text="0%")
        self.progress_label.pack(side=tk.LEFT, padx=5)

        # Buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill=tk.X)

        self.start_button = ttk.Button(button_frame, text="▶ Start Scraping", command=self.start_scraping,
                                        style="Accent.TButton")
        self.start_button.pack(side=tk.LEFT, padx=5)

        self.stop_button = ttk.Button(button_frame, text="⏹ Stop", command=self.stop_scraping, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)

        ttk.Button(button_frame, text="📋 Clear Form", command=self.clear_form).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="📁 Save Settings", command=self.save_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="📂 Load Settings", command=self.load_settings).pack(side=tk.LEFT, padx=5)

        log_button_frame = ttk.Frame(button_frame)
        log_button_frame.pack(side=tk.RIGHT, padx=5)

        ttk.Button(log_button_frame, text="🗑️ Clear Logs", command=self.clear_logs).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_button_frame, text="📋 Copy Logs", command=self.copy_logs).pack(side=tk.LEFT, padx=2)
        ttk.Button(log_button_frame, text="💾 Save Logs", command=self.save_logs).pack(side=tk.LEFT, padx=2)

        # Logs
        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = scrolledtext.ScrolledText(log_frame, height=12, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)

        # Configure text tags for colors
        self.log_text.tag_config('info', foreground='black')
        self.log_text.tag_config('warning', foreground='orange')
        self.log_text.tag_config('error', foreground='red')
        self.log_text.tag_config('debug', foreground='gray')

        # Now that log_text and every settings var exist, wire up logging
        self.setup_logging()

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)

    def _setup_styles(self):
        """Two-color theme: PRIMARY for section headers/progress, ACCENT for the one primary action button"""
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass  # fall back to whatever theme is already active

        style.configure("Accent.TButton", background=COLOR_ACCENT, foreground="white",
                         font=("Segoe UI", 10, "bold"), padding=6)
        style.map("Accent.TButton",
                  background=[("active", COLOR_ACCENT), ("disabled", "#cccccc")],
                  foreground=[("disabled", "#888888")])

        style.configure("Accent.Horizontal.TProgressbar", background=COLOR_PRIMARY, troughcolor="#e6e6e6")
        style.configure("Treeview", rowheight=self._px(24))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.map("Treeview", background=[("selected", COLOR_PRIMARY)], foreground=[("selected", "white")])

    def _add_card(self, parent, title):
        """A bordered card with a colored header bar. Returns (card_frame, body_frame);
        the caller places card_frame and builds its contents inside body_frame."""
        card = tk.Frame(parent, bg=COLOR_PRIMARY, bd=0)
        header = tk.Label(card, text=title, bg=COLOR_PRIMARY, fg="white",
                           font=("Segoe UI", 10, "bold"), anchor="w", padx=10, pady=6)
        header.pack(fill=tk.X)

        body = ttk.Frame(card, padding=10)
        body.pack(fill=tk.BOTH, expand=True, padx=1, pady=(0, 1))
        return card, body

    def create_options_panel(self, parent):
        """All scraping options on one screen, grouped into color-coded cards"""
        panel = ttk.Frame(parent)
        panel.pack(fill=tk.X, pady=(0, 10))
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)

        search_card, search_body = self._add_card(panel, "🔍 Search & Cities")
        search_card.grid(row=0, column=0, columnspan=2, sticky=tk.EW, pady=(0, 8))
        self._build_search_section(search_body)

        output_card, output_body = self._add_card(panel, "💾 Output & Export")
        output_card.grid(row=1, column=0, sticky=tk.NSEW, padx=(0, 4))
        self._build_output_section(output_body)

        scraping_card, scraping_body = self._add_card(panel, "⚙️ Scraping Options")
        scraping_card.grid(row=1, column=1, sticky=tk.NSEW, padx=(4, 0))
        self._build_scraping_section(scraping_body)

    def _build_search_section(self, frame):
        """Query, file name and cities on the left, the job queue on the right"""
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(0, weight=1)

        left = ttk.Frame(frame)
        left.grid(row=0, column=0, sticky=tk.NSEW, padx=(0, 12))
        left.columnconfigure(1, weight=1)

        ttk.Label(left, text="Search Query:").grid(row=0, column=0, sticky=tk.W, pady=5)
        query_row = ttk.Frame(left)
        query_row.grid(row=0, column=1, sticky=tk.EW, pady=5, padx=5)
        query_row.columnconfigure(0, weight=3)
        query_row.columnconfigure(2, weight=2)
        self.query_var = tk.StringVar(value="technology companies")
        self.query_entry = ttk.Entry(query_row, textvariable=self.query_var)
        self.query_entry.grid(row=0, column=0, sticky=tk.EW)
        ttk.Label(query_row, text="Save as:").grid(row=0, column=1, padx=(10, 5))
        self.output_var = tk.StringVar(value="")
        output_entry = ttk.Entry(query_row, textvariable=self.output_var)
        output_entry.grid(row=0, column=2, sticky=tk.EW)
        self.add_job_button = ttk.Button(query_row, text="Add to queue", command=self.add_job)
        self.add_job_button.grid(row=0, column=3, padx=(10, 0))
        for entry in (self.query_entry, output_entry):
            entry.bind("<Return>", lambda event: self.add_job())

        ttk.Label(left, text="Cities Input:").grid(row=1, column=0, sticky=tk.W, pady=5)
        input_frame = ttk.Frame(left)
        input_frame.grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)

        self.cities_mode_var = tk.StringVar(value="list")
        ttk.Radiobutton(input_frame, text="Enter Cities", variable=self.cities_mode_var,
                       value="list", command=self.update_cities_input).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(input_frame, text="From File", variable=self.cities_mode_var,
                       value="file", command=self.update_cities_input).pack(side=tk.LEFT)

        ttk.Label(left, text="Cities:").grid(row=2, column=0, sticky=tk.NW, pady=5)
        cities_frame = ttk.Frame(left)
        cities_frame.grid(row=2, column=1, sticky=tk.EW, pady=5, padx=5)

        self.cities_text = tk.Text(cities_frame, height=3, width=40)
        self.cities_text.pack(fill=tk.BOTH, expand=True)
        self.cities_text.insert(tk.END, "New York, USA\nLondon, UK\nTokyo, Japan\nParis, France")

        self.cities_file_var = tk.StringVar(value="")
        self.cities_file_entry = ttk.Entry(cities_frame, textvariable=self.cities_file_var)
        ttk.Button(cities_frame, text="Browse...", command=self.browse_cities_file).pack(pady=5)

        right = ttk.Frame(frame)
        right.grid(row=0, column=1, sticky=tk.NSEW)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.queue_label = ttk.Label(right, text="", font=("Segoe UI", 9, "bold"))
        self.queue_label.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(5, 3))

        self.queue_tree = ttk.Treeview(right, columns=("query", "file", "status"), show="headings",
                                       height=4, selectmode="browse")
        for column, title, width, stretch in (("query", "Search query", 170, True),
                                              ("file", "Saves as", 140, True),
                                              ("status", "Status", 110, False)):
            self.queue_tree.heading(column, text=title, anchor=tk.W)
            self.queue_tree.column(column, width=self._px(width), minwidth=self._px(60), anchor=tk.W, stretch=stretch)
        self.queue_tree.grid(row=1, column=0, sticky=tk.NSEW)
        queue_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.queue_tree.yview)
        queue_scroll.grid(row=1, column=1, sticky=tk.NS)
        self.queue_tree.configure(yscrollcommand=queue_scroll.set)
        self.queue_tree.bind("<Delete>", lambda event: self.remove_job())
        self.queue_tree.bind("<Double-1>", self.edit_job)

        buttons = ttk.Frame(right)
        buttons.grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))
        self.move_up_button = ttk.Button(buttons, text="Up", width=6, command=lambda: self.move_job(-1))
        self.move_down_button = ttk.Button(buttons, text="Down", width=6, command=lambda: self.move_job(1))
        self.remove_job_button = ttk.Button(buttons, text="Remove", width=9, command=self.remove_job)
        self.clear_queue_button = ttk.Button(buttons, text="Clear", width=7, command=self.clear_queue)
        for button in (self.move_up_button, self.move_down_button, self.remove_job_button, self.clear_queue_button):
            button.pack(side=tk.LEFT, padx=(0, 6))
        self._refresh_queue()

    def _make_job(self, query_text, name_text, existing):
        """Check one query and file name. Raises ValueError with a message safe to show the user."""
        query = " ".join(query_text.split())  # one line, single spaces
        if not query:
            raise ValueError("Please enter a search query.")
        output = clean_output_name(name_text, query)
        for other in existing:
            if same_file(other.output, output):
                raise ValueError(f"Another job already saves as '{other.output}'. Type a different name in Save as.")
        return Job(query, output)

    def _refresh_queue(self, select=None):
        self.queue_tree.delete(*self.queue_tree.get_children())
        for index, job in enumerate(self.jobs):
            self.queue_tree.insert("", tk.END, iid=str(index), values=(job.query, job.output, self.job_status[index]))
        if select is not None and 0 <= select < len(self.jobs):
            self.queue_tree.selection_set(str(select))
            self.queue_tree.see(str(select))
        count = len(self.jobs)
        self.queue_label.config(text=f"Job queue ({count})" if count else "Job queue (empty: Start runs the query in the box)")

    def _set_job_status(self, index, status):
        if index < len(self.job_status):
            self.job_status[index] = status
            if self.queue_tree.exists(str(index)):
                self.queue_tree.set(str(index), "status", status)

    def _set_queue_editable(self, editable):
        state = tk.NORMAL if editable else tk.DISABLED
        for button in (self.add_job_button, self.move_up_button, self.move_down_button,
                       self.remove_job_button, self.clear_queue_button):
            button.config(state=state)

    def _selected_job_index(self):
        selection = self.queue_tree.selection()
        return int(selection[0]) if selection else None

    def add_job(self):
        if self.scraping:
            return
        try:
            job = self._make_job(self.query_var.get(), self.output_var.get(), self.jobs)
        except ValueError as e:
            messagebox.showerror("Cannot add job", str(e))
            return
        self.jobs.append(job)
        self.job_status.append("Waiting")
        self.query_var.set("")
        self.output_var.set("")
        self._refresh_queue(select=len(self.jobs) - 1)
        self.query_entry.focus_set()

    def move_job(self, step):
        index = self._selected_job_index()
        if self.scraping or index is None or not 0 <= index + step < len(self.jobs):
            return
        for items in (self.jobs, self.job_status):
            items[index], items[index + step] = items[index + step], items[index]
        self._refresh_queue(select=index + step)

    def remove_job(self):
        index = self._selected_job_index()
        if self.scraping or index is None:
            return
        del self.jobs[index]
        del self.job_status[index]
        self._refresh_queue(select=min(index, len(self.jobs) - 1))

    def clear_queue(self):
        if self.scraping:
            return
        self.jobs.clear()
        self.job_status.clear()
        self._refresh_queue()

    def edit_job(self, event):
        """Double-click: take the job out of the queue and put it back in the boxes to change it"""
        row = self.queue_tree.identify_row(event.y)
        if self.scraping or not row:
            return
        index = int(row)
        job = self.jobs[index]
        del self.jobs[index]
        del self.job_status[index]
        self.query_var.set(job.query)
        self.output_var.set(job.output)
        self._refresh_queue(select=min(index, len(self.jobs) - 1))
        self.query_entry.focus_set()

    def _build_output_section(self, frame):
        """Export formats and optional database export. File names come from each job's Save as box."""
        frame.columnconfigure(1, weight=1)

        # Independent checkboxes so any combination can be exported at once,
        # matching the CLI's `--format csv json excel` style
        ttk.Label(frame, text="Format:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.export_csv_var = tk.BooleanVar(value=True)
        self.export_json_var = tk.BooleanVar(value=True)
        self.export_excel_var = tk.BooleanVar(value=False)
        format_frame = ttk.Frame(frame)
        format_frame.grid(row=0, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Checkbutton(format_frame, text="CSV", variable=self.export_csv_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(format_frame, text="JSON", variable=self.export_json_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(format_frame, text="Excel", variable=self.export_excel_var).pack(side=tk.LEFT)

        ttk.Label(frame, text="Save in folder:").grid(row=1, column=0, sticky=tk.W, pady=5)
        folder_row = ttk.Frame(frame)
        folder_row.grid(row=1, column=1, sticky=tk.EW, pady=5, padx=5)
        folder_row.columnconfigure(0, weight=1)
        self.folder_var = tk.StringVar(value="")
        ttk.Entry(folder_row, textvariable=self.folder_var).grid(row=0, column=0, sticky=tk.EW)
        ttk.Button(folder_row, text="Browse...", command=self.browse_output_folder).grid(row=0, column=1, padx=(5, 0))

        self.timestamp_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Add the date and time to file names", variable=self.timestamp_var).grid(
            row=2, column=1, sticky=tk.W, padx=5)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=3, column=0, columnspan=2, sticky=tk.EW, pady=8)
        ttk.Label(frame, text="Database export (optional)", font=("Segoe UI", 9, "bold")).grid(
            row=4, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(frame, text="SQLite DB file:").grid(row=5, column=0, sticky=tk.W, pady=5)
        sqlite_row = ttk.Frame(frame)
        sqlite_row.grid(row=5, column=1, sticky=tk.EW, pady=5, padx=5)
        sqlite_row.columnconfigure(0, weight=1)
        self.sqlite_var = tk.StringVar(value="")
        ttk.Entry(sqlite_row, textvariable=self.sqlite_var).grid(row=0, column=0, sticky=tk.EW)
        ttk.Button(sqlite_row, text="Browse...", command=self.browse_sqlite_file).grid(row=0, column=1, padx=(5, 0))

        ttk.Label(frame, text="PostgreSQL connection string:").grid(row=6, column=0, sticky=tk.W, pady=5)
        self.postgres_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.postgres_var).grid(row=6, column=1, sticky=tk.EW, pady=5, padx=5)

    def _build_scraping_section(self, frame):
        """Scraping behavior, performance, proxy, and scheduling"""
        frame.columnconfigure(1, weight=1)

        toggles = ttk.Frame(frame)
        toggles.grid(row=0, column=0, columnspan=2, sticky=tk.W, pady=(0, 10))
        self.headless_var = tk.BooleanVar(value=True)
        self.test_var = tk.BooleanVar(value=False)
        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(toggles, text="Headless", variable=self.headless_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Test Mode", variable=self.test_var).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Checkbutton(toggles, text="Verbose Logging", variable=self.verbose_var).pack(side=tk.LEFT)

        ttk.Label(frame, text="Limit Cities (0 = no limit):").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.limit_var = tk.IntVar(value=0)
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self.limit_var, width=10).grid(
            row=1, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Label(frame, text="Max Results Per City (0 = no limit):").grid(row=2, column=0, sticky=tk.W, pady=5)
        self.max_results_var = tk.IntVar(value=0)
        ttk.Spinbox(frame, from_=0, to=1000, textvariable=self.max_results_var, width=10).grid(
            row=2, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Label(frame, text="Delay Between Cities (seconds):").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.delay_var = tk.IntVar(value=10)
        delay_frame = ttk.Frame(frame)
        delay_frame.grid(row=3, column=1, sticky=tk.EW, pady=5, padx=5)
        delay_frame.columnconfigure(0, weight=1)
        self.delay_scale = ttk.Scale(delay_frame, from_=1, to=60, orient=tk.HORIZONTAL,
                                     variable=self.delay_var, command=self.update_delay_label)
        self.delay_scale.grid(row=0, column=0, sticky=tk.EW)
        self.delay_label = ttk.Label(delay_frame, text="10s", width=4)
        self.delay_label.grid(row=0, column=1, padx=(5, 0))

        ttk.Label(frame, text="Parallel Workers:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.workers_var = tk.IntVar(value=1)
        ttk.Spinbox(frame, from_=1, to=20, textvariable=self.workers_var, width=10).grid(
            row=4, column=1, sticky=tk.W, pady=5, padx=5)

        ttk.Label(frame, text="Proxy Server (http://ip:port):").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.proxy_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.proxy_var).grid(row=5, column=1, sticky=tk.EW, pady=5, padx=5)

        ttk.Label(frame, text="Repeat Schedule:").grid(row=6, column=0, sticky=tk.W, pady=5)
        self.schedule_var = tk.StringVar(value="None")
        ttk.Combobox(frame, textvariable=self.schedule_var, values=["None", "daily", "hourly", "weekly"],
                     state="readonly", width=12).grid(row=6, column=1, sticky=tk.W, pady=5, padx=5)

    def update_delay_label(self, value):
        """Update delay label"""
        self.delay_label.config(text=f"{int(float(value))}s")

    def update_cities_input(self):
        """Toggle between text area and file input"""
        if self.cities_mode_var.get() == "list":
            self.cities_file_entry.pack_forget()
            self.cities_text.pack(fill=tk.BOTH, expand=True)
        else:
            self.cities_text.pack_forget()
            self.cities_file_entry.pack(fill=tk.X, pady=5)

    def browse_cities_file(self):
        """Browse for cities file"""
        filename = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("Markdown files", "*.md"), ("All files", "*.*")])
        if filename:
            self.cities_file_var.set(filename)

    def browse_sqlite_file(self):
        """Browse for (or choose a new) SQLite database file"""
        filename = filedialog.asksaveasfilename(defaultextension=".db", filetypes=[("SQLite DB", "*.db"), ("All files", "*.*")])
        if filename:
            self.sqlite_var.set(filename)

    def browse_output_folder(self):
        """Pick the folder the result files are saved in"""
        folder = filedialog.askdirectory(initialdir=self._results_folder() if os.path.isdir(self._results_folder()) else None)
        if folder:
            self.folder_var.set(os.path.normpath(folder))

    def clear_form(self):
        """Clear all form fields"""
        self.query_var.set("")
        self.cities_text.delete(1.0, tk.END)
        self.output_var.set("")
        self.folder_var.set("")
        self.timestamp_var.set(False)
        if not self.scraping:
            self.jobs.clear()
            self.job_status.clear()
            self._refresh_queue()
        self.export_csv_var.set(True)
        self.export_json_var.set(True)
        self.export_excel_var.set(False)
        self.headless_var.set(True)
        self.test_var.set(False)
        self.verbose_var.set(False)
        self.limit_var.set(0)
        self.delay_var.set(10)
        self.max_results_var.set(0)
        self.workers_var.set(1)
        self.proxy_var.set("")
        self.schedule_var.set("None")
        self.sqlite_var.set("")
        self.postgres_var.set("")
        self.cities_file_var.set("")
        self.logger.info("Form cleared")

    # ---- settings: save, load, recent files, and the remembered last session ----

    @staticmethod
    def _int_of(var, default):
        try:
            return int(var.get())
        except (tk.TclError, ValueError):
            return default

    def _settings_snapshot(self):
        """Every option, the cities and the queue as a dict, ready to save. Passwords are removed from the
        proxy and the database string. Returns (data, passwords_were_removed)."""
        proxy = self.proxy_var.get().strip()
        postgres = self.postgres_var.get().strip()
        safe_proxy, safe_postgres = strip_secrets(proxy), strip_secrets(postgres)
        data = {
            "agms_settings": SETTINGS_VERSION,
            "saved_by": __version__,
            "query": self.query_var.get(),
            "save_as": self.output_var.get(),
            "queue": [{"query": job.query, "save_as": job.output} for job in self.jobs],
            "cities_mode": self.cities_mode_var.get(),
            "cities_text": self.cities_text.get("1.0", "end-1c"),
            "cities_file": self.cities_file_var.get(),
            "csv": self.export_csv_var.get(),
            "json": self.export_json_var.get(),
            "excel": self.export_excel_var.get(),
            "folder": self.folder_var.get(),
            "add_timestamp": self.timestamp_var.get(),
            "sqlite": self.sqlite_var.get(),
            "postgres": safe_postgres,
            "headless": self.headless_var.get(),
            "test_mode": self.test_var.get(),
            "verbose": self.verbose_var.get(),
            "limit": self._int_of(self.limit_var, 0),
            "max_results": self._int_of(self.max_results_var, 0),
            "delay": self._int_of(self.delay_var, 10),
            "workers": self._int_of(self.workers_var, 1),
            "proxy": safe_proxy,
            "schedule": self.schedule_var.get(),
        }
        return data, (safe_proxy, safe_postgres) != (proxy, postgres)

    def _apply_settings(self, clean):
        """Put already-checked settings into the form. Only the settings present in `clean` change.
        Returns notes about anything that could not be applied."""
        notes = []
        variables = {
            "query": self.query_var, "save_as": self.output_var, "cities_file": self.cities_file_var,
            "csv": self.export_csv_var, "json": self.export_json_var, "excel": self.export_excel_var,
            "folder": self.folder_var, "add_timestamp": self.timestamp_var, "sqlite": self.sqlite_var,
            "postgres": self.postgres_var, "headless": self.headless_var, "test_mode": self.test_var,
            "verbose": self.verbose_var, "limit": self.limit_var, "max_results": self.max_results_var,
            "delay": self.delay_var, "workers": self.workers_var, "proxy": self.proxy_var,
            "schedule": self.schedule_var,
        }
        for key, variable in variables.items():
            if key in clean:
                variable.set(clean[key])
        self.update_delay_label(self._int_of(self.delay_var, 10))
        if "cities_text" in clean:
            self.cities_text.delete("1.0", tk.END)
            self.cities_text.insert("1.0", clean["cities_text"])
        if "cities_mode" in clean:
            self.cities_mode_var.set(clean["cities_mode"])
            self.update_cities_input()
        if "queue" in clean:
            if self.scraping:
                notes.append("The queue was not changed, because a run is in progress.")
            else:
                jobs = []
                for entry in clean["queue"]:
                    try:
                        jobs.append(self._make_job(entry["query"], entry["save_as"], jobs))
                    except ValueError as problem:
                        notes.append(f"Skipped the queue job '{entry['query'][:40]}': {problem}")
                self.jobs[:] = jobs
                self.job_status[:] = ["Waiting"] * len(jobs)
                self._refresh_queue()
        return notes

    def save_settings(self):
        """Save the whole form to a file: search, queue, cities, output and scraping options"""
        filename = filedialog.asksaveasfilename(
            defaultextension=SETTINGS_EXTENSION, initialfile="agms_settings" + SETTINGS_EXTENSION,
            filetypes=[("AGMS settings", "*" + SETTINGS_EXTENSION), ("All files", "*.*")])
        if not filename:
            return
        try:
            data, passwords_removed = self._settings_snapshot()
            write_text_safely(filename, json.dumps(data, indent=2, ensure_ascii=False))
        except Exception as e:
            self.logger.error(f"Error saving settings: {e}")
            messagebox.showerror("Error", f"Error saving settings: {e}")
            return
        self._remember_recent(filename)
        self.logger.info(f"Settings saved to {filename}")
        jobs = len(data["queue"])
        message = ("Settings saved.\n\nThe file holds your search, "
                   f"{jobs} queued job{'' if jobs == 1 else 's'}, the cities, and every option.")
        if passwords_removed:
            message += "\n\nPasswords are never saved. Enter the proxy and database passwords again after loading."
        messagebox.showinfo("Settings saved", message)

    def load_settings(self):
        """Ask for a settings file, then load it"""
        if self.scraping:
            messagebox.showinfo("Run in progress", "Stop the run first, then load the settings.")
            return
        filename = filedialog.askopenfilename(filetypes=[
            ("AGMS settings", "*" + SETTINGS_EXTENSION), ("Older settings files", "*.txt"), ("All files", "*.*")])
        if filename:
            self.load_settings_from(filename)

    def load_settings_from(self, filename, quiet=False):
        """Read, check and apply a settings file. The whole file is checked before anything changes, so a
        bad file never leaves the form half loaded. quiet=True (the last session) shows no dialogs.
        Returns True when the settings were applied."""
        def fail(message):
            if quiet:
                self.logger.warning(f"Settings not loaded: {message}")
            else:
                self.logger.error(message)
                messagebox.showerror("Cannot load settings", message)
            return False

        if self.scraping and not quiet:
            messagebox.showinfo("Run in progress", "Stop the run first, then load the settings.")
            return False
        try:
            if os.path.getsize(filename) > MAX_SETTINGS_BYTES:
                return fail("This file is too big to be an AGMS settings file.")
            with open(filename, "r", encoding="utf-8-sig", errors="replace") as f:
                clean, notes = parse_settings_text(f.read())
        except FileNotFoundError:
            if not quiet:
                self.prefs["recent"] = [p for p in self.prefs["recent"] if not same_file(p, filename)]
                save_prefs(self.prefs)
            return fail(f"The file was not found:\n{filename}")
        except (OSError, ValueError) as e:
            return fail(str(e))
        try:
            notes += self._apply_settings(clean)
        except Exception as e:
            return fail(f"Error loading settings: {e}")

        if quiet:
            for note in notes:
                self.logger.warning(note)
            return True
        self._remember_recent(filename)
        self.logger.info(f"Settings loaded from {filename}")
        lines = ["Settings loaded."]
        if "queue" in clean:
            lines.append(f"{len(self.jobs)} job{'' if len(self.jobs) == 1 else 's'} in the queue.")
        if clean.get("cities_text", "").strip():
            count = len([c for c in clean["cities_text"].splitlines() if c.strip()])
            lines.append(f"{count} cit{'y' if count == 1 else 'ies'} in the list.")
        if notes:
            lines += ["", "Notes:"] + [f"- {note}" for note in notes]
            messagebox.showwarning("Settings loaded", "\n".join(lines))
        else:
            messagebox.showinfo("Settings loaded", "\n".join(lines))
        return True

    def _remember_recent(self, path):
        path = os.path.abspath(path)
        self.prefs["recent"] = ([path] + [p for p in self.prefs["recent"] if not same_file(p, path)])[:MAX_RECENT]
        save_prefs(self.prefs)

    def _fill_recent_menu(self):
        menu = self.recent_menu
        menu.delete(0, tk.END)
        if not self.prefs["recent"]:
            menu.add_command(label="(none yet)", state=tk.DISABLED)
            return
        for path in self.prefs["recent"]:
            menu.add_command(label=path, command=lambda path=path: self.load_settings_from(path))

    def _toggle_remember_session(self):
        self.prefs["remember_session"] = bool(self.remember_var.get())
        save_prefs(self.prefs)
        if not self.prefs["remember_session"]:
            try:
                os.remove(session_path())
            except OSError:
                pass

    def _remember_session(self):
        """Save the form when the window closes, so the next start picks up where this one stopped"""
        if not self.prefs["remember_session"]:
            return
        try:
            data, _ = self._settings_snapshot()
            write_text_safely(session_path(), json.dumps(data, indent=2, ensure_ascii=False))
        except Exception:
            pass  # closing the window must never fail because of this

    def _restore_session(self):
        path = session_path()
        if self.prefs["remember_session"] and os.path.exists(path):
            if self.load_settings_from(path, quiet=True):
                self.logger.info("Restored your last session. Clear Form starts fresh.")

    def clear_logs(self):
        """Clear log text"""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def copy_logs(self):
        """Copy logs to clipboard"""
        try:
            logs = self.log_text.get(1.0, tk.END)
            self.root.clipboard_clear()
            self.root.clipboard_append(logs)
            self.logger.info("Logs copied to clipboard")
        except Exception as e:
            self.logger.error(f"Error copying logs: {e}")

    def save_logs(self):
        """Save logs to file"""
        filename = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt"), ("Log files", "*.log")])
        if filename:
            try:
                logs = self.log_text.get(1.0, tk.END)
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(logs)
                self.logger.info(f"Logs saved to {filename}")
                messagebox.showinfo("Success", "Logs saved successfully!")
            except Exception as e:
                self.logger.error(f"Error saving logs: {e}")
                messagebox.showerror("Error", f"Error saving logs: {e}")

    def get_cities_list(self):
        """Get cities from input. Raises ValueError with a user-facing message if the cities file can't be used."""
        if self.cities_mode_var.get() == "list":
            cities_text = self.cities_text.get(1.0, tk.END)
            return [c.strip() for c in cities_text.split('\n') if c.strip()]

        filename = self.cities_file_var.get()
        if not filename:
            raise ValueError("Please select a cities file.")
        try:
            return self.load_cities_from_file(filename)
        except Exception as e:
            raise ValueError(f"Error loading cities file: {e}")

    def load_cities_from_file(self, filename):
        """Load cities from file"""
        cities = []
        with open(filename, 'r', encoding='utf-8') as file:
            content = file.read()
        lines = content.strip().split('\n')
        for line in lines:
            line = line.strip()
            if line:
                if line.startswith('OK-'):
                    city = line[3:].strip()
                elif line.startswith('- '):
                    city = line[2:].strip()
                else:
                    city = line.strip()
                if city:
                    cities.append(city)
        return cities

    def _read_int(self, var, label, minimum):
        try:
            value = int(var.get())
        except (tk.TclError, ValueError):
            raise ValueError(f"{label} must be a whole number.")
        if value < minimum:
            raise ValueError(f"{label} must be {minimum} or more.")
        return value

    def _collect_jobs(self):
        """The jobs to run: the queue, or just the query in the box when the queue is empty.
        Returns (jobs, queued), or None if the user backs out of the question below."""
        typed = " ".join(self.query_var.get().split())
        if not self.jobs:
            return (self._make_job(typed, self.output_var.get(), []),), False

        jobs = list(self.jobs)
        if typed:
            answer = messagebox.askyesnocancel(
                "Query not in the queue",
                f"The query box still contains:\n\n    {typed}\n\nIt is not in the queue yet. "
                "Add it as the last job?\n\nYes: add it and start\nNo: start without it\nCancel: go back")
            if answer is None:
                return None
            if answer:
                job = self._make_job(typed, self.output_var.get(), jobs)
                jobs.append(job)
                self.jobs.append(job)
                self.job_status.append("Waiting")
                self.query_var.set("")
                self.output_var.set("")
                self._refresh_queue()
        return tuple(jobs), True

    def _collect_settings(self):
        """Read and validate every option on the main thread, so worker threads never touch Tk variables.
        Raises ValueError with a message that is safe to show the user. Returns None if the user cancels."""
        cities = self.get_cities_list()
        if not cities:
            raise ValueError("Please enter or select cities.")

        formats = tuple(name for name, var in (("csv", self.export_csv_var), ("json", self.export_json_var),
                                               ("excel", self.export_excel_var)) if var.get())
        sqlite_db = self.sqlite_var.get().strip()
        postgres_conn = self.postgres_var.get().strip()
        if not (formats or sqlite_db or postgres_conn):
            raise ValueError("Please select at least one output format or database export.")

        limit = self._read_int(self.limit_var, "Limit Cities", 0)
        max_results = self._read_int(self.max_results_var, "Max Results Per City", 0)
        delay = self._read_int(self.delay_var, "Delay Between Cities", 0)
        workers = self._read_int(self.workers_var, "Parallel Workers", 1)

        folder = self.folder_var.get().strip()
        if folder:
            folder = os.path.abspath(os.path.expanduser(folder))
            try:
                os.makedirs(folder, exist_ok=True)
            except OSError as e:
                raise ValueError(f"The folder in Save in folder cannot be used:\n{folder}\n\n{e.strerror or e}")
        timestamp = self.timestamp_var.get()
        schedule_mode = self.schedule_var.get()

        collected = self._collect_jobs()  # asks a question when the query box still has text
        if collected is None:
            return None
        jobs, queued = collected

        # Progress from an earlier run of the same job: continue, or start over. A job that continues replaces
        # its own partial files, so it is left out of the existing-files question below.
        used_cities = effective_cities(cities, self._read_int(self.limit_var, "Limit Cities", 0), self.test_var.get())
        saved = []
        for job in jobs:
            key = job_key(job.query, used_cities, max_results, folder, job.output)
            checkpoint = Checkpoint.load(key)
            if checkpoint is not None and checkpoint.done:
                saved.append((job, checkpoint))
            elif checkpoint is not None:
                checkpoint.delete()
        resume = frozenset()
        if saved:
            choice = self._ask_resume([(job, len(checkpoint.done)) for job, checkpoint in saved], len(used_cities))
            if choice is None:
                return None
            if choice == "resume":
                resume = frozenset(checkpoint.key for _, checkpoint in saved)
            else:
                for _, checkpoint in saved:
                    checkpoint.delete()
        resumed_outputs = {job.output for job, checkpoint in saved if checkpoint.key in resume}

        # Files that already exist. With the date and time in every name a clash is not expected, and the
        # numbering rule still protects older files if one happens.
        on_existing = "number"
        clashes = [] if timestamp else [path for job in jobs if job.output not in resumed_outputs
                                        for path in existing_files(folder, job.output, formats)]
        if clashes:
            on_existing = self._ask_existing_files(clashes, allow_skip=len(jobs) > 1 and schedule_mode == "None")
            if on_existing is None:
                return None

        return ScrapeSettings(
            jobs=jobs,
            queued=queued,
            cities=tuple(cities),
            formats=formats,
            sqlite_db=sqlite_db,
            postgres_conn=postgres_conn,
            headless=self.headless_var.get(),
            verbose=self.verbose_var.get(),
            test_mode=self.test_var.get(),
            limit=limit,
            max_results=max_results,
            delay=delay,
            workers=workers,
            proxy=self.proxy_var.get().strip(),
            schedule_mode=schedule_mode,
            folder=folder,
            timestamp=timestamp,
            on_existing=on_existing,
            resume=resume,
        )

    def _ask_choice(self, title, heading, items, note, options):
        """A small modal question: a heading, a list of items, an optional note, and one button per option.
        options is a list of (button text, value, explanation). Cancel is always added and returns None."""
        dialog = tk.Toplevel(self.root)
        dialog.title(title)
        dialog.transient(self.root)
        dialog.resizable(False, False)
        answer = {"value": None}

        def choose(value):
            answer["value"] = value
            dialog.destroy()

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=heading, font=("Segoe UI", 10, "bold"), wraplength=self._px(560), justify=tk.LEFT).pack(anchor=tk.W)

        listing = ttk.Frame(body)
        listing.pack(fill=tk.X, pady=(6, 12))
        box = tk.Listbox(listing, height=min(6, len(items)), width=62, activestyle="none")
        for item in items:
            box.insert(tk.END, item)
        box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if len(items) > 6:
            bar = ttk.Scrollbar(listing, orient=tk.VERTICAL, command=box.yview)
            bar.pack(side=tk.LEFT, fill=tk.Y)
            box.config(yscrollcommand=bar.set)

        if note:
            ttk.Label(body, text=note, foreground="#666666", wraplength=self._px(560), justify=tk.LEFT).pack(anchor=tk.W)
        ttk.Label(body, text="What should AGMS do?", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(12, 6))

        grid = ttk.Frame(body)
        grid.pack(fill=tk.X)
        first = None
        for row, (label, value, text) in enumerate(options):
            button = ttk.Button(grid, text=label, width=16, command=lambda value=value: choose(value))
            button.grid(row=row, column=0, sticky=tk.W, pady=3)
            ttk.Label(grid, text=text, wraplength=self._px(380), justify=tk.LEFT).grid(row=row, column=1, sticky=tk.W, padx=(12, 0))
            first = first or button
        ttk.Button(body, text="Cancel", width=16, command=lambda: choose(None)).pack(anchor=tk.W, pady=(10, 0))

        dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))
        dialog.bind("<Escape>", lambda event: choose(None))
        dialog.update_idletasks()
        self._place_window(dialog, dialog.winfo_reqwidth(), dialog.winfo_reqheight())
        try:
            dialog.grab_set()
        except tk.TclError:
            pass  # some window managers refuse a grab until the window is shown: the dialog still works
        first.focus_set()
        dialog.wait_window()
        return answer["value"]

    def _ask_existing_files(self, files, allow_skip):
        """Tell the user which result files already exist and ask what to do.
        Returns 'number' (keep both), 'overwrite' or 'skip', or None when the user cancels."""
        count = len(files)
        options = [("Keep both", "number", "Save the new results with a number, like hotels_2.csv, so nothing is replaced."),
                   ("Replace", "overwrite", "Overwrite the old files with the new results.")]
        if allow_skip:
            options.append(("Skip those jobs", "skip", "Run only the jobs that have no files yet. This finishes an interrupted queue."))
        return self._ask_choice(
            "Files already exist",
            "1 result file already exists:" if count == 1 else f"{count} result files already exist:",
            [os.path.basename(path) for path in files],
            f"Folder: {os.path.dirname(os.path.abspath(files[0]))}",
            options)

    def _ask_resume(self, saved, total_cities):
        """Offer to continue jobs that an earlier run left unfinished. saved is a list of (job, finished cities).
        Returns 'resume', 'restart', or None when the user cancels."""
        items = []
        for job, finished in saved:
            left = total_cities - finished
            items.append(f"{job.query}: {finished} of {total_cities} cities finished"
                         + (f", {left} left" if left > 0 else ", only saving is left"))
        count = len(saved)
        return self._ask_choice(
            "Continue an unfinished job?",
            "An earlier run of this job did not finish:" if count == 1 else f"Earlier runs of {count} jobs did not finish:",
            items,
            "AGMS kept the cities that were scraped to the end. A city that was cut short is scraped again.",
            [("Continue", "resume", "Scrape only the cities that are left, then save everything together."),
             ("Start over", "restart", "Forget the saved progress and scrape every city again.")])

    def start_scraping(self):
        """Validate the options, then scrape in a separate thread"""
        try:
            settings = self._collect_settings()
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return
        if settings is None:
            return

        self.scraping = True
        self.stop_event.clear()
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self._set_queue_editable(False)

        self.worker_thread = threading.Thread(target=self.scrape_thread, args=(settings,), daemon=True)
        self.worker_thread.start()

    def scrape_thread(self, settings):
        """Scraping thread: applies the limits, then runs the queue once or on a schedule"""
        try:
            # Import lazily so a missing google_maps_scraper.py doesn't crash the GUI on launch
            try:
                from google_maps_scraper import GoogleMapsScraper as Scraper
            except ImportError as e:
                self.logger.error(f"Could not load google_maps_scraper.py: {e}")
                self._show_dialog_safe(
                    "showerror", "Error",
                    f"Could not load google_maps_scraper.py:\n{e}\n\nMake sure both files are in the same folder:\n"
                    "- google_maps_scraper.py\n- google_maps_scraper_gui.py\n\n"
                    "and that the packages in requirements.txt are installed.")
                return

            logging.getLogger('google_maps_scraper').setLevel(
                logging.DEBUG if settings.verbose else logging.INFO)

            cities = list(settings.cities)
            if settings.limit > 0:
                cities = cities[:settings.limit]
                self.logger.info(f"Limited to first {len(cities)} cities")

            if settings.test_mode:
                cities = cities[:1]
                self.logger.info("TEST MODE: Processing only first city")

            if settings.schedule_mode != "None":
                self.run_scheduled_job(Scraper, cities, settings)
            else:
                self.run_queue(Scraper, cities, settings, notify=True)

        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            self._show_dialog_safe("showerror", "Error", f"Error during scraping: {e}")
        finally:
            self._post(self._reset_controls)

    def run_scheduled_job(self, Scraper, cities, settings):
        """Run the whole queue immediately, then again on the chosen interval until Stop is pressed"""
        scheduler = schedule.Scheduler()  # dedicated instance: avoids leaking jobs into schedule's global state

        first_run = True

        def job():
            nonlocal first_run
            # Only the first run can continue saved progress. A repeat is a fresh run that refreshes the data.
            run_settings = settings if first_run else replace(settings, resume=frozenset())
            first_run = False
            try:
                self.run_queue(Scraper, cities, run_settings, notify=False)
            except Exception as e:
                self.logger.error(f"Scheduled run failed, will retry on the next cycle: {e}")
            if self.scraping:
                self._update_progress_safe(0, "Waiting for next scheduled run...")

        if settings.schedule_mode == 'daily':
            scheduler.every().day.at("00:00").do(job)
        elif settings.schedule_mode == 'hourly':
            scheduler.every().hour.do(job)
        elif settings.schedule_mode == 'weekly':
            scheduler.every().week.do(job)

        self.logger.info(f"📅 Scheduler initialized. Job will run: {settings.schedule_mode}")
        job()

        self.logger.info("⏳ Waiting for next scheduled run... (Stop cancels it)")
        while self.scraping:
            scheduler.run_pending()
            self.stop_event.wait(1)
        self.logger.info("Scheduler stopped")

    def run_queue(self, Scraper, cities, settings, notify):
        """Run the jobs one after another. A failed job is logged and the next one still runs.
        notify=False (scheduled runs) skips the modal dialogs, which would pile up unattended."""
        jobs = settings.jobs
        total = len(jobs)
        outcomes = []  # (job, JobResult), or (job, None) when the job failed

        for number in range(total):
            self._set_job_status_safe(settings, number, "Waiting")
        try:
            for number, job in enumerate(jobs, 1):
                if not self.scraping:
                    for index in range(number - 1, total):
                        self._set_job_status_safe(settings, index, "Skipped")
                    break
                self._progress_base, self._progress_span = (number - 1) / total * 100, 1 / total
                self._job_label = f"Job {number}/{total}: " if total > 1 else ""
                if total > 1:
                    self.logger.info(f"Job {number} of {total}: {job.query}")
                self._set_job_status_safe(settings, number - 1, "Running")
                try:
                    result = self.execute_job(Scraper, cities, settings, job)
                except Exception as e:
                    self._set_job_status_safe(settings, number - 1, "Failed")
                    if total == 1:
                        raise
                    self.logger.error(f"Job {number} ({job.query}) failed: {e}")
                    outcomes.append((job, None))
                    continue
                outcomes.append((job, result))
                self._set_job_status_safe(settings, number - 1, self._status_text(result))
        finally:
            self._progress_base, self._progress_span, self._job_label = 0, 1, ""
        self._report_outcomes(settings, outcomes, notify)

    @staticmethod
    def _status_text(result):
        if result.skipped:
            return "Skipped (exists)"
        if result.cancelled:
            return f"Stopped ({result.found})"
        if result.failed_exports:
            return f"Export failed ({result.found})"
        return f"Done ({result.found})" if result.found else "No results"

    def _set_job_status_safe(self, settings, index, status):
        if settings.queued:
            self._post(self._set_job_status, index, status)

    def _report_outcomes(self, settings, outcomes, notify):
        """One summary for the whole run: a dialog (unless scheduled), the status bar and the log"""
        total = len(settings.jobs)
        results = [result for _, result in outcomes if result is not None]
        if total == 1:
            if not results:
                return
            result = results[0]
            if notify and result.skipped:
                self._show_dialog_safe("showinfo", "Nothing to do", f"{result.text}.")
            elif notify and result.found:
                kind, title = ("showwarning", "Warning") if result.failed_exports else ("showinfo", "Success")
                message = f"{result.text}\nTotal: {result.found} companies found"
                if settings.formats:
                    where = os.path.dirname(os.path.abspath(os.path.join(settings.folder, result.saved_as)))
                    message += f"\nSaved as {result.saved_as} in {where}"
                self._show_dialog_safe(kind, title, message)
            elif notify:
                self._show_dialog_safe("showwarning", "Warning", "No results found")
            return

        stopped = not self.scraping
        failed = [job for job, result in outcomes if result is None]
        export_failures = sorted({name for result in results for name in result.failed_exports})
        skipped = total - len(outcomes)
        already_there = [result for result in results if result.skipped]

        if stopped:
            headline = f"Stopped after {len(outcomes)} of {total} jobs. Partial results were saved."
        else:
            headline = f"Finished {len(outcomes)} of {total} jobs."
        if already_there:
            headline += f" {len(already_there)} had files already and {'was' if len(already_there) == 1 else 'were'} skipped."
        if failed:
            headline += f" {len(failed)} failed."
        if export_failures:
            headline += f" These exports failed: {', '.join(export_failures)}."

        lines = []
        for job, result in outcomes:
            if result is None:
                lines.append(f"{job.query}: failed")
            elif result.skipped:
                lines.append(f"{job.query}: skipped, {result.saved_as} already exists")
            elif result.found == 0:
                lines.append(f"{job.query}: no results")
            else:
                lines.append(f"{job.query}: {result.found} companies, saved as {result.saved_as or job.output}")
        if skipped:
            lines.append(f"{skipped} job was skipped." if skipped == 1 else f"{skipped} jobs were skipped.")
        if settings.formats and any(result.found for result in results):
            lines.append(f"Folder: {os.path.abspath(settings.folder or '.')}")

        problems = bool(stopped or failed or export_failures)
        icon = "⏹" if stopped else "⚠️" if problems else "✅"
        self._set_status_safe(f"{icon} {headline}")
        self.logger.info(f"{icon} {headline}")
        if notify:
            self._show_dialog_safe("showwarning" if problems else "showinfo", "Warning" if problems else "Success",
                                   headline + "\n\n" + "\n".join(lines))

    def execute_job(self, Scraper, cities, settings, job):
        """Scrape the cities of one job (single- or multi-threaded) and export its results. Cities that an
        earlier, interrupted run finished are taken from its saved progress instead of being scraped again.
        Returns a JobResult."""
        total_cities = len(cities)
        key = job_key(job.query, cities, settings.max_results, settings.folder, job.output)
        checkpoint = Checkpoint.load(key) if key in settings.resume else None
        resumed = checkpoint is not None
        if resumed:
            # The same file name as the first attempt, so the partial files that attempt left are replaced
            name = planned = checkpoint.name or job.output
            self.logger.info(f"Resuming '{job.query}': {len(checkpoint.done)} of {total_cities} cities were already "
                             f"finished. Results will be saved as {name}")
        else:
            Checkpoint.discard(key)  # progress the user chose not to continue must not leak into this run
            checkpoint = Checkpoint(key, job.query, cities)
            name = f"{job.output}_{time.strftime('%Y-%m-%d_%H-%M')}" if settings.timestamp else job.output
            planned = pick_output_name(settings.folder, name, settings.formats, settings.on_existing)
            if planned is None:
                text = f"Skipped '{job.query}': {name} already exists"
                self._set_status_safe(f"{self._job_label}⏭ {text}")
                self.logger.info(f"⏭ {text}")
                return JobResult(job=job, found=0, failed_exports=(), cancelled=False, icon="⏭", text=text,
                                 skipped=True, saved_as=name)
            where = os.path.dirname(os.path.abspath(os.path.join(settings.folder, name)))
            if planned != name:
                self.logger.warning(f"{name} already exists, so '{job.query}' will be saved as {planned}")
            elif existing_files(settings.folder, name, settings.formats):
                self.logger.warning(f"Replacing the existing files named {name}")
            else:
                self.logger.info(f"Results for '{job.query}' will be saved as {name} in {where}")
            checkpoint.name = planned

        all_results = checkpoint.results(cities)
        todo = [city for city in cities if city not in checkpoint.done]
        already = total_cities - len(todo)
        order = {city: position for position, city in enumerate(cities)}
        proxy = settings.proxy or None
        query, headless, delay, workers = job.query, settings.headless, settings.delay, settings.workers
        max_results = settings.max_results or None
        warned = False

        def city_finished(city, businesses):
            """Keep a city that was scraped to the end. One that Stop cut short is not kept, so a resume does it again."""
            nonlocal warned
            if not self.scraping:
                return
            checkpoint.mark_done(city, businesses)
            if not checkpoint.save() and not warned:
                warned = True
                self.logger.warning("Progress could not be saved, so this job cannot be continued if it is interrupted.")

        if not todo:
            self.logger.info("Every city was already finished, so only saving is left.")
        elif workers > 1:
            self.logger.info(f"🚀 Starting multi-threaded mode with {workers} concurrent browsers...")

            def scrape_one_city(city, pre_delay):
                if pre_delay and self.stop_event.wait(Scraper.jittered_delay(pre_delay)):
                    return None  # stopped during the cool-down: this city was never started
                worker_scraper = self._open_scraper(Scraper, headless, proxy)
                try:
                    businesses = worker_scraper.search_google_maps(query, city, max_results)
                    for business in businesses:
                        business['city'] = city
                    return businesses
                finally:
                    self._close_scraper(worker_scraper)

            completed = already
            submitted = 0
            cities_iter = iter(todo)
            pending = {}

            with ThreadPoolExecutor(max_workers=workers) as executor:
                def submit_next():
                    nonlocal submitted
                    city = next(cities_iter, None)
                    if city is not None:
                        # A worker's first city starts at once; each later one waits out the delay first
                        pre_delay = delay if submitted >= workers else 0
                        pending[executor.submit(scrape_one_city, city, pre_delay)] = city
                        submitted += 1

                for _ in range(min(workers, len(todo))):
                    if not self.scraping:
                        break
                    submit_next()

                while pending:
                    done, _ = wait(pending.keys(), return_when=FIRST_COMPLETED)
                    for future in done:
                        city = pending.pop(future)
                        try:
                            businesses = future.result()
                            if businesses is not None:
                                all_results.extend(businesses)
                                city_finished(city, businesses)
                                self.logger.info(f"Found {len(businesses)} businesses in {city}")
                        except Exception as e:
                            self.logger.error(f"Error processing city {city}: {e}")

                        completed += 1
                        progress = completed / total_cities * 100
                        self._update_progress_safe(progress, f"Processed {completed}/{total_cities} cities")

                        # Stop requested: let already-running cities finish, don't start new ones
                        if self.scraping:
                            submit_next()
        else:
            scraper = self._open_scraper(Scraper, headless, proxy)
            try:
                for i, city in enumerate(todo, 1):
                    if not self.scraping:
                        break

                    number = already + i
                    progress = (number - 1) / total_cities * 100
                    self._update_progress_safe(progress, f"Processing city {number}/{total_cities}: {city}")
                    self.logger.info(f"Processing city {number}/{total_cities}: {city}")

                    try:
                        businesses = scraper.search_google_maps(query, city, max_results)
                        for business in businesses:
                            business['city'] = city
                        all_results.extend(businesses)
                        city_finished(city, businesses)
                        self.logger.info(f"Found {len(businesses)} businesses in {city}")

                        if i < len(todo):
                            scraper.pause(Scraper.jittered_delay(delay))
                    except Exception as e:
                        self.logger.error(f"Error processing city {city}: {e}")
            finally:
                self._close_scraper(scraper)

        all_results.sort(key=lambda business: order.get(business.get('city'), total_cities))  # city order, however they finished
        cancelled = not self.scraping
        if cancelled:
            self.logger.info("Scraping cancelled by user")
        complete = all(city in checkpoint.done for city in cities)

        failed_exports = ()
        final_name = planned
        if all_results:
            self._update_progress_safe(100, "Saving results...")
            self.logger.info("Saving results...")
            if not resumed:
                # The scrape can take hours, so look again: a file may have appeared meanwhile. Skipping is only
                # right before a job starts, never when results are already in hand, so it becomes numbering here.
                policy = "number" if settings.on_existing == "skip" else settings.on_existing
                final_name = pick_output_name(settings.folder, name, settings.formats, policy)
                if final_name != planned:
                    self.logger.warning(f"{planned} was created while this job ran, so the results are saved as {final_name}")
            failed_exports = tuple(self.save_results(Scraper, all_results, settings, job, final_name, checkpoint))
            self.display_statistics(all_results)
            icon, done_text = ("⏹", "Scraping stopped. Partial results were saved.") if cancelled and not complete \
                else ("✅", "Scraping completed successfully!")
            if failed_exports:
                icon, done_text = "⚠️", f"Scraping finished, but these exports failed: {', '.join(failed_exports)}. See the log."
            self._set_status_safe(f"{self._job_label}{icon} {done_text}")
            self.logger.info(f"{icon} {done_text}")
        else:
            icon, done_text = "❌", "No results found"
            self._set_status_safe(f"{self._job_label}{icon} {done_text}")
            self.logger.warning(done_text)

        if complete and not failed_exports:
            checkpoint.delete()  # nothing is left to continue
        elif checkpoint.done:
            checkpoint.save()
            finished = f"{len(checkpoint.done)} of {total_cities} cities are finished and saved."
            if failed_exports and complete:
                self.logger.info(f"The scraping is complete. Run this job again and choose Continue to save without scraping. {finished}")
            elif cancelled:
                self.logger.info(f"Progress kept: {finished} Run this job again and choose Continue to carry on.")
            else:
                self.logger.warning(f"Some cities failed. {finished} Run this job again and choose Continue to retry only the rest.")

        return JobResult(job=job, found=len(all_results), failed_exports=failed_exports,
                         cancelled=cancelled and not complete, icon=icon, text=done_text, saved_as=final_name)

    def save_results(self, Scraper, all_results, settings, job, name=None, checkpoint=None):
        """Export through the shared Scraper class, so CSV/JSON/Excel/SQLite/Postgres
        all use the same field set (including city/hours/image) as the CLI.
        `name` is the file name chosen for this run (job.output by default). With a checkpoint, a database receives
        each finished city only once, however many runs it took to finish the job. Database rows carry the search.
        Returns the names of any exports that failed."""
        base = os.path.join(settings.folder, name or job.output)
        try:
            os.makedirs(os.path.dirname(os.path.abspath(base)), exist_ok=True)  # a typed path may name a new folder
        except OSError as e:
            self.logger.error(f"Could not create the folder for {base}: {e}")  # the export below then reports it
        sqlite_db = settings.sqlite_db or None
        postgres = settings.postgres_conn or None
        if checkpoint is None:
            return Scraper.for_export(all_results).export_all(base, settings.formats, sqlite_db, postgres, search=job.query)

        targets = [(label, value) for label, value in (("SQLite", sqlite_db), ("PostgreSQL", postgres)) if value]
        pending = {label: [city for city in checkpoint.done if city not in set(checkpoint.exported.get(label, []))]
                   for label, _ in targets}
        finished_rows = sum(len(rows) for rows in checkpoint.done.values())
        if len(all_results) == finished_rows and all(set(cities) == set(checkpoint.done) for cities in pending.values()):
            # The usual case: every database gets everything, in the same call as the files
            failed = list(Scraper.for_export(all_results).export_all(base, settings.formats, sqlite_db, postgres, search=job.query))
        else:
            # Resumed or stopped: files get everything found, each database only the finished cities it has not seen
            failed = list(Scraper.for_export(all_results).export_all(base, settings.formats)) if settings.formats else []
            for label, value in targets:
                rows = [business for city in checkpoint.done if city in pending[label] for business in checkpoint.done[city]]
                if rows:
                    where = {"sqlite_db": value} if label == "SQLite" else {"postgres_conn": value}
                    failed += Scraper.for_export(rows).export_all(base, (), search=job.query, **where)
        for label, _ in targets:
            if label not in failed:
                checkpoint.exported[label] = sorted(set(checkpoint.exported.get(label, [])) | set(pending[label]))
        return failed

    def _open_scraper(self, Scraper, headless, proxy):
        """Start a browser and register it, so closing the window can shut down any that are still open"""
        scraper = Scraper(headless=headless, proxy=proxy, stop_event=self.stop_event)
        with self._scrapers_lock:
            self._active_scrapers.add(scraper)
        return scraper

    def _close_scraper(self, scraper):
        with self._scrapers_lock:
            self._active_scrapers.discard(scraper)
        try:
            scraper.close()
        except Exception as e:
            self.logger.warning(f"Error closing browser: {e}")

    def stop_scraping(self):
        """Stop scraping: browsers wake from their waits and the run ends within a moment"""
        self.scraping = False
        self.stop_event.set()
        self.logger.info("Stopping scraper...")
        self.status_var.set("Stopping...")

    def on_close(self):
        """Window close: stop any running job and shut its browsers down before exiting"""
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self._remember_session()
            self.root.destroy()
            return
        if not messagebox.askyesno("Quit", "A scrape is still running. Stop it and exit?"):
            return
        self.stop_scraping()
        self._remember_session()
        self.status_var.set("Closing browsers...")
        self._close_deadline = time.monotonic() + 30
        self._finish_close()

    def _finish_close(self):
        if self.worker_thread.is_alive() and time.monotonic() < self._close_deadline:
            self.root.after(200, self._finish_close)
            return
        # Out of time (a page load can hang): quit any browser still open so no Chrome process is left behind
        with self._scrapers_lock:
            leftovers = list(self._active_scrapers)
        for scraper in leftovers:
            try:
                scraper.close()
            except Exception:
                pass
        self.root.destroy()

    def _reset_controls(self):
        """Reset UI to idle state - must run on the main thread"""
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self._set_queue_editable(True)
        self.scraping = False

    def _post(self, func, *args):
        """Run func on the Tk main thread. Quietly does nothing if the window is already gone."""
        try:
            self.root.after(0, func, *args)
        except (RuntimeError, tk.TclError):
            pass

    def _update_progress_safe(self, progress, status_text):
        """Schedule a progress/status update on the main thread. progress is this job's own 0-100,
        which is mapped onto its share of the whole queue."""
        overall = self._progress_base + progress * self._progress_span
        text = self._job_label + status_text

        def update():
            self.progress_var.set(overall)
            self.progress_label.config(text=f"{int(overall)}%")
            self.status_var.set(text)
        self._post(update)

    def _set_status_safe(self, text):
        self._post(lambda: self.status_var.set(text))

    def _show_dialog_safe(self, kind, title, message):
        # The caller passes a finished message: a lambda over an except-block variable is unbound once the block ends
        self._post(lambda: getattr(messagebox, kind)(title, message))

    def display_statistics(self, results):
        """Display statistics"""
        self.logger.info(f"\n{'='*60}")
        self.logger.info("Scraping Results")
        self.logger.info(f"{'='*60}")
        self.logger.info(f"📊 Total companies found: {len(results)}")
        self.logger.info(f"🏙️  Cities processed: {len(set(r['city'] for r in results))}")

        with_ratings = len([r for r in results if r['rating']])
        with_reviews = len([r for r in results if r['reviews']])
        with_websites = len([r for r in results if r['website']])
        with_emails = len([r for r in results if r.get('email')])
        with_phones = len([r for r in results if r['phone']])
        with_addresses = len([r for r in results if r['address']])
        with_hours = len([r for r in results if r.get('hours')])

        self.logger.info(f"⭐ Companies with ratings: {with_ratings} ({with_ratings/len(results)*100:.1f}%)")
        self.logger.info(f"💬 Companies with reviews: {with_reviews} ({with_reviews/len(results)*100:.1f}%)")
        self.logger.info(f"🌐 Companies with websites: {with_websites} ({with_websites/len(results)*100:.1f}%)")
        self.logger.info(f"📧 Companies with emails: {with_emails} ({with_emails/len(results)*100:.1f}%)")
        self.logger.info(f"📞 Companies with phones: {with_phones} ({with_phones/len(results)*100:.1f}%)")
        self.logger.info(f"📍 Companies with addresses: {with_addresses} ({with_addresses/len(results)*100:.1f}%)")
        self.logger.info(f"🕒 Companies with hours: {with_hours} ({with_hours/len(results)*100:.1f}%)")

    def _px(self, value):
        """Scale a size in pixels to the screen's DPI, so windows keep their proportions at 125% and above"""
        return int(value * self.root.winfo_fpixels("1i") / 96)

    def create_menu(self):
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Save Settings...", command=self.save_settings)
        file_menu.add_command(label="Load Settings...", command=self.load_settings)
        self.recent_menu = tk.Menu(file_menu, tearoff=False, postcommand=self._fill_recent_menu)
        file_menu.add_cascade(label="Recent Settings", menu=self.recent_menu)
        self.remember_var = tk.BooleanVar(value=self.prefs["remember_session"])
        file_menu.add_checkbutton(label="Remember Last Session", variable=self.remember_var,
                                  command=self._toggle_remember_session)
        file_menu.add_separator()
        file_menu.add_command(label="Open Results Folder", command=self.open_results_folder)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=file_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="How to Use", accelerator="F1", command=self.show_help)
        help_menu.add_separator()
        help_menu.add_command(label="About AGMS", command=self.show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)
        self.root.bind("<F1>", lambda event: self.show_help())

    def _results_folder(self):
        """The folder result files go to: the Save in folder box, or the current folder when it is empty"""
        folder = self.folder_var.get().strip()
        return os.path.abspath(os.path.expanduser(folder)) if folder else os.getcwd()

    def open_results_folder(self):
        folder = self._results_folder()
        if not os.path.isdir(folder):
            messagebox.showinfo("Folder not found",
                                f"The folder does not exist yet:\n{folder}\n\nIt is created when the first results are saved.")
            return
        try:
            os.startfile(folder)
        except (AttributeError, OSError) as e:
            messagebox.showerror("Error", f"Could not open the folder {folder}: {e}")

    def _place_window(self, window, width, height):
        """Size a pop-up window and centre it over the main window"""
        self.root.update_idletasks()
        x = self.root.winfo_rootx() + max(0, (self.root.winfo_width() - width) // 2)
        y = self.root.winfo_rooty() + max(0, (self.root.winfo_height() - height) // 3)
        window.geometry(f"{width}x{height}+{x}+{y}")
        try:
            window.iconbitmap(resource_path("icon.ico"))
        except tk.TclError:
            pass
        window.after(250, lambda: self._apply_exact_icons(window))

    def show_help(self):
        """A scrollable guide to every part of the app, with a contents list on the left"""
        existing = self._help_window
        if existing is not None and existing.winfo_exists():
            existing.deiconify()
            existing.lift()
            existing.focus_force()
            return

        window = tk.Toplevel(self.root)
        self._help_window = window
        window.title("How to use AGMS")
        window.minsize(self._px(640), self._px(420))
        self._place_window(window, self._px(940), self._px(680))
        window.bind("<Escape>", lambda event: window.destroy())

        footer = ttk.Frame(window, padding=(12, 8))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(footer, text="Press F1 any time to open this guide.").pack(side=tk.LEFT)
        ttk.Button(footer, text="Close", command=window.destroy).pack(side=tk.RIGHT)

        side = tk.Frame(window, bg=COLOR_PRIMARY, width=self._px(240))
        side.pack(side=tk.LEFT, fill=tk.Y)
        side.pack_propagate(False)
        tk.Label(side, text="Contents", bg=COLOR_PRIMARY, fg="white", font=("Segoe UI", 12, "bold"),
                 anchor="w", padx=16, pady=14).pack(fill=tk.X)

        text = tk.Text(window, wrap=tk.WORD, padx=self._px(28), pady=self._px(20), borderwidth=0,
                       highlightthickness=0, bg="white", font=("Segoe UI", 10), cursor="arrow")
        scrollbar = ttk.Scrollbar(window, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        indent, hang = self._px(18), self._px(44)
        text.tag_configure("title", font=("Segoe UI", 20, "bold"), foreground=COLOR_PRIMARY, spacing3=4)
        text.tag_configure("subtitle", font=("Segoe UI", 11), foreground="#555555", spacing3=10)
        text.tag_configure("heading", font=("Segoe UI", 14, "bold"), foreground=COLOR_PRIMARY, spacing1=22, spacing3=8)
        text.tag_configure("para", spacing1=4, spacing3=9)
        text.tag_configure("item", lmargin1=indent, lmargin2=hang, tabs=(hang,), spacing3=5)
        text.tag_configure("note_label", font=("Segoe UI", 10, "bold"), foreground=COLOR_ACCENT)
        text.tag_configure("note", background="#FFF3E6", lmargin1=indent, lmargin2=indent, spacing1=4, spacing3=10)
        text.tag_configure("bold", font=("Segoe UI", 10, "bold"))

        def write(content, tag):
            for index, piece in enumerate(content.split("**")):  # **bold** marks bold words
                if piece:
                    text.insert(tk.END, piece, (tag, "bold") if index % 2 else (tag,))

        text.insert(tk.END, "How to use AGMS\n", "title")
        text.insert(tk.END, "Everything the app can do, explained step by step.\n", "subtitle")

        for number, (title, blocks) in enumerate(HELP_SECTIONS):
            mark = f"section{number}"
            text.mark_set(mark, "end-1c")
            text.mark_gravity(mark, tk.LEFT)
            text.insert(tk.END, title + "\n", "heading")
            for kind, payload in blocks:
                if kind == "para":
                    write(payload + "\n", "para")
                elif kind == "note":
                    text.insert(tk.END, "Note  ", ("note", "note_label"))
                    write(payload + "\n", "note")
                elif kind == "steps":
                    for step_number, item in enumerate(payload, 1):
                        write(f"{step_number}.\t{item}\n", "item")
                elif kind == "bullets":
                    for item in payload:
                        write(f"\u2022\t{item}\n", "item")

            entry = tk.Label(side, text=title, bg=COLOR_PRIMARY, fg="white", font=("Segoe UI", 10),
                             anchor="w", padx=16, pady=7, cursor="hand2", wraplength=self._px(200), justify=tk.LEFT)
            entry.pack(fill=tk.X)
            entry.bind("<Button-1>", lambda event, name=mark: text.yview(name))
            entry.bind("<Enter>", lambda event, label=entry: label.config(bg="#25598A"))
            entry.bind("<Leave>", lambda event, label=entry: label.config(bg=COLOR_PRIMARY))

        text.config(state=tk.DISABLED)
        text.focus_set()

    def _about_details(self):
        folder = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else APP_DIR
        return "\n".join([
            f"AGMS version: {__version__}",
            f"Publisher: {PUBLISHER}",
            f"Python: {platform.python_version()}   Tk: {self.root.tk.call('info', 'patchlevel')}",
            f"System: {platform.platform()}",
            f"Running as: {'installed app' if getattr(sys, 'frozen', False) else 'Python script'}",
            f"Program folder: {folder}",
            f"Results folder: {self._results_folder()}",
        ])

    def show_about(self):
        window = tk.Toplevel(self.root)
        window.title("About AGMS")
        window.resizable(False, False)
        window.transient(self.root)
        self._place_window(window, self._px(500), self._px(460))
        window.bind("<Escape>", lambda event: window.destroy())

        header = tk.Frame(window, bg=COLOR_PRIMARY)
        header.pack(fill=tk.X)
        tk.Label(header, text="Another Google Maps Scraper", bg=COLOR_PRIMARY, fg="white",
                 font=("Segoe UI", 17, "bold")).pack(anchor="w", padx=24, pady=(18, 0))
        tk.Label(header, text=f"AGMS  version {__version__}", bg=COLOR_PRIMARY, fg="white",
                 font=("Segoe UI", 11)).pack(anchor="w", padx=24, pady=(2, 16))

        body = ttk.Frame(window, padding=(24, 16))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, wraplength=self._px(440), justify=tk.LEFT,
                  text="AGMS collects business details from Google Maps for market research and lead generation. "
                       "Use it responsibly. You are responsible for following Google's terms of service, "
                       "the laws where you live, and data protection rules such as GDPR.").pack(anchor="w")

        facts = ttk.Frame(body)
        facts.pack(anchor="w", pady=(14, 0), fill=tk.X)

        def add_fact(row, label, value, link=None):
            ttk.Label(facts, text=label, font=("Segoe UI", 9, "bold")).grid(row=row, column=0, sticky="nw", pady=2, padx=(0, 14))
            if link:
                value_label = ttk.Label(facts, text=value, foreground=COLOR_PRIMARY, cursor="hand2",
                                        font=("Segoe UI", 9, "underline"))
                value_label.bind("<Button-1>", lambda event: webbrowser.open(link))
            else:
                value_label = ttk.Label(facts, text=value, wraplength=self._px(330), justify=tk.LEFT)
            value_label.grid(row=row, column=1, sticky="w", pady=2)

        add_fact(0, "Publisher", PUBLISHER)
        add_fact(1, "Copyright", COPYRIGHT.replace("Copyright (c)", "©"))
        add_fact(2, "License", "MIT License")
        add_fact(3, "Project page", "github.com/powergr/AGMS", "https://github.com/powergr/AGMS")
        add_fact(4, "Report a problem", "github.com/powergr/AGMS/issues", "https://github.com/powergr/AGMS/issues")
        add_fact(5, "Python", f"{platform.python_version()}  (Tk {self.root.tk.call('info', 'patchlevel')})")
        add_fact(6, "Results folder", self._results_folder())

        buttons = ttk.Frame(body)
        buttons.pack(side=tk.BOTTOM, fill=tk.X, pady=(16, 0))
        ttk.Button(buttons, text="Close", command=window.destroy).pack(side=tk.RIGHT)
        copy_button = ttk.Button(buttons, text="Copy details")

        def copy_details():
            self.root.clipboard_clear()
            self.root.clipboard_append(self._about_details())
            copy_button.config(text="Copied")

        copy_button.config(command=copy_details)
        copy_button.pack(side=tk.RIGHT, padx=(0, 8))
        window.focus_set()


HELP_SECTIONS = [
    ("Getting started", [
        ("para", "AGMS collects business details from Google Maps for the search words and cities you choose. "
                 "It opens Google Chrome in the background, scrolls through the results, and saves what it finds "
                 "to files on your computer."),
        ("note", "You need Google Chrome installed and an internet connection."),
        ("steps", [
            "Type what you are looking for in **Search Query**, for example dentists.",
            "Type one city per line in **Cities**, for example Athens, Greece.",
            "Tick the file types you want under **Output & Export**.",
            "Press **Start Scraping** and watch the log at the bottom of the window.",
        ]),
        ("para", "When the run ends, your files are in the results folder. Choose **File, Open Results Folder** "
                 "to see them. The first line of the log also shows where that folder is."),
    ]),
    ("Search query and cities", [
        ("para", "AGMS joins your search query with each city. The query dentists and the city Athens, Greece "
                 "search Google Maps for dentists Athens, Greece."),
        ("bullets", [
            "**Enter Cities:** type one city per line. Adding the country, as in Paris, France, avoids mix-ups "
            "between cities that share a name.",
            "**From File:** pick a text file with one city per line. Lines that start with OK- or a dash are "
            "cleaned up for you.",
            "**Limit Cities** in Scraping Options uses only the first cities on the list.",
            "**Test Mode** uses only the first city. It is a quick way to check your settings before a long run.",
        ]),
    ]),
    ("The job queue: several searches in a row", [
        ("para", "The queue lets you line up several searches. They run one after another, and the next one starts "
                 "as soon as the one before it finishes. Every job uses the same cities and the same options, "
                 "and each job saves to its own files."),
        ("steps", [
            "Type a search query.",
            "Type a name in **Save as**, or leave it empty. An empty box makes the name from the query, "
            "so Coffee shops is saved as coffee_shops.",
            "Press **Add to queue**, or press Enter. The boxes clear so you can add the next job.",
            "Repeat for every search, then press **Start Scraping**.",
        ]),
        ("bullets", [
            "The **Status** column shows Waiting, Running, Done with the number of businesses found, No results, "
            "Failed, Stopped, or Skipped.",
            "Select a job to move it with **Up** and **Down**, or take it out with **Remove** or the Delete key. "
            "**Clear** empties the whole queue.",
            "Double-click a job to move it back into the boxes and change it.",
            "Two jobs cannot save under the same name. AGMS tells you when a name is taken.",
            "The queue is locked while it runs. Press Stop first if you need to change it.",
            "If a job fails, for example because Chrome would not start, AGMS logs the error and carries on "
            "with the next job.",
            "With an empty queue, Start simply runs the query in the box.",
            "If the query box still has text when you press Start, AGMS asks whether to add it as the last job.",
            "The progress bar covers the whole queue, and the status bar shows which job is running.",
        ]),
        ("note", "Save Settings keeps the queue too, so you can reuse a set of searches later."),
    ]),
    ("Output and export", [
        ("bullets", [
            "**CSV, JSON and Excel:** tick any mix of these. Each job writes its own files, named after its "
            "Save as name, for example hotels.csv and hotels.xlsx.",
            "**Save in folder:** the folder for your files. Press Browse to pick one. AGMS creates it if it does "
            "not exist. Leave it empty to use the default results folder.",
            "**A full path in Save as** such as C:\\Data\\hotels overrides the folder for that one job.",
            "**Add the date and time to file names:** hotels becomes hotels_2026-11-15_14-30. Every run then "
            "keeps its own files, which suits a repeat schedule.",
            "**SQLite file:** also saves every business into a database file. Press Browse to pick or create one.",
            "**PostgreSQL connection string:** also saves into a PostgreSQL server. Use the form "
            "postgresql://user:password@host/database.",
            "**Database rows carry the search.** Each row has a search column with the query that found it, so "
            "searches that share one table can be told apart. CSV, JSON and Excel files keep their usual columns.",
            "**Databases keep adding rows.** Running the same search again adds the same businesses a second "
            "time. Within one job, a city is added only once, even when the job takes several runs to finish.",
            "A database table made by an older version gets the search column added on the next save. Its old "
            "rows keep that column empty.",
            "**Passwords are never saved** in a settings file. Type them again after loading.",
            "If one file type fails to save, for example because the CSV is open in Excel, the other types are "
            "still saved and AGMS tells you which one failed.",
        ]),
        ("para", "**When files already exist.** AGMS never replaces your earlier results without asking. When you "
                 "press Start and a CSV, JSON or Excel file with the same name is already in the folder, a window "
                 "lists those files and asks what to do."),
        ("bullets", [
            "**Keep both:** saves the new results with a number, such as hotels_2.csv, so nothing is replaced.",
            "**Replace:** overwrites the old files with the new results.",
            "**Skip those jobs:** runs only the jobs that have no files yet. Use it to finish a queue that was "
            "interrupted. This choice appears when the queue has more than one job and no schedule is set.",
            "**Cancel:** goes back without running anything.",
            "A file that appears while a long job runs is also protected. The new results are saved with a number.",
        ]),
        ("para", "Each business is one row with these columns: id, name, description, rating, reviewCount, phone, "
                 "email, website, address, hours, image, verified, tags, and city."),
        ("para", "The email is read from the business's own website, because Google Maps does not list emails. "
                 "It stays empty when the website shows none. Verified is true when the business has a rating. "
                 "Description and tags are reserved and stay empty."),
    ]),
    ("Scraping options", [
        ("bullets", [
            "**Headless:** runs Chrome out of sight. Turn it off to watch the browser work, which helps when "
            "something goes wrong.",
            "**Test Mode:** scrapes only the first city, so you can check your settings quickly.",
            "**Verbose Logging:** adds extra detail lines to the log.",
            "**Limit Cities:** uses only the first cities of the list. Zero means all cities.",
            "**Max Results Per City:** the most businesses to collect in each city. Zero means all of them. "
            "Scrolling stops as soon as enough are loaded.",
            "**Delay Between Cities:** the seconds to wait between cities, varied by 20% either way so requests "
            "are not perfectly regular. Longer is gentler on Google. Shorter is faster but riskier.",
            "**Parallel Workers:** how many Chrome windows work on different cities at the same time. Each one "
            "uses a lot of memory, so start with 2 or 3. Each worker also waits the delay between its own cities.",
            "**Proxy Server:** an address such as http://203.0.113.5:8080 to send the traffic through. Chrome does "
            "not use a username and password typed into this box, so use a proxy that needs no login.",
            "**Repeat Schedule:** runs the queue again and again. See the next section.",
        ]),
    ]),
    ("Stopping and continuing", [
        ("para", "A long job can be interrupted. You may press Stop, close the window, lose power, or hit an error. "
                 "AGMS saves its progress after every city, so the cities already finished are not lost."),
        ("steps", [
            "Press Start on the same job again. It must have the same query, the same cities, the same Max "
            "Results, the same folder, and the same Save as name.",
            "A window shows how many cities were finished, for example 12 of 40.",
            "Choose **Continue** to scrape only the cities that are left. AGMS then saves everything together, "
            "in city order, and replaces the partial files from the interrupted run.",
            "Choose **Start over** to forget the progress and scrape every city again.",
        ]),
        ("bullets", [
            "A city that Stop cut short is not counted as finished, so it is scraped again in full.",
            "A city that failed, for example because the page did not load, is kept out of the finished list. "
            "Run the job again and Continue retries only those cities.",
            "If only the saving failed, for example because a CSV was open in Excel, Continue saves without "
            "scraping anything. The window then says only saving is left.",
            "Databases receive each finished city once, so continuing never adds the same city twice.",
            "Progress files are kept in your user profile folder and deleted when the job finishes. Files older "
            "than 30 days are removed automatically.",
            "Scheduled repeats always start fresh. Only the first run of a schedule can continue earlier progress.",
        ]),
    ]),
    ("Repeating on a schedule", [
        ("para", "Choose daily, hourly, or weekly. AGMS runs the whole queue straight away, then waits and runs it "
                 "again at the next time. Daily runs happen at midnight."),
        ("bullets", [
            "Keep AGMS open, because scheduling stops as soon as you close the window.",
            "Your settings are read when you press Start. To change them, press Stop and start again.",
            "A run that fails is logged, and the next one still happens.",
            "Scheduled runs show no pop-up dialogs. Check the log and the status bar instead.",
            "If files with the same name already exist, you choose once at the start. Keep both saves each later "
            "run with a number. Replace overwrites on every run. Tick the date and time option to give every run "
            "its own names. Databases keep adding rows.",
        ]),
    ]),
    ("Saving your settings", [
        ("para", "Save Settings writes everything on the screen into one file: the search query, the queue, "
                 "the cities, the folder, the file types, the database boxes, and every scraping option. "
                 "Load Settings puts it all back, so a favorite setup is two clicks away."),
        ("bullets", [
            "**Save Settings** asks for a file name. The file ends in .agms and is plain text you can open in "
            "Notepad.",
            "**Load Settings** checks the whole file first. A damaged file, or a results file picked by mistake, "
            "gives a clear message and changes nothing.",
            "If a few values in a file are wrong, the good ones are loaded and a message lists what was ignored.",
            "**File, Recent Settings** lists the last files you saved or loaded, so you can open one with a click.",
            "**File, Remember Last Session** is on by default. AGMS saves your screen when you close it and "
            "restores it on the next start. Switch it off to always start fresh. Clear Form also starts fresh.",
            "**Passwords are never saved**, in a settings file or in the remembered session. Type the proxy and "
            "database passwords again when you need them.",
            "Settings files from older versions still load. They hold no cities, so your cities stay as they are.",
            "Loading is not possible while a run is in progress, so press Stop first.",
        ]),
        ("note", "Your remembered session and recent files live in your user profile folder, "
                 "not in the results folder."),
    ]),
    ("Buttons, menus and the log", [
        ("bullets", [
            "**Start Scraping** runs the queue, or the single query in the box when the queue is empty.",
            "**Stop** ends the run within a moment, even in the middle of a big city. The businesses found so far "
            "are saved, and jobs that had not started are marked Skipped. You can continue the job later.",
            "**Clear Form** puts every box back to its starting value and empties the queue.",
            "**Save Settings** and **Load Settings** keep your whole screen in a file. See the section above.",
            "**Clear Logs**, **Copy Logs** and **Save Logs** work on the log panel at the bottom.",
            "**File menu:** Save Settings, Load Settings, Recent Settings, Remember Last Session, Open Results "
            "Folder, and Exit.",
            "**Help menu:** this guide, and the About window with version details you can copy.",
            "In the log, red lines are errors, orange lines are warnings, black lines are progress, and gray "
            "lines are extra detail from Verbose Logging.",
            "If you close the window during a run, AGMS asks first, then shuts down every Chrome window it started.",
        ]),
    ]),
    ("Tips for good results", [
        ("bullets", [
            "Try a new search in **Test Mode** first. One city shows you whether the query finds what you want.",
            "Use specific queries, such as vegan restaurants, instead of just food.",
            "Keep the delay at 10 seconds or more for big runs. Google may slow you down or ask for a captcha "
            "if you go too fast.",
            "Use 2 or 3 parallel workers at most. Each worker is a full Chrome window.",
            "Check a sample of the results by hand before you rely on them.",
        ]),
    ]),
    ("Troubleshooting", [
        ("bullets", [
            "**No results found:** check the spelling, add the country to each city, and try again with Headless "
            "turned off so you can watch what Chrome does.",
            "**Chrome does not start:** install Google Chrome. On the first run AGMS downloads a matching driver, "
            "so it needs an internet connection.",
            "**Timeouts or a captcha:** raise the delay, lower the number of workers, or try again later.",
            "**A file did not save:** it is probably open in Excel. Close it and run again. Your other file types "
            "were saved.",
            "**Cannot find your files:** choose File, Open Results Folder. It opens the folder named in Save in "
            "folder, or the default results folder.",
            "**Your last search is already on screen at start-up:** that is Remember Last Session. Turn it off in "
            "the File menu, or press Clear Form.",
            "**A job says Failed:** read the red lines in the log just above. The jobs after it still ran.",
            "**A window asks about an unfinished job:** that is saved progress from an interrupted run. See "
            "Stopping and continuing.",
        ]),
    ]),
    ("Good to know", [
        ("bullets", [
            "AGMS only reads information that Google Maps shows publicly. You are responsible for following "
            "Google's terms of service, the laws where you live, and data protection rules such as GDPR.",
            "Maps pages are requested in English, so ratings, hours and photos are found on any system language.",
            "All jobs in a queue share the same cities and options. To use different cities, run a separate queue.",
            "The command line version, google_maps_scraper.py, has the same options but no queue and no menus.",
        ]),
    ]),
    ("Keyboard shortcuts", [
        ("bullets", [
            "**F1:** open this guide.",
            "**Enter** in the Search Query or Save as box: add the job to the queue.",
            "**Delete:** remove the selected job from the queue.",
            "**Double-click** a job: move it back into the boxes to edit it.",
            "**Esc:** close this window.",
        ]),
    ]),
]


def run_self_test():
    """Used by the Windows build to prove the packaged app is complete: it loads every module the
    scraper needs and runs each export format. A build that lost a dependency fails here, not on a
    user's computer. The report goes to the temp folder because a windowed exe has no console.
    Add --online to also make an HTTPS request and start Chrome, which needs internet and Chrome installed."""
    import importlib
    import tempfile

    lines = []
    failed = False

    def step(name, action):
        nonlocal failed
        try:
            action()
            lines.append(f"ok    {name}")
        except Exception as e:
            failed = True
            lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")

    for module in ("selenium", "webdriver_manager.chrome", "bs4", "requests", "schedule", "pandas",
                   "openpyxl", "sqlalchemy", "psycopg2", "google_maps_scraper"):
        step(f"import {module}", lambda module=module: importlib.import_module(module))

    def icon_is_bundled():
        if not os.path.exists(resource_path("icon.ico")):
            raise FileNotFoundError(resource_path("icon.ico"))

    def postgres_driver_loads():
        import sqlalchemy
        from google_maps_scraper import normalize_postgres_url
        # Creating the engine loads the driver, and never connects
        sqlalchemy.create_engine(normalize_postgres_url("postgresql://user:password@localhost/db"))

    def every_export_format_works():
        from google_maps_scraper import GoogleMapsScraper
        sample = [{'company': 'Self Test Co', 'address': '1 Main St', 'phone': '1', 'website': 'https://example.com',
                   'email': 'info@example.com', 'rating': '4.5', 'reviews': '10', 'hours': '9-5', 'image': '',
                   'city': 'Paris'}]
        with tempfile.TemporaryDirectory() as folder:
            base = os.path.join(folder, "selftest")
            database = os.path.join(folder, "selftest.db")
            broken = GoogleMapsScraper.for_export(sample).export_all(base, ["csv", "json", "excel"], sqlite_db=database,
                                                                     search="self test")
            if broken:
                raise RuntimeError(f"these exports failed: {', '.join(broken)}")
            for path in (base + ".csv", base + ".json", base + ".xlsx", database):
                if not os.path.exists(path):
                    raise FileNotFoundError(path)
            import sqlite3
            connection = sqlite3.connect(database)
            try:
                stored = connection.execute("SELECT search FROM businesses").fetchall()
            finally:
                connection.close()
            if stored != [("self test",)]:
                raise RuntimeError(f"the database rows did not carry the search: {stored}")

    def progress_files_work():
        checkpoint = Checkpoint("selftest0000000000000000", "q", ["a", "b"])
        checkpoint.mark_done("a", [{"company": "x", "city": "a"}])
        previous = os.environ.get("AGMS_CONFIG_DIR")
        with tempfile.TemporaryDirectory() as folder:
            os.environ["AGMS_CONFIG_DIR"] = folder  # a throwaway folder, so the test never touches real progress
            try:
                if not checkpoint.save():
                    raise RuntimeError("the progress file could not be written")
                loaded = Checkpoint.load(checkpoint.key)
                if loaded is None or list(loaded.done) != ["a"]:
                    raise RuntimeError("the progress file did not load back")
            finally:
                if previous is None:
                    os.environ.pop("AGMS_CONFIG_DIR", None)
                else:
                    os.environ["AGMS_CONFIG_DIR"] = previous

    def chrome_classes_resolve():
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.support import expected_conditions
        from selenium.webdriver.support.ui import WebDriverWait
        from webdriver_manager.chrome import ChromeDriverManager
        # Newer Selenium loads Chrome lazily by name, which PyInstaller cannot see by reading the code
        getattr(webdriver, "Chrome")
        Options()

    def parse_html_and_pick_email():
        from bs4 import BeautifulSoup
        from google_maps_scraper import pick_best_email
        link = BeautifulSoup('<a href="mailto:Info@Acme.com?subject=Hi">mail</a>', "html.parser").find("a")
        if pick_best_email([link["href"][7:].split("?")[0]], "https://acme.com") != "info@acme.com":
            raise RuntimeError("the email choice gave a wrong answer")

    def https_works():
        import requests
        requests.get("https://example.com", timeout=20).raise_for_status()  # fails if the certificates are missing

    def chrome_starts():
        from google_maps_scraper import GoogleMapsScraper
        scraper = GoogleMapsScraper(headless=True)  # the app's real start-up path, driver download included
        try:
            scraper.driver.get("about:blank")
        finally:
            scraper.close()

    step("icon.ico is bundled", icon_is_bundled)
    step("PostgreSQL driver loads", postgres_driver_loads)
    step("Chrome driver classes load", chrome_classes_resolve)
    step("CSV, JSON, Excel and SQLite exports work", every_export_format_works)
    step("Progress files for resuming work", progress_files_work)
    step("HTML parsing and email choice work", parse_html_and_pick_email)
    if "--online" in sys.argv[1:]:
        step("HTTPS requests work", https_works)
        step("Chrome starts and loads a page", chrome_starts)

    report = "\n".join(lines) + "\n" + ("SELF-TEST FAILED" if failed else "SELF-TEST PASSED") + "\n"
    try:
        with open(os.path.join(tempfile.gettempdir(), "agms_selftest.txt"), "w", encoding="utf-8") as f:
            f.write(report)
    except OSError:
        pass
    print(report)
    return 1 if failed else 0


if __name__ == "__main__":
    if "--self-test" in sys.argv[1:]:
        sys.exit(run_self_test())
    enable_dpi_awareness()
    prepare_output_folder()
    set_windows_app_id()
    root = tk.Tk()
    app = GoogleMapsScraperGUI(root)
    root.mainloop()
