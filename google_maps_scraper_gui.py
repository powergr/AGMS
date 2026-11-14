import ctypes
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
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import schedule

__version__ = "1.3.4"

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


class LogHandler(logging.Handler):
    """Custom logging handler for GUI"""
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

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

        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.logger.info(f"Files with a relative name are saved in: {os.getcwd()}")
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
        ttk.Label(frame, text="Each job saves to files named after its Save as box.", foreground="#666666").grid(
            row=1, column=0, columnspan=2, sticky=tk.W)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=10)
        ttk.Label(frame, text="Database export (optional)", font=("Segoe UI", 9, "bold")).grid(
            row=3, column=0, columnspan=2, sticky=tk.W)

        ttk.Label(frame, text="SQLite DB file:").grid(row=4, column=0, sticky=tk.W, pady=5)
        sqlite_row = ttk.Frame(frame)
        sqlite_row.grid(row=4, column=1, sticky=tk.EW, pady=5, padx=5)
        sqlite_row.columnconfigure(0, weight=1)
        self.sqlite_var = tk.StringVar(value="")
        ttk.Entry(sqlite_row, textvariable=self.sqlite_var).grid(row=0, column=0, sticky=tk.EW)
        ttk.Button(sqlite_row, text="Browse...", command=self.browse_sqlite_file).grid(row=0, column=1, padx=(5, 0))

        ttk.Label(frame, text="PostgreSQL connection string:").grid(row=5, column=0, sticky=tk.W, pady=5)
        self.postgres_var = tk.StringVar(value="")
        ttk.Entry(frame, textvariable=self.postgres_var).grid(row=5, column=1, sticky=tk.EW, pady=5, padx=5)

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

    def clear_form(self):
        """Clear all form fields"""
        self.query_var.set("")
        self.cities_text.delete(1.0, tk.END)
        self.output_var.set("")
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

    def save_settings(self):
        """Save current settings"""
        filename = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if filename:
            try:
                proxy = self.proxy_var.get().strip()
                postgres = self.postgres_var.get().strip()
                safe_proxy, safe_postgres = strip_secrets(proxy), strip_secrets(postgres)
                with open(filename, 'w', encoding='utf-8') as f:
                    f.write(f"query={self.query_var.get()}\n")
                    f.write(f"output={self.output_var.get()}\n")
                    for job in self.jobs:
                        f.write(f"job={job.query}\t{job.output}\n")
                    f.write(f"export_csv={self.export_csv_var.get()}\n")
                    f.write(f"export_json={self.export_json_var.get()}\n")
                    f.write(f"export_excel={self.export_excel_var.get()}\n")
                    f.write(f"headless={self.headless_var.get()}\n")
                    f.write(f"test={self.test_var.get()}\n")
                    f.write(f"verbose={self.verbose_var.get()}\n")
                    f.write(f"limit={self.limit_var.get()}\n")
                    f.write(f"delay={self.delay_var.get()}\n")
                    f.write(f"max_results={self.max_results_var.get()}\n")
                    f.write(f"workers={self.workers_var.get()}\n")
                    f.write(f"proxy={safe_proxy}\n")
                    f.write(f"schedule={self.schedule_var.get()}\n")
                    f.write(f"sqlite={self.sqlite_var.get()}\n")
                    f.write(f"postgres={safe_postgres}\n")
                self.logger.info(f"Settings saved to {filename}")
                note = ""
                if (safe_proxy, safe_postgres) != (proxy, postgres):
                    note = "\n\nPasswords are never saved. Enter the proxy and database passwords again after loading."
                messagebox.showinfo("Success", "Settings saved successfully!" + note)
            except Exception as e:
                self.logger.error(f"Error saving settings: {e}")
                messagebox.showerror("Error", f"Error saving settings: {e}")

    def load_settings(self):
        """Load settings from file"""
        filename = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if filename:
            try:
                loaded_jobs = []
                with open(filename, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        line = line.strip()
                        # split(..., 1): values like a Postgres URL contain '=' themselves
                        if not line or '=' not in line:
                            continue
                        key, value = line.split('=', 1)
                        if key == "query":
                            self.query_var.set(value)
                        elif key == "output":
                            self.output_var.set(value)
                        elif key == "job":
                            query, _, name = value.partition("\t")
                            try:
                                loaded_jobs.append(self._make_job(query, name, loaded_jobs))
                            except ValueError as problem:
                                self.logger.warning(f"Skipped a saved job: {problem}")
                        elif key == "export_csv":
                            self.export_csv_var.set(value.lower() == "true")
                        elif key == "export_json":
                            self.export_json_var.set(value.lower() == "true")
                        elif key == "export_excel":
                            self.export_excel_var.set(value.lower() == "true")
                        elif key == "format":
                            # Back-compat with settings files saved by older GUI versions
                            self.export_csv_var.set(value in ("csv", "both"))
                            self.export_json_var.set(value in ("json", "both"))
                        elif key == "headless":
                            self.headless_var.set(value.lower() == "true")
                        elif key == "test":
                            self.test_var.set(value.lower() == "true")
                        elif key == "verbose":
                            self.verbose_var.set(value.lower() == "true")
                        elif key == "limit":
                            self.limit_var.set(int(value))
                        elif key == "delay":
                            self.delay_var.set(int(value))
                        elif key == "max_results":
                            self.max_results_var.set(int(value))
                        elif key == "workers":
                            self.workers_var.set(int(value))
                        elif key == "proxy":
                            self.proxy_var.set(value)
                        elif key == "schedule":
                            self.schedule_var.set(value)
                        elif key == "sqlite":
                            self.sqlite_var.set(value)
                        elif key == "postgres":
                            self.postgres_var.set(value)
                if loaded_jobs and not self.scraping:
                    self.jobs[:] = loaded_jobs
                    self.job_status[:] = ["Waiting"] * len(loaded_jobs)
                    self._refresh_queue()
                self.logger.info(f"Settings loaded from {filename}")
                messagebox.showinfo("Success", "Settings loaded successfully!")
            except Exception as e:
                self.logger.error(f"Error loading settings: {e}")
                messagebox.showerror("Error", f"Error loading settings: {e}")

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

        collected = self._collect_jobs()  # last, because it may ask a question
        if collected is None:
            return None
        jobs, queued = collected

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
            schedule_mode=self.schedule_var.get(),
        )

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

        def job():
            try:
                self.run_queue(Scraper, cities, settings, notify=False)
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
                self.logger.info(f"Results for '{job.query}' are saved as: {job.output}")
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
            if notify and result.found:
                kind, title = ("showwarning", "Warning") if result.failed_exports else ("showinfo", "Success")
                self._show_dialog_safe(kind, title, f"{result.text}\nTotal: {result.found} companies found")
            elif notify:
                self._show_dialog_safe("showwarning", "Warning", "No results found")
            return

        stopped = not self.scraping
        failed = [job for job, result in outcomes if result is None]
        export_failures = sorted({name for result in results for name in result.failed_exports})
        skipped = total - len(outcomes)

        if stopped:
            headline = f"Stopped after {len(outcomes)} of {total} jobs. Partial results were saved."
        else:
            headline = f"Finished {len(outcomes)} of {total} jobs."
        if failed:
            headline += f" {len(failed)} failed."
        if export_failures:
            headline += f" These exports failed: {', '.join(export_failures)}."

        lines = []
        for job, result in outcomes:
            if result is None:
                lines.append(f"{job.query}: failed")
            elif result.found == 0:
                lines.append(f"{job.query}: no results")
            else:
                lines.append(f"{job.query}: {result.found} companies, saved as {job.output}")
        if skipped:
            lines.append(f"{skipped} job was skipped." if skipped == 1 else f"{skipped} jobs were skipped.")

        problems = bool(stopped or failed or export_failures)
        icon = "⏹" if stopped else "⚠️" if problems else "✅"
        self._set_status_safe(f"{icon} {headline}")
        self.logger.info(f"{icon} {headline}")
        if notify:
            self._show_dialog_safe("showwarning" if problems else "showinfo", "Warning" if problems else "Success",
                                   headline + "\n\n" + "\n".join(lines))

    def execute_job(self, Scraper, cities, settings, job):
        """Scrape all cities for one job (single- or multi-threaded) and export its results.
        Returns a JobResult."""
        total_cities = len(cities)
        all_results = []
        proxy = settings.proxy or None
        query, headless, delay, workers = job.query, settings.headless, settings.delay, settings.workers
        max_results = settings.max_results or None

        if workers > 1:
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

            completed = 0
            submitted = 0
            cities_iter = iter(cities)
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

                for _ in range(min(workers, total_cities)):
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
                for i, city in enumerate(cities, 1):
                    if not self.scraping:
                        break

                    progress = (i - 1) / total_cities * 100
                    self._update_progress_safe(progress, f"Processing city {i}/{total_cities}: {city}")
                    self.logger.info(f"Processing city {i}/{total_cities}: {city}")

                    try:
                        businesses = scraper.search_google_maps(query, city, max_results)
                        for business in businesses:
                            business['city'] = city
                        all_results.extend(businesses)
                        self.logger.info(f"Found {len(businesses)} businesses in {city}")

                        if i < total_cities:
                            scraper.pause(Scraper.jittered_delay(delay))
                    except Exception as e:
                        self.logger.error(f"Error processing city {city}: {e}")
            finally:
                self._close_scraper(scraper)

        cancelled = not self.scraping
        if cancelled:
            self.logger.info("Scraping cancelled by user")

        failed_exports = ()
        if all_results:
            self._update_progress_safe(100, "Saving results...")
            self.logger.info("Saving results...")
            failed_exports = tuple(self.save_results(Scraper, all_results, settings, job))
            self.display_statistics(all_results)
            icon, done_text = ("⏹", "Scraping stopped. Partial results were saved.") if cancelled \
                else ("✅", "Scraping completed successfully!")
            if failed_exports:
                icon, done_text = "⚠️", f"Scraping finished, but these exports failed: {', '.join(failed_exports)}. See the log."
            self._set_status_safe(f"{self._job_label}{icon} {done_text}")
            self.logger.info(f"{icon} {done_text}")
        else:
            icon, done_text = "❌", "No results found"
            self._set_status_safe(f"{self._job_label}{icon} {done_text}")
            self.logger.warning(done_text)

        return JobResult(job=job, found=len(all_results), failed_exports=failed_exports,
                         cancelled=cancelled, icon=icon, text=done_text)

    def save_results(self, Scraper, all_results, settings, job):
        """Export through the shared Scraper class, so CSV/JSON/Excel/SQLite/Postgres
        all use the same field set (including city/hours/image) as the CLI.
        Returns the names of any exports that failed."""
        exporter = Scraper.for_export(all_results)
        return exporter.export_all(job.output, settings.formats,
                                   settings.sqlite_db or None, settings.postgres_conn or None)

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
            self.root.destroy()
            return
        if not messagebox.askyesno("Quit", "A scrape is still running. Stop it and exit?"):
            return
        self.stop_scraping()
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

    def open_results_folder(self):
        try:
            os.startfile(os.getcwd())
        except (AttributeError, OSError) as e:
            messagebox.showerror("Error", f"Could not open the folder {os.getcwd()}: {e}")

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
            f"Python: {platform.python_version()}   Tk: {self.root.tk.call('info', 'patchlevel')}",
            f"System: {platform.platform()}",
            f"Running as: {'installed app' if getattr(sys, 'frozen', False) else 'Python script'}",
            f"Program folder: {folder}",
            f"Results folder: {os.getcwd()}",
        ])

    def show_about(self):
        window = tk.Toplevel(self.root)
        window.title("About AGMS")
        window.resizable(False, False)
        window.transient(self.root)
        self._place_window(window, self._px(500), self._px(430))
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

        add_fact(0, "Made by", "Pashalis Laoutaris")
        add_fact(1, "License", "MIT License")
        add_fact(2, "Project page", "github.com/powergr/AGMS", "https://github.com/powergr/AGMS")
        add_fact(3, "Report a problem", "github.com/powergr/AGMS/issues", "https://github.com/powergr/AGMS/issues")
        add_fact(4, "Python", f"{platform.python_version()}  (Tk {self.root.tk.call('info', 'patchlevel')})")
        add_fact(5, "Results folder", os.getcwd())

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
            "**Saving somewhere else:** type a full path in Save as, such as C:\\Data\\hotels, and the files "
            "go to that folder.",
            "**SQLite file:** also saves every business into a database file. Press Browse to pick or create one.",
            "**PostgreSQL connection string:** also saves into a PostgreSQL server. Use the form "
            "postgresql://user:password@host/database.",
            "**Databases keep adding rows.** Running the same search again adds the same businesses a second "
            "time, and every job writes into the same table.",
            "**Passwords are never saved** in a settings file. Type them again after loading.",
            "If one file type fails to save, for example because the CSV is open in Excel, the other types are "
            "still saved and AGMS tells you which one failed.",
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
    ("Repeating on a schedule", [
        ("para", "Choose daily, hourly, or weekly. AGMS runs the whole queue straight away, then waits and runs it "
                 "again at the next time. Daily runs happen at midnight."),
        ("bullets", [
            "Keep AGMS open, because scheduling stops as soon as you close the window.",
            "Your settings are read when you press Start. To change them, press Stop and start again.",
            "A run that fails is logged, and the next one still happens.",
            "Scheduled runs show no pop-up dialogs. Check the log and the status bar instead.",
            "CSV, JSON and Excel files with the same name are replaced on every run. Databases keep adding rows.",
        ]),
    ]),
    ("Buttons, menus and the log", [
        ("bullets", [
            "**Start Scraping** runs the queue, or the single query in the box when the queue is empty.",
            "**Stop** ends the run within a moment, even in the middle of a big city. The businesses found so far "
            "are saved, and jobs that had not started are marked Skipped.",
            "**Clear Form** puts every box back to its starting value and empties the queue.",
            "**Save Settings** and **Load Settings** keep your options and your queue in a text file.",
            "**Clear Logs**, **Copy Logs** and **Save Logs** work on the log panel at the bottom.",
            "**File menu:** Save Settings, Load Settings, Open Results Folder, and Exit.",
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
            "**Cannot find your files:** choose File, Open Results Folder.",
            "**A job says Failed:** read the red lines in the log just above. The jobs after it still ran.",
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
            broken = GoogleMapsScraper.for_export(sample).export_all(base, ["csv", "json", "excel"], sqlite_db=database)
            if broken:
                raise RuntimeError(f"these exports failed: {', '.join(broken)}")
            for path in (base + ".csv", base + ".json", base + ".xlsx", database):
                if not os.path.exists(path):
                    raise FileNotFoundError(path)

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
