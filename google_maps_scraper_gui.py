import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import time
import logging
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import schedule

__version__ = "1.3.2"

# The whole UI sticks to exactly two accent colors: PRIMARY marks section
# headers/progress so related options are easy to scan, ACCENT marks the one
# primary action (Start).
COLOR_PRIMARY = "#2F6FA5"
COLOR_ACCENT = "#D9720C"

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
        self.root.geometry("1200x800")
        self.root.state('zoomed')  # Maximized

        self.scraper = None
        self.scraping = False
        self.cities_list = []
        self.logger = None
        self.gui_handler = None

        self.create_ui()

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
        """Search query and cities input — always visible, no tab switch needed"""
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
                with open(filename, 'w') as f:
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
                    f.write(f"proxy={self.proxy_var.get()}\n")
                    f.write(f"schedule={self.schedule_var.get()}\n")
                    f.write(f"sqlite={self.sqlite_var.get()}\n")
                    f.write(f"postgres={self.postgres_var.get()}\n")
                self.logger.info(f"Settings saved to {filename}")
                messagebox.showinfo("Success", "Settings saved successfully!")
            except Exception as e:
                self.logger.error(f"Error saving settings: {e}")
                messagebox.showerror("Error", f"Error saving settings: {e}")

    def load_settings(self):
        """Load settings from file"""
        filename = filedialog.askopenfilename(filetypes=[("Text files", "*.txt")])
        if filename:
            try:
                with open(filename, 'r') as f:
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
                with open(filename, 'w') as f:
                    f.write(logs)
                self.logger.info(f"Logs saved to {filename}")
                messagebox.showinfo("Success", "Logs saved successfully!")
            except Exception as e:
                self.logger.error(f"Error saving logs: {e}")
                messagebox.showerror("Error", f"Error saving logs: {e}")

    def get_cities_list(self):
        """Get cities from input"""
        if self.cities_mode_var.get() == "list":
            cities_text = self.cities_text.get(1.0, tk.END)
            cities = [c.strip() for c in cities_text.split('\n') if c.strip()]
        else:
            filename = self.cities_file_var.get()
            if not filename:
                messagebox.showerror("Error", "Please select a cities file")
                return []
            try:
                cities = self.load_cities_from_file(filename)
            except Exception as e:
                messagebox.showerror("Error", f"Error loading cities file: {e}")
                return []
        return cities

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

    def start_scraping(self):
        """Start scraping in a separate thread"""
        if not self.query_var.get():
            messagebox.showerror("Error", "Please enter a search query")
            return

        cities = self.get_cities_list()
        if not cities:
            messagebox.showerror("Error", "Please enter or select cities")
            return

        if not (self.export_csv_var.get() or self.export_json_var.get() or self.export_excel_var.get()
                or self.sqlite_var.get().strip() or self.postgres_var.get().strip()):
            messagebox.showerror("Error", "Please select at least one output format or database export")
            return

        self.scraping = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)

        # Run scraping in thread
        thread = threading.Thread(target=self.scrape_thread, args=(cities,), daemon=True)
        thread.start()

    def scrape_thread(self, cities):
        """Scraping thread: resolves options, then runs once or on a schedule"""
        try:
            # Import lazily so a missing google_maps_scraper.py doesn't crash the GUI on launch
            try:
                from google_maps_scraper import GoogleMapsScraper as Scraper
            except ImportError:
                self.logger.error("ERROR: google_maps_scraper.py not found in same folder!")
                self.root.after(0, lambda: messagebox.showerror(
                    "Error",
                    "google_maps_scraper.py not found!\n\nMake sure both files are in the same folder:\n"
                    "- google_maps_scraper.py\n- google_maps_scraper_gui.py"))
                return

            logging.getLogger('google_maps_scraper').setLevel(
                logging.DEBUG if self.verbose_var.get() else logging.INFO)

            if self.limit_var.get() > 0:
                cities = cities[:self.limit_var.get()]
                self.logger.info(f"Limited to first {len(cities)} cities")

            if self.test_var.get():
                cities = cities[:1]
                self.logger.info("TEST MODE: Processing only first city")

            proxy = self.proxy_var.get().strip() or None
            workers = max(1, self.workers_var.get())
            schedule_choice = self.schedule_var.get()

            if schedule_choice and schedule_choice != "None":
                self.run_scheduled_job(Scraper, cities, proxy, workers, schedule_choice)
            else:
                self.execute_job(Scraper, cities, proxy, workers)

        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            self.root.after(0, lambda: messagebox.showerror("Error", f"Error during scraping: {e}"))
        finally:
            self.root.after(0, self._reset_controls)

    def run_scheduled_job(self, Scraper, cities, proxy, workers, schedule_choice):
        """Run the job immediately, then keep re-running it on the chosen interval until Stop is pressed"""
        scheduler = schedule.Scheduler()  # dedicated instance: avoids leaking jobs into schedule's global state
        job = lambda: self.execute_job(Scraper, cities, proxy, workers)

        if schedule_choice == 'daily':
            scheduler.every().day.at("00:00").do(job)
        elif schedule_choice == 'hourly':
            scheduler.every().hour.do(job)
        elif schedule_choice == 'weekly':
            scheduler.every().week.do(job)

        self.logger.info(f"📅 Scheduler initialized. Job will run: {schedule_choice}")
        job()

        self.logger.info("⏳ Waiting for next scheduled run... (Stop cancels it)")
        while self.scraping:
            scheduler.run_pending()
            time.sleep(1)
        self.logger.info("Scheduler stopped")

    def execute_job(self, Scraper, cities, proxy, workers):
        """Scrape all cities once (single- or multi-threaded) and export the results"""
        total_cities = len(cities)
        all_results = []
        query = self.query_var.get()
        headless = self.headless_var.get()
        delay = self.delay_var.get()

        if workers > 1:
            self.logger.info(f"🚀 Starting multi-threaded mode with {workers} concurrent browsers...")

            def scrape_one_city(city):
                worker_scraper = Scraper(headless=headless, proxy=proxy)
                try:
                    businesses = worker_scraper.search_google_maps(query, city)
                    for business in businesses:
                        business['city'] = city
                    return businesses
                finally:
                    worker_scraper.close()

            completed = 0
            cities_iter = iter(cities)
            pending = {}

            with ThreadPoolExecutor(max_workers=workers) as executor:
                def submit_next():
                    city = next(cities_iter, None)
                    if city is not None:
                        pending[executor.submit(scrape_one_city, city)] = city

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

            if not self.scraping:
                self.logger.info("Scraping cancelled by user")
        else:
            scraper = Scraper(headless=headless, proxy=proxy)
            try:
                for i, city in enumerate(cities, 1):
                    if not self.scraping:
                        self.logger.info("Scraping cancelled by user")
                        break

                    progress = (i - 1) / total_cities * 100
                    self._update_progress_safe(progress, f"Processing city {i}/{total_cities}: {city}")
                    self.logger.info(f"Processing city {i}/{total_cities}: {city}")

                    try:
                        businesses = scraper.search_google_maps(query, city)
                        for business in businesses:
                            business['city'] = city
                        all_results.extend(businesses)
                        self.logger.info(f"Found {len(businesses)} businesses in {city}")

                        if i < total_cities:
                            time.sleep(delay)
                    except Exception as e:
                        self.logger.error(f"Error processing city {city}: {e}")
            finally:
                scraper.close()

        if all_results:
            self._update_progress_safe(100, "Saving results...")
            self.logger.info("Saving results...")
            self.save_results(Scraper, all_results)
            self.display_statistics(all_results)
            self._set_status_safe("✅ Scraping completed successfully!")
            self.logger.info("✅ Scraping completed successfully!")
            self.root.after(0, lambda: messagebox.showinfo(
                "Success", f"Scraping completed!\nTotal: {len(all_results)} companies found"))
        else:
            self._set_status_safe("❌ No results found")
            self.logger.warning("No results found")
            self.root.after(0, lambda: messagebox.showwarning("Warning", "No results found"))

        return all_results

    def save_results(self, Scraper, all_results):
        """Export via the shared Scraper class's own exporters, so CSV/JSON/Excel/SQLite/Postgres
        all use the same up-to-date field set (including city/hours/image) as the CLI."""
        exporter = Scraper.__new__(Scraper)  # skip __init__: exporting needs no browser
        exporter.results = all_results
        try:
            output = self.output_var.get()
            if self.export_csv_var.get():
                exporter.save_to_csv(f"{output}.csv")
            if self.export_json_var.get():
                exporter.save_to_json(f"{output}.json")
            if self.export_excel_var.get():
                exporter.save_to_excel(f"{output}.xlsx")

            sqlite_db = self.sqlite_var.get().strip()
            if sqlite_db:
                exporter.save_to_sqlite(sqlite_db)

            postgres_conn = self.postgres_var.get().strip()
            if postgres_conn:
                exporter.save_to_postgres(postgres_conn)
        except Exception as e:
            self.logger.error(f"Error saving results: {e}")

    def stop_scraping(self):
        """Stop scraping"""
        self.scraping = False
        self.logger.info("Stopping scraper...")
        self.status_var.set("Stopping...")

    def _reset_controls(self):
        """Reset UI to idle state - must run on the main thread"""
        self.progress_var.set(0)
        self.progress_label.config(text="0%")
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.scraping = False

    def _update_progress_safe(self, progress, status_text):
        """Schedule a progress/status update on the main thread"""
        def update():
            self.progress_var.set(progress)
            self.progress_label.config(text=f"{int(progress)}%")
            self.status_var.set(status_text)
        self.root.after(0, update)

    def _set_status_safe(self, text):
        self.root.after(0, lambda: self.status_var.set(text))

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
    root = tk.Tk()
    app = GoogleMapsScraperGUI(root)
    root.mainloop()
