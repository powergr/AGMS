import ctypes
import os
import re
import sys
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import time
import logging
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import schedule

__version__ = "1.3.2"

# The whole UI sticks to exactly two accent colors: PRIMARY marks section
# headers/progress so related options are easy to scan, ACCENT marks the one
# primary action (Start).
COLOR_PRIMARY = "#2F6FA5"
COLOR_ACCENT = "#D9720C"

APP_DIR = os.path.dirname(os.path.abspath(__file__))


def set_windows_app_id():
    """Give the process its own taskbar identity, so Windows shows our icon instead of python.exe's"""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("powergr.agms.gui")
        except (AttributeError, OSError):
            pass


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
class ScrapeSettings:
    """Every option, read once on the main thread when Start is pressed"""
    query: str
    cities: tuple
    output: str
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

        self.create_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _set_window_icon(self):
        try:
            self.root.iconbitmap(os.path.join(APP_DIR, "icon.ico"))
        except tk.TclError as e:
            print(f"Could not load icon: {e}")

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
        """Search query and cities input, always visible with no tab switch needed"""
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Search Query:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.query_var = tk.StringVar(value="technology companies")
        ttk.Entry(frame, textvariable=self.query_var).grid(row=0, column=1, sticky=tk.EW, pady=5, padx=5)

        ttk.Label(frame, text="Cities Input:").grid(row=1, column=0, sticky=tk.W, pady=5)
        input_frame = ttk.Frame(frame)
        input_frame.grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)

        self.cities_mode_var = tk.StringVar(value="list")
        ttk.Radiobutton(input_frame, text="Enter Cities", variable=self.cities_mode_var,
                       value="list", command=self.update_cities_input).pack(side=tk.LEFT, padx=(0, 15))
        ttk.Radiobutton(input_frame, text="From File", variable=self.cities_mode_var,
                       value="file", command=self.update_cities_input).pack(side=tk.LEFT)

        ttk.Label(frame, text="Cities:").grid(row=2, column=0, sticky=tk.NW, pady=5)
        cities_frame = ttk.Frame(frame)
        cities_frame.grid(row=2, column=1, sticky=tk.EW, pady=5, padx=5)

        self.cities_text = tk.Text(cities_frame, height=3, width=40)
        self.cities_text.pack(fill=tk.BOTH, expand=True)
        self.cities_text.insert(tk.END, "New York, USA\nLondon, UK\nTokyo, Japan\nParis, France")

        self.cities_file_var = tk.StringVar(value="")
        self.cities_file_entry = ttk.Entry(cities_frame, textvariable=self.cities_file_var)
        ttk.Button(cities_frame, text="Browse...", command=self.browse_cities_file).pack(pady=5)

    def _build_output_section(self, frame):
        """Output filename, export formats, and optional database export"""
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Output Filename:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.output_var = tk.StringVar(value="google_maps_results")
        ttk.Entry(frame, textvariable=self.output_var).grid(row=0, column=1, sticky=tk.EW, pady=5, padx=5)

        # Independent checkboxes so any combination can be exported at once,
        # matching the CLI's `--format csv json excel` style
        ttk.Label(frame, text="Format:").grid(row=1, column=0, sticky=tk.W, pady=5)
        self.export_csv_var = tk.BooleanVar(value=True)
        self.export_json_var = tk.BooleanVar(value=True)
        self.export_excel_var = tk.BooleanVar(value=False)
        format_frame = ttk.Frame(frame)
        format_frame.grid(row=1, column=1, sticky=tk.W, pady=5, padx=5)
        ttk.Checkbutton(format_frame, text="CSV", variable=self.export_csv_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(format_frame, text="JSON", variable=self.export_json_var).pack(side=tk.LEFT, padx=(0, 10))
        ttk.Checkbutton(format_frame, text="Excel", variable=self.export_excel_var).pack(side=tk.LEFT)

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
        self.output_var.set("google_maps_results")
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

    def _collect_settings(self):
        """Read and validate every option on the main thread, so worker threads never touch Tk variables.
        Raises ValueError with a message that is safe to show the user."""
        query = self.query_var.get().strip()
        if not query:
            raise ValueError("Please enter a search query.")

        cities = self.get_cities_list()
        if not cities:
            raise ValueError("Please enter or select cities.")

        formats = tuple(name for name, var in (("csv", self.export_csv_var), ("json", self.export_json_var),
                                               ("excel", self.export_excel_var)) if var.get())
        sqlite_db = self.sqlite_var.get().strip()
        postgres_conn = self.postgres_var.get().strip()
        if not (formats or sqlite_db or postgres_conn):
            raise ValueError("Please select at least one output format or database export.")

        return ScrapeSettings(
            query=query,
            cities=tuple(cities),
            output=self.output_var.get().strip() or "google_maps_results",
            formats=formats,
            sqlite_db=sqlite_db,
            postgres_conn=postgres_conn,
            headless=self.headless_var.get(),
            verbose=self.verbose_var.get(),
            test_mode=self.test_var.get(),
            limit=self._read_int(self.limit_var, "Limit Cities", 0),
            max_results=self._read_int(self.max_results_var, "Max Results Per City", 0),
            delay=self._read_int(self.delay_var, "Delay Between Cities", 0),
            workers=self._read_int(self.workers_var, "Parallel Workers", 1),
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

        self.scraping = True
        self.stop_event.clear()
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        self.worker_thread = threading.Thread(target=self.scrape_thread, args=(settings,), daemon=True)
        self.worker_thread.start()

    def scrape_thread(self, settings):
        """Scraping thread: applies the limits, then runs once or on a schedule"""
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
                self.execute_job(Scraper, cities, settings)

        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            self._show_dialog_safe("showerror", "Error", f"Error during scraping: {e}")
        finally:
            self._post(self._reset_controls)

    def run_scheduled_job(self, Scraper, cities, settings):
        """Run the job immediately, then keep re-running it on the chosen interval until Stop is pressed"""
        scheduler = schedule.Scheduler()  # dedicated instance: avoids leaking jobs into schedule's global state

        def job():
            try:
                self.execute_job(Scraper, cities, settings, notify=False)
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

    def execute_job(self, Scraper, cities, settings, notify=True):
        """Scrape all cities once (single- or multi-threaded) and export the results.
        notify=False (scheduled runs) skips the modal dialogs, which would pile up unattended."""
        total_cities = len(cities)
        all_results = []
        proxy = settings.proxy or None
        query, headless, delay, workers = settings.query, settings.headless, settings.delay, settings.workers
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

        if all_results:
            self._update_progress_safe(100, "Saving results...")
            self.logger.info("Saving results...")
            failed_exports = self.save_results(Scraper, all_results, settings)
            self.display_statistics(all_results)
            icon, done_text = ("⏹", "Scraping stopped. Partial results were saved.") if cancelled \
                else ("✅", "Scraping completed successfully!")
            if failed_exports:
                icon, done_text = "⚠️", f"Scraping finished, but these exports failed: {', '.join(failed_exports)}. See the log."
            self._set_status_safe(f"{icon} {done_text}")
            self.logger.info(f"{icon} {done_text}")
            if notify:
                kind, title = ("showwarning", "Warning") if failed_exports else ("showinfo", "Success")
                self._show_dialog_safe(kind, title, f"{done_text}\nTotal: {len(all_results)} companies found")
        else:
            self._set_status_safe("❌ No results found")
            self.logger.warning("No results found")
            if notify:
                self._show_dialog_safe("showwarning", "Warning", "No results found")

        return all_results

    def save_results(self, Scraper, all_results, settings):
        """Export through the shared Scraper class, so CSV/JSON/Excel/SQLite/Postgres
        all use the same field set (including city/hours/image) as the CLI.
        Returns the names of any exports that failed."""
        exporter = Scraper.for_export(all_results)
        return exporter.export_all(settings.output, settings.formats,
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
        self.scraping = False

    def _post(self, func, *args):
        """Run func on the Tk main thread. Quietly does nothing if the window is already gone."""
        try:
            self.root.after(0, func, *args)
        except (RuntimeError, tk.TclError):
            pass

    def _update_progress_safe(self, progress, status_text):
        """Schedule a progress/status update on the main thread"""
        def update():
            self.progress_var.set(progress)
            self.progress_label.config(text=f"{int(progress)}%")
            self.status_var.set(status_text)
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

if __name__ == "__main__":
    set_windows_app_id()
    root = tk.Tk()
    app = GoogleMapsScraperGUI(root)
    root.mainloop()
