import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import threading
import time
import csv
import json
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException, NoSuchElementException
from webdriver_manager.chrome import ChromeDriverManager
import logging
import random
import urllib.parse
from datetime import datetime

__version__ = "1.2.0"

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
            self.text_widget.config(state=tk.NORMAL)
            
            # Color code based on level
            if record.levelno >= logging.ERROR:
                self.text_widget.insert(tk.END, log_entry + '\n', 'error')
            elif record.levelno >= logging.WARNING:
                self.text_widget.insert(tk.END, log_entry + '\n', 'warning')
            elif record.levelno >= logging.INFO:
                self.text_widget.insert(tk.END, log_entry + '\n', 'info')
            else:
                self.text_widget.insert(tk.END, log_entry + '\n', 'debug')
            
            self.text_widget.see(tk.END)
            self.text_widget.config(state=tk.DISABLED)
            self.text_widget.update()
        except:
            pass

class GoogleMapsScraperGUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"Google Maps Scraper v{__version__}")
        self.root.geometry("1200x800")
        self.root.state('zoomed')  # Maximized
        
        self.scraper = None
        self.scraping = False
        self.cities_list = []
        self.logger = None
        self.gui_handler = None
        
        self.create_ui()
        self.setup_logging()
        
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
        
    def create_ui(self):
        """Create the GUI interface"""
        # Main container
        main_frame = ttk.Frame(self.root)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Top half - Options
        options_frame = ttk.LabelFrame(main_frame, text="Scraping Options", padding=10)
        options_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 10))
        
        # Create notebooks for sections
        notebook = ttk.Notebook(options_frame)
        notebook.pack(fill=tk.BOTH, expand=True)
        
        # Basic Settings Tab
        self.create_basic_settings_tab(notebook)
        
        # Advanced Settings Tab
        self.create_advanced_settings_tab(notebook)
        
        # Control Panel
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.BOTH, expand=False, pady=10)
        
        # Progress Bar
        progress_frame = ttk.Frame(control_frame)
        progress_frame.pack(fill=tk.X, pady=(0, 10))
        
        ttk.Label(progress_frame, text="Progress:").pack(side=tk.LEFT, padx=5)
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.progress_label = ttk.Label(progress_frame, text="0%")
        self.progress_label.pack(side=tk.LEFT, padx=5)
        
        # Buttons
        button_frame = ttk.Frame(control_frame)
        button_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.start_button = ttk.Button(button_frame, text="▶ Start Scraping", command=self.start_scraping)
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
        
        # Bottom half - Logs
        log_frame = ttk.LabelFrame(main_frame, text="Logs", padding=10)
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_text = scrolledtext.ScrolledText(log_frame, height=15, state=tk.DISABLED)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        
        # Configure text tags for colors
        self.log_text.tag_config('info', foreground='black')
        self.log_text.tag_config('warning', foreground='orange')
        self.log_text.tag_config('error', foreground='red')
        self.log_text.tag_config('debug', foreground='gray')
        
        # Update logger with log_text widget
        self.setup_logging()
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ttk.Label(self.root, textvariable=self.status_var, relief=tk.SUNKEN)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
    def create_basic_settings_tab(self, notebook):
        """Create basic settings tab"""
        frame = ttk.Frame(notebook, padding=15)
        notebook.add(frame, text="Basic Settings")
        
        # Search Query
        ttk.Label(frame, text="Search Query:").grid(row=0, column=0, sticky=tk.W, pady=5)
        self.query_var = tk.StringVar(value="sustainability companies")
        ttk.Entry(frame, textvariable=self.query_var, width=40).grid(row=0, column=1, sticky=tk.EW, pady=5, padx=5)
        
        # Cities input method
        ttk.Label(frame, text="Cities Input:").grid(row=1, column=0, sticky=tk.W, pady=5)
        input_frame = ttk.Frame(frame)
        input_frame.grid(row=1, column=1, sticky=tk.EW, pady=5, padx=5)
        
        self.cities_mode_var = tk.StringVar(value="list")
        ttk.Radiobutton(input_frame, text="Enter Cities", variable=self.cities_mode_var, 
                       value="list", command=self.update_cities_input).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(input_frame, text="From File", variable=self.cities_mode_var, 
                       value="file", command=self.update_cities_input).pack(side=tk.LEFT, padx=10)
        
        # Cities text area or file path
        ttk.Label(frame, text="Cities:").grid(row=2, column=0, sticky=tk.NW, pady=5)
        cities_frame = ttk.Frame(frame)
        cities_frame.grid(row=2, column=1, sticky=tk.EW, pady=5, padx=5)
        
        self.cities_text = tk.Text(cities_frame, height=4, width=40)
        self.cities_text.pack(fill=tk.BOTH, expand=True)
        
        self.cities_file_var = tk.StringVar(value="")
        self.cities_file_entry = ttk.Entry(cities_frame, textvariable=self.cities_file_var)
        
        ttk.Button(cities_frame, text="Browse...", command=self.browse_cities_file).pack(pady=5)
        
        # Default: show text area
        self.cities_text.pack()
        
        # Sample text
        self.cities_text.insert(tk.END, "New York, USA\nLondon, UK\nTokyo, Japan\nParis, France")
        
        # Output Options
        ttk.Label(frame, text="Output Filename:").grid(row=3, column=0, sticky=tk.W, pady=5)
        self.output_var = tk.StringVar(value="google_maps_results")
        ttk.Entry(frame, textvariable=self.output_var, width=40).grid(row=3, column=1, sticky=tk.EW, pady=5, padx=5)
        
        # Format
        ttk.Label(frame, text="Output Format:").grid(row=4, column=0, sticky=tk.W, pady=5)
        self.format_var = tk.StringVar(value="both")
        format_frame = ttk.Frame(frame)
        format_frame.grid(row=4, column=1, sticky=tk.EW, pady=5, padx=5)
        
        ttk.Radiobutton(format_frame, text="CSV", variable=self.format_var, value="csv").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(format_frame, text="JSON", variable=self.format_var, value="json").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(format_frame, text="Both", variable=self.format_var, value="both").pack(side=tk.LEFT, padx=10)
        
        frame.columnconfigure(1, weight=1)
        
    def create_advanced_settings_tab(self, notebook):
        """Create advanced settings tab"""
        frame = ttk.Frame(notebook, padding=15)
        notebook.add(frame, text="Advanced Settings")
        
        # Headless mode
        self.headless_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Headless Mode (Hidden Browser)", variable=self.headless_var).pack(anchor=tk.W, pady=5)
        
        # Test mode
        self.test_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Test Mode (First City Only)", variable=self.test_var).pack(anchor=tk.W, pady=5)
        
        # Verbose mode
        self.verbose_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Verbose Logging", variable=self.verbose_var).pack(anchor=tk.W, pady=5)
        
        # Limit cities
        ttk.Label(frame, text="Limit Cities (0 = no limit):").pack(anchor=tk.W, pady=(15, 5))
        self.limit_var = tk.IntVar(value=0)
        ttk.Spinbox(frame, from_=0, to=100, textvariable=self.limit_var, width=10).pack(anchor=tk.W, padx=20, pady=5)
        
        # Delay between cities
        ttk.Label(frame, text="Delay Between Cities (seconds):").pack(anchor=tk.W, pady=(15, 5))
        self.delay_var = tk.IntVar(value=10)
        delay_frame = ttk.Frame(frame)
        delay_frame.pack(anchor=tk.W, padx=20, pady=5, fill=tk.X)
        
        self.delay_scale = ttk.Scale(delay_frame, from_=1, to=60, orient=tk.HORIZONTAL, 
                                     variable=self.delay_var, command=self.update_delay_label)
        self.delay_scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        
        self.delay_label = ttk.Label(delay_frame, text="10s", width=5)
        self.delay_label.pack(side=tk.LEFT, padx=5)
        
        # Max results
        ttk.Label(frame, text="Max Results Per City (0 = no limit):").pack(anchor=tk.W, pady=(15, 5))
        self.max_results_var = tk.IntVar(value=0)
        ttk.Spinbox(frame, from_=0, to=1000, textvariable=self.max_results_var, width=10).pack(anchor=tk.W, padx=20, pady=5)
        
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
            
    def clear_form(self):
        """Clear all form fields"""
        self.query_var.set("")
        self.cities_text.delete(1.0, tk.END)
        self.output_var.set("google_maps_results")
        self.format_var.set("both")
        self.headless_var.set(True)
        self.test_var.set(False)
        self.verbose_var.set(False)
        self.limit_var.set(0)
        self.delay_var.set(10)
        self.max_results_var.set(0)
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
                    f.write(f"format={self.format_var.get()}\n")
                    f.write(f"headless={self.headless_var.get()}\n")
                    f.write(f"test={self.test_var.get()}\n")
                    f.write(f"verbose={self.verbose_var.get()}\n")
                    f.write(f"limit={self.limit_var.get()}\n")
                    f.write(f"delay={self.delay_var.get()}\n")
                    f.write(f"max_results={self.max_results_var.get()}\n")
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
                        key, value = line.strip().split('=')
                        if key == "query":
                            self.query_var.set(value)
                        elif key == "output":
                            self.output_var.set(value)
                        elif key == "format":
                            self.format_var.set(value)
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
            
        self.scraping = True
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        
        # Run scraping in thread
        thread = threading.Thread(target=self.scrape_thread, args=(cities,), daemon=True)
        thread.start()
        
    def scrape_thread(self, cities):
        """Scraping thread"""
        try:
            # Try to import the scraper
            try:
                from google_maps_scraper import GoogleMapsScraper as Scraper
            except ImportError:
                self.logger.error("ERROR: google_maps_scraper.py not found in same folder!")
                messagebox.showerror("Error", "google_maps_scraper.py not found!\n\nMake sure both files are in the same folder:\n- google_maps_scraper.py\n- google_maps_scraper_gui.py")
                return
            
            self.status_var.set("Initializing scraper...")
            self.logger.info("Initializing scraper...")
            
            scraper = Scraper(headless=self.headless_var.get())
            
            # Apply limit if set
            if self.limit_var.get() > 0:
                cities = cities[:self.limit_var.get()]
                self.logger.info(f"Limited to first {len(cities)} cities")
                
            # Apply test mode
            if self.test_var.get():
                cities = cities[:1]
                self.logger.info("TEST MODE: Processing only first city")
                
            total_cities = len(cities)
            results = []
            
            for i, city in enumerate(cities, 1):
                if not self.scraping:
                    self.logger.info("Scraping cancelled by user")
                    break
                    
                # Update progress
                progress = (i - 1) / total_cities * 100
                self.progress_var.set(progress)
                self.progress_label.config(text=f"{int(progress)}%")
                self.status_var.set(f"Processing city {i}/{total_cities}: {city}")
                self.root.update()
                
                self.logger.info(f"Processing city {i}/{total_cities}: {city}")
                
                try:
                    businesses = scraper.search_google_maps(self.query_var.get(), city)
                    for business in businesses:
                        business['city'] = city
                        results.append(business)
                    
                    self.logger.info(f"Found {len(businesses)} businesses in {city}")
                    
                    if i < total_cities:
                        time.sleep(self.delay_var.get())
                        
                except Exception as e:
                    self.logger.error(f"Error processing city {city}: {e}")
                    continue
                    
            scraper.close()
            
            if results:
                # Save results
                self.status_var.set("Saving results...")
                self.logger.info("Saving results...")
                
                output = self.output_var.get()
                if self.format_var.get() in ['csv', 'both']:
                    self.save_csv(output, results)
                if self.format_var.get() in ['json', 'both']:
                    self.save_json(output, results)
                    
                # Display statistics
                self.display_statistics(results)
                
                self.status_var.set("✅ Scraping completed successfully!")
                self.logger.info("✅ Scraping completed successfully!")
                messagebox.showinfo("Success", f"Scraping completed!\nTotal: {len(results)} companies found")
            else:
                self.status_var.set("❌ No results found")
                self.logger.warning("No results found")
                messagebox.showwarning("Warning", "No results found")
                
        except Exception as e:
            self.logger.error(f"Error during scraping: {e}")
            messagebox.showerror("Error", f"Error during scraping: {e}")
        finally:
            self.progress_var.set(0)
            self.progress_label.config(text="0%")
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)
            self.scraping = False
            
    def stop_scraping(self):
        """Stop scraping"""
        self.scraping = False
        self.logger.info("Stopping scraper...")
        self.status_var.set("Stopping...")
        
    def save_csv(self, output, results):
        """Save results to CSV with new format"""
        try:
            filename = f"{output}.csv"
            fieldnames = ['id', 'name', 'description', 'rating', 'reviewCount', 'phone', 'email', 'website', 'address', 'image', 'verified', 'tags']
            
            with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                
                for idx, result in enumerate(results, 1):
                    row = {
                        'id': idx,
                        'name': result.get('company', ''),
                        'description': '',  # Not available from Google Maps
                        'rating': result.get('rating', ''),
                        'reviewCount': result.get('reviews', ''),
                        'phone': result.get('phone', ''),
                        'email': result.get('email', ''),
                        'website': result.get('website', ''),
                        'address': result.get('address', ''),
                        'image': '',  # Not available from Google Maps
                        'verified': 'true' if result.get('rating') else 'false',
                        'tags': ''  # Not available from Google Maps
                    }
                    writer.writerow(row)
                    
            self.logger.info(f"Results saved to {filename}")
        except Exception as e:
            self.logger.error(f"Error saving CSV: {e}")
            
    def save_json(self, output, results):
        """Save results to JSON with new format"""
        try:
            filename = f"{output}.json"
            formatted_results = []
            
            for idx, result in enumerate(results, 1):
                formatted_result = {
                    'id': idx,
                    'name': result.get('company', ''),
                    'description': '',  # Not available from Google Maps
                    'rating': float(result.get('rating', 0)) if result.get('rating') else 0,
                    'reviewCount': int(result.get('reviews', 0)) if result.get('reviews') else 0,
                    'phone': result.get('phone', ''),
                    'email': result.get('email', ''),
                    'website': result.get('website', ''),
                    'address': result.get('address', ''),
                    'image': '',  # Not available from Google Maps
                    'verified': bool(result.get('rating')),
                    'tags': []  # Not available from Google Maps
                }
                formatted_results.append(formatted_result)
                
            with open(filename, 'w', encoding='utf-8') as jsonfile:
                json.dump(formatted_results, jsonfile, indent=2, ensure_ascii=False)
                
            self.logger.info(f"Results saved to {filename}")
        except Exception as e:
            self.logger.error(f"Error saving JSON: {e}")
            
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
        with_phones = len([r for r in results if r['phone']])
        with_addresses = len([r for r in results if r['address']])
        
        self.logger.info(f"⭐ Companies with ratings: {with_ratings} ({with_ratings/len(results)*100:.1f}%)")
        self.logger.info(f"💬 Companies with reviews: {with_reviews} ({with_reviews/len(results)*100:.1f}%)")
        self.logger.info(f"🌐 Companies with websites: {with_websites} ({with_websites/len(results)*100:.1f}%)")
        self.logger.info(f"📞 Companies with phones: {with_phones} ({with_phones/len(results)*100:.1f}%)")
        self.logger.info(f"📍 Companies with addresses: {with_addresses} ({with_addresses/len(results)*100:.1f}%)")
        
if __name__ == "__main__":
    root = tk.Tk()
    app = GoogleMapsScraperGUI(root)
    root.mainloop()