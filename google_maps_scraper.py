import time
import csv
import json
import re
import argparse
import sys
import random
import urllib.parse
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- NEW DEPENDENCIES ---
import requests
from bs4 import BeautifulSoup
import schedule
import pandas as pd
try:
    from sqlalchemy import create_engine
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.keys import Keys
from selenium.common.exceptions import TimeoutException
from webdriver_manager.chrome import ChromeDriverManager

# Version
__version__ = "1.3.3"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
ASSET_SUFFIXES = ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.css', '.js', '.ico', '.woff', '.woff2', '.ttf')
JUNK_EMAIL_DOMAINS = ('sentry.io', 'wixpress.com', 'example.com', 'domain.com', 'yourdomain.com', 'email.com', 'test.com')
PREFERRED_EMAIL_NAMES = ('info', 'contact', 'hello', 'office', 'sales', 'enquiries', 'inquiries', 'support', 'admin', 'mail')

# Social, booking, delivery and review sites that a Maps page links to; none is the business's own website
EXCLUDED_SITE_BRANDS = (
    'facebook', 'instagram', 'twitter', 'x', 'linkedin', 'youtube', 'tiktok', 'pinterest', 'whatsapp',
    'tripadvisor', 'yelp', 'foursquare', 'opentable', 'resy', 'booking', 'expedia',
    'ubereats', 'doordash', 'grubhub', 'deliveroo', 'wolt', 'wikipedia',
)
PUBLIC_SUFFIX_LABELS = ('co', 'com', 'org', 'net', 'gov', 'ac')


def pick_best_email(candidates, website=None):
    """Choose one contact email from scraped candidates: drop junk, then prefer an address on the
    business's own domain, then common contact names. The same input always gives the same answer."""
    cleaned = set()
    for raw in candidates:
        email = urllib.parse.unquote(raw).strip().strip('.,;:<>()[]"\'').lower()
        if not EMAIL_PATTERN.fullmatch(email) or email.endswith(ASSET_SUFFIXES):
            continue
        domain = email.rsplit('@', 1)[1]
        if any(domain == junk or domain.endswith('.' + junk) for junk in JUNK_EMAIL_DOMAINS):
            continue
        cleaned.add(email)
    if not cleaned:
        return ''

    site_host = urllib.parse.urlparse(website).netloc.lower().split(':')[0] if website else ''
    if site_host.startswith('www.'):
        site_host = site_host[4:]

    def rank(email):
        name, domain = email.split('@')
        on_own_site = bool(site_host) and (
            domain == site_host or domain.endswith('.' + site_host) or site_host.endswith('.' + domain))
        name_rank = PREFERRED_EMAIL_NAMES.index(name) if name in PREFERRED_EMAIL_NAMES else len(PREFERRED_EMAIL_NAMES)
        return (not on_own_site, name_rank, email)

    return min(cleaned, key=rank)

def normalize_postgres_url(connection_string):
    """SQLAlchemy 2.1 made a plain postgresql:// URL use the newer psycopg driver, but this project
    installs psycopg2, so name that driver. The postgres:// form that many hosts hand out is accepted too."""
    for prefix in ("postgresql://", "postgres://"):
        if connection_string.startswith(prefix):
            return "postgresql+psycopg2://" + connection_string[len(prefix):]
    return connection_string


class GoogleMapsScraper:
    def __init__(self, headless=True, proxy=None, stop_event=None):
        self.proxy = proxy
        self.stop_event = stop_event if stop_event is not None else threading.Event()
        self.setup_driver(headless)
        self.results = []

    @classmethod
    def for_export(cls, results):
        """An instance that only holds results for the save_to_* methods. Skips __init__, so no browser starts."""
        exporter = cls.__new__(cls)
        exporter.results = results
        return exporter

    @staticmethod
    def jittered_delay(delay):
        """The configured delay, varied by 20% either way so requests are not perfectly regular"""
        return random.uniform(delay * 0.8, delay * 1.2)

    def should_stop(self):
        return self.stop_event.is_set()

    def pause(self, seconds):
        """Sleep that wakes immediately when a stop is requested"""
        self.stop_event.wait(seconds)
        
    def setup_driver(self, headless=True):
        chrome_options = Options()
        if headless:
            chrome_options.add_argument("--headless")
        
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        # --- NEW: Proxy Support ---
        if self.proxy:
            chrome_options.add_argument(f'--proxy-server={self.proxy}')
            logger.info(f"Using proxy: {self.proxy.rpartition('@')[2]}")  # never log a user:password part
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        chrome_options.add_argument(f"--user-agent={user_agent}")

        # Keep Maps in English: the rating, hours and photo selectors below match English labels
        chrome_options.add_argument("--lang=en-US")
        chrome_options.add_experimental_option("prefs", {"intl.accept_languages": "en-US,en"})
        
        try:
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            self.wait = WebDriverWait(self.driver, 20)
            logger.info("Chrome driver initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Chrome driver: {e}")
            raise
    
    def handle_cookie_consent(self):
        """Automatically handle Google's cookie consent popup"""
        try:
            self.pause(2)
            
            consent_buttons = [
                'button[aria-label*="Reject all"]',
                'button[aria-label*="reject all"]',
                'button[aria-label*="Accept all"]',
                'button[aria-label*="accept all"]',
                'form[action*="consent"] button',
                'button.VfPpkd-LgbsSe'
            ]
            
            for selector in consent_buttons:
                try:
                    buttons = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    for button in buttons:
                        if button.is_displayed() and button.is_enabled():
                            button_text = button.text.lower()
                            if 'reject' in button_text or 'accept' in button_text:
                                self.driver.execute_script("arguments[0].click();", button)
                                logger.info(f"Clicked cookie consent button: {button.text}")
                                self.pause(2)
                                return
                except:
                    continue
            
            try:
                xpath_selectors = [
                    "//button[contains(text(), 'Reject all')]",
                    "//button[contains(text(), 'Accept all')]",
                    "//button[contains(@aria-label, 'Reject')]",
                    "//button[contains(@aria-label, 'Accept')]"
                ]
                
                for xpath in xpath_selectors:
                    try:
                        button = self.driver.find_element(By.XPATH, xpath)
                        if button.is_displayed():
                            self.driver.execute_script("arguments[0].click();", button)
                            logger.info("Clicked cookie consent button via XPath")
                            self.pause(2)
                            return
                    except:
                        continue
            except:
                pass
            
            logger.debug("No cookie consent popup found or already handled")
            
        except Exception as e:
            logger.debug(f"Error handling cookie consent: {e}")
    
    def search_google_maps(self, query, city, max_results=None):
        max_results = max_results if max_results and max_results > 0 else None
        search_term = f"{query} {city}"
        logger.info(f"Searching for: {search_term}")
        
        try:
            # --- FIX: Use Direct URL Search instead of typing ---
            # This is much more reliable and skips the search box entirely
            encoded_query = urllib.parse.quote_plus(search_term)
            search_url = f"https://www.google.com/maps/search/{encoded_query}?hl=en"
            
            self.driver.get(search_url)
            self.pause(random.uniform(4, 6))
            if self.should_stop():
                return []
            
            # Handle any cookie popups that appear
            self.handle_cookie_consent()
            
            # Wait for the results panel to load
            try:
                # Look for the feed container or actual business listings
                self.wait.until(EC.presence_of_element_located((
                    By.CSS_SELECTOR, 'div[role="feed"], div.m6QErb[aria-label], .hfpxzc'
                )))
                logger.info("Search results loaded")
            except TimeoutException:
                logger.warning("Results panel not found (might be no results for this query)")
                return []
            
            self.load_all_results(max_results)
            if self.should_stop():
                return []
            businesses = self.extract_all_businesses(max_results)
            return businesses
            
        except Exception as e:
            logger.error(f"Error searching for {search_term}: {e}")
            return []
    
    def load_all_results(self, max_results=None):
        try:
            self.pause(3)
            scroll_attempts = 0
            max_scrolls = 25
            no_change_count = 0
            
            while scroll_attempts < max_scrolls and not self.should_stop():
                current_elements = self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc')
                current_count = len(current_elements)
                if max_results and current_count >= max_results:
                    logger.info(f"Loaded {current_count} businesses, enough for the limit of {max_results}")
                    break
                
                logger.info(f"Scroll {scroll_attempts + 1}: Currently {current_count} businesses visible")
                
                if current_count > 0:
                    # Method 1: Scroll the last known business into view
                    last_element = current_elements[-1]
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView(true);", last_element)
                    except:
                        pass
                    
                    # Method 2: Send PAGE_DOWN key directly to the element (simulates a human pressing down)
                    try:
                        last_element.send_keys(Keys.PAGE_DOWN)
                        last_element.send_keys(Keys.PAGE_DOWN)
                    except:
                        pass
                
                # Method 3: Fallback standard container scroll
                try:
                    scrollable_container = self.driver.find_element(By.CSS_SELECTOR, 'div[role="feed"]')
                    self.driver.execute_script("arguments[0].scrollTop = arguments[0].scrollHeight", scrollable_container)
                except:
                    pass
                
                # Wait for Google to fetch the new data from their server
                self.pause(random.uniform(4, 6))
                
                new_elements = self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc')
                new_count = len(new_elements)
                
                if new_count > current_count:
                    logger.info(f"[OK] Loaded {new_count - current_count} more businesses (total: {new_count})")
                    no_change_count = 0
                else:
                    no_change_count += 1
                    logger.info(f"No new businesses loaded (attempt {no_change_count}/5)")
                    
                    if no_change_count >= 5:
                        logger.info(f"No new results after 5 attempts, stopping scroll")
                        break
                
                scroll_attempts += 1
                
                # Check if Google explicitly says there are no more results
                try:
                    end_messages = self.driver.find_elements(By.XPATH, 
                        "//*[contains(text(), 'reached the end') or contains(text(), 'no more results')]")
                    if end_messages:
                        logger.info("Reached explicitly stated end of results")
                        break
                except:
                    pass
            
            final_count = len(self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc'))
            logger.info(f"[OK] Finished scrolling: Total {final_count} businesses found")
            
        except Exception as e:
            logger.error(f"Error during scrolling: {e}")
            import traceback
            traceback.print_exc()
    
    def extract_all_businesses(self, max_results=None):
        businesses = []
        
        try:
            # Get the total count first
            total_elements = len(self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc'))
            logger.info(f"Found {total_elements} businesses. Starting extraction...")
            
            # Loop using index, re-fetching elements every time to prevent StaleElementReference
            for i in range(total_elements):
                if self.should_stop():
                    logger.info(f"Stop requested: keeping the {len(businesses)} businesses extracted so far")
                    break
                if max_results and len(businesses) >= max_results:
                    break
                try:
                    logger.info(f"Processing business {i+1}/{total_elements}")
                    
                    # RE-FETCH the elements list to ensure they are fresh
                    current_elements = self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc')
                    
                    if i >= len(current_elements):
                        logger.warning(f"Element {i} no longer exists in DOM. Skipping.")
                        continue
                        
                    element = current_elements[i]
                    business_data = self.extract_business_info(element)
                    
                    if business_data and business_data.get('company'):
                        businesses.append(business_data)
                        logger.info(f"[OK] Extracted: {business_data['company']}")
                        if business_data.get('website'):
                            logger.info(f"  Website: {business_data['website']}")
                        if business_data.get('email'):
                            logger.info(f"  Email: {business_data['email']}")
                    
                    self.pause(random.uniform(1.5, 2.5))
                    
                except Exception as e:
                    logger.warning(f"Error processing business {i+1}: {e}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error extracting businesses: {e}")
        
        return businesses
    
    def extract_business_info(self, element):
        business_data = {
            'company': '',
            'address': '',
            'phone': '',
            'website': '',
            'email': '',
            'rating': '',
            'reviews': '',
            'hours': '',     # NEW
            'image': ''      # NEW
        }
        
        try:
            aria_label = element.get_attribute('aria-label')
            if aria_label:
                business_data['company'] = aria_label.strip()
                
                rating_match = re.search(r'(\d+\.?\d*)\s*star', aria_label, re.IGNORECASE)
                if rating_match:
                    business_data['rating'] = rating_match.group(1)
                
                review_patterns = [
                    r'(\d+)\s+review',
                    r'\((\d+)\)',
                    r'(\d+)\s+rating',
                    r'(\d+)\s+avis'
                ]
                
                for pattern in review_patterns:
                    review_match = re.search(pattern, aria_label, re.IGNORECASE)
                    if review_match:
                        business_data['reviews'] = review_match.group(1)
                        logger.debug(f"Found reviews from aria-label: {business_data['reviews']}")
                        break
            
            if business_data['company']:
                try:
                    # Scroll into view
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
                    self.pause(0.6)

                    # Try normal click first, fall back to JS click
                    try:
                        element.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", element)

                    self.pause(random.uniform(3.5, 5.5))
                    if self.should_stop():
                        return None  # the panel may not have loaded yet, so this business would hold stale data
                    self.extract_detailed_info(business_data)
                except Exception as e:
                    logger.warning(f"Could not get detailed info for {business_data['company']}: {e}")
            
            return business_data
            
        except Exception as e:
            logger.warning(f"Error extracting business info: {e}")
            return None
    
    def extract_detailed_info(self, business_data):
        try:
            self.pause(3)
            self.extract_address(business_data)
            self.extract_phone(business_data)
            self.extract_website_comprehensive(business_data)
            self.extract_rating_reviews(business_data)
            
            # --- NEW Features ---
            self.extract_hours(business_data)
            self.extract_image(business_data)
            
        except Exception as e:
            logger.warning(f"Error extracting detailed info: {e}")
    
    def extract_address(self, business_data):
        address_selectors = [
            '[data-item-id="address"] .Io6YTe',
            '[data-item-id="address"]',
            'button[data-item-id="address"]',
            '.Io6YTe.fontBodyMedium'
        ]
        
        for selector in address_selectors:
            try:
                address_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                address_text = address_element.text.strip()
                if address_text and len(address_text) > 10:
                    business_data['address'] = address_text
                    break
            except:
                continue
    
    def extract_phone(self, business_data):
        phone_selectors = [
            '[data-item-id*="phone"] .Io6YTe',
            'button[data-item-id*="phone"]',
            'a[href^="tel:"]'
        ]
        
        for selector in phone_selectors:
            try:
                phone_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                phone_text = phone_element.text.strip()
                if phone_text:
                    business_data['phone'] = phone_text
                    break
            except:
                continue

    # --- NEW: Extract Hours ---
    def extract_hours(self, business_data):
        try:
            # 1. Try the standard Google Maps hours container first
            try:
                oh_elements = self.driver.find_elements(By.CSS_SELECTOR, '[data-item-id="oh"]')
                if oh_elements:
                    text = oh_elements[0].get_attribute('aria-label')
                    if not text:
                        text = oh_elements[0].text
                    if text and len(text) > 3:
                        business_data['hours'] = text.replace('\u202f', ' ').replace('\n', ' - ').strip()
                        return
            except:
                pass

            # 2. Try generic text matching for hours
            hour_selectors = [
                'div[aria-label*="Open ⋅ Closes"]',
                'div[aria-label*="Closed ⋅ Opens"]',
                'div[aria-label*="Open 24 hours"]',
                'div[aria-label*="Temporarily closed"]',
                '.t39OBf'
            ]
            
            for selector in hour_selectors:
                try:
                    elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                    text = elem.get_attribute('aria-label')
                    if not text:
                        text = elem.text
                    if text and len(text) > 3:
                        business_data['hours'] = text.replace('\u202f', ' ').replace('\n', ' - ').strip()
                        return
                except:
                    continue
                    
        except Exception as e:
            logger.debug(f"Could not extract hours: {e}")

    # --- NEW: Extract Primary Image ---
    def extract_image(self, business_data):
        try:
            img_elem = self.driver.find_element(By.CSS_SELECTOR, 'button[aria-label*="Photo"] img')
            src = img_elem.get_attribute('src')
            if src:
                business_data['image'] = src
        except:
            pass
    
    def extract_website_comprehensive(self, business_data):
        website_selectors = [
            '[data-item-id="authority"]',
            'a[data-item-id="authority"]',
            'button[data-item-id="authority"]',
            '[data-value="Website"]',
            'a[data-value="Website"]',
            'button[aria-label*="ebsite"]'
        ]
        
        for selector in website_selectors:
            try:
                website_element = self.driver.find_element(By.CSS_SELECTOR, selector)
                
                href = website_element.get_attribute('href')
                if href:
                    clean_url = self.clean_website_url(href)
                    if clean_url:
                        business_data['website'] = clean_url
                        logger.info(f"Found website via href: {clean_url}")
                        self.extract_email_from_website(business_data) # <--- NEW EMAIL EXTRACTION
                        return
                
                try:
                    original_windows = self.driver.window_handles
                    website_element.click()
                    self.pause(3)
                    
                    new_windows = self.driver.window_handles
                    if len(new_windows) > len(original_windows):
                        self.driver.switch_to.window(new_windows[-1])
                        current_url = self.driver.current_url
                        
                        clean_url = self.clean_website_url(current_url)
                        if clean_url:
                            business_data['website'] = clean_url
                            logger.info(f"Found website via click: {clean_url}")
                            self.extract_email_from_website(business_data) # <--- NEW EMAIL EXTRACTION
                        
                        self.driver.close()
                        self.driver.switch_to.window(original_windows[0])
                        
                        if business_data['website']:
                            return
                            
                except Exception as click_error:
                    logger.warning(f"Error clicking website element: {click_error}")
                    continue
                    
            except Exception as e:
                continue
        
        try:
            # Skip the results list on the left: its links belong to other businesses
            hrefs = self.driver.execute_script("""
                return Array.from(document.querySelectorAll('a[href]'))
                    .filter(a => !a.closest('div[role="feed"]'))
                    .map(a => a.href);
            """)
            for href in hrefs:
                if not href.startswith(('http://', 'https://')):
                    continue
                if self.is_google_url(href) or self.is_excluded_site(href):
                    continue
                clean_url = self.clean_website_url(href)
                if clean_url:
                    business_data['website'] = clean_url
                    logger.info(f"Found website via link scan: {clean_url}")
                    self.extract_email_from_website(business_data) # <--- NEW EMAIL EXTRACTION
                    return
        except:
            pass

    # --- NEW: Advanced Email Extraction via Requests/BS4 ---
    def extract_email_from_website(self, business_data):
        url = business_data.get('website')
        if not url: return

        try:
            logger.info(f"Scanning {url} for emails...")
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(url, headers=headers, timeout=10)
            soup = BeautifulSoup(response.text, 'html.parser')

            # Method 1: Mailto links (most accurate)
            mailto_candidates = []
            for a_tag in soup.find_all('a', href=True):
                if a_tag['href'].lower().startswith('mailto:'):
                    mailto_candidates.extend(a_tag['href'][7:].split('?')[0].split(','))
            email = pick_best_email(mailto_candidates, url)
            if email:
                business_data['email'] = email
                logger.info(f"Found email via mailto: {email}")
                return

            # Method 2: Regex scanning the text
            email = pick_best_email(EMAIL_PATTERN.findall(response.text), url)
            if email:
                business_data['email'] = email
                logger.info(f"Found email via Regex: {email}")

        except requests.RequestException as e:
            logger.debug(f"Failed to connect to website for email extraction: {e}")
        except Exception as e:
            logger.debug(f"Error during email extraction: {e}")

    def is_excluded_site(self, url):
        """True for social, booking, delivery and review sites, which are never the business's own website"""
        labels = urllib.parse.urlparse(url).netloc.lower().split(':')[0].split('.')
        brand = labels[-2:-1]
        if len(labels) >= 3 and labels[-2] in PUBLIC_SUFFIX_LABELS:
            brand = labels[-3:-2]
        return any(label in EXCLUDED_SITE_BRANDS for label in brand)

    def clean_website_url(self, url):
        if not url:
            return None
        
        if self.is_google_url(url):
            real_url = self.extract_from_google_redirect(url)
            if real_url:
                return real_url
            return None
        
        url = url.strip()
        
        if not url.startswith(('http://', 'https://')):
            if url.startswith('www.'):
                url = 'https://' + url
            else:
                url = 'https://' + url
        
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.netloc and '.' in parsed.netloc:
                return url
        except:
            pass
        
        return None
    
    def is_google_url(self, url):
        google_domains = [
            'google.com', 'google.fr', 'google.co.uk', 'google.de',
            'googleapis.com', 'googleusercontent.com', 'gstatic.com'
        ]
        
        for domain in google_domains:
            if domain in url.lower():
                return True
        return False
    
    def extract_from_google_redirect(self, google_url):
        try:
            if 'url=' in google_url:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(google_url).query)
                if 'url' in parsed:
                    return parsed['url'][0]
            
            if 'adurl=' in google_url:
                parts = google_url.split('adurl=')
                if len(parts) > 1:
                    real_url = parts[1].split('&')[0]
                    return urllib.parse.unquote(real_url)
        except:
            pass
        
        return None
    
    def extract_rating_reviews(self, business_data):
        try:
            if not business_data['rating']:
                rating_selectors = [
                    '.F7nice span[aria-hidden="true"]',
                    '.jANrlb .fontDisplayLarge',
                    'span[aria-label*="stars"]'
                ]
                
                for selector in rating_selectors:
                    try:
                        elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        rating_text = elem.text.strip()
                        if rating_text:
                            rating_match = re.search(r'(\d+\.?\d*)', rating_text)
                            if rating_match:
                                business_data['rating'] = rating_match.group(1)
                                logger.debug(f"Found rating from detail panel: {business_data['rating']}")
                                break
                    except:
                        continue
            
            if not business_data['reviews']:
                review_selectors = [
                    'button[jsaction*="pane.rating.moreReviews"]',
                    '.F7nice',
                    'button[aria-label*="review"]',
                    'button[aria-label*="avis"]'
                ]
                
                for selector in review_selectors:
                    try:
                        elem = self.driver.find_element(By.CSS_SELECTOR, selector)
                        
                        aria_label = elem.get_attribute('aria-label')
                        if aria_label:
                            review_match = re.search(r'(\d+)\s+(?:review|avis)', aria_label, re.IGNORECASE)
                            if review_match:
                                business_data['reviews'] = review_match.group(1)
                                logger.debug(f"Found reviews from aria-label: {business_data['reviews']}")
                                break
                        
                        review_text = elem.text.strip()
                        if review_text:
                            review_match = re.search(r'\((\d+)\)', review_text)
                            if review_match:
                                business_data['reviews'] = review_match.group(1)
                                logger.debug(f"Found reviews from text: {business_data['reviews']}")
                                break
                            
                            review_match = re.search(r'(\d+)\s+review', review_text, re.IGNORECASE)
                            if review_match:
                                business_data['reviews'] = review_match.group(1)
                                logger.debug(f"Found reviews from text: {business_data['reviews']}")
                                break
                    except:
                        continue
                    
        except Exception as e:
            logger.warning(f"Error extracting rating/reviews: {e}")
    
    def scrape_cities(self, cities, query, delay=10, max_results=None):
        all_results = []

        for i, city in enumerate(cities, 1):
            if self.should_stop():
                break
            city = city.strip()
            if not city:
                continue

            logger.info(f"Processing city {i}/{len(cities)}: {city}")

            try:
                businesses = self.search_google_maps(query, city, max_results)
                for business in businesses:
                    business['city'] = city
                    all_results.append(business)

                logger.info(f"Found {len(businesses)} businesses in {city}")
                if i < len(cities):
                    self.pause(self.jittered_delay(delay))

            except Exception as e:
                logger.error(f"Error processing city {city}: {e}")
                continue

        self.results = all_results
        return all_results

    def format_results_for_export(self):
        """Helper to maintain your custom structure for exports"""
        formatted = []
        for idx, result in enumerate(self.results, 1):
            row = {
                'id': idx,
                'name': result.get('company', ''),
                'description': '',
                'rating': result.get('rating', ''),
                'reviewCount': result.get('reviews', ''),
                'phone': result.get('phone', ''),
                'email': result.get('email', ''),
                'website': result.get('website', ''),
                'address': result.get('address', ''),
                'hours': result.get('hours', ''),     # NEW
                'image': result.get('image', ''),     # DYNAMIC NOW
                'verified': 'true' if result.get('rating') else 'false',
                'tags': '',
                'city': result.get('city', '')
            }
            formatted.append(row)
        return formatted
    
    def save_to_csv(self, filename):
        """Save results to CSV with new format"""
        if not self.results:
            logger.warning("No results to save")
            return
        
        formatted_results = self.format_results_for_export()
        fieldnames = list(formatted_results[0].keys())
        
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for row in formatted_results:
                writer.writerow(row)
        
        logger.info(f"Results saved to {filename}")
        print(f"Saved {len(self.results)} results to {filename}")
    
    def save_to_json(self, filename):
        """Save results to JSON with new format"""
        if not self.results:
            logger.warning("No results to save")
            return
        
        formatted_results = self.format_results_for_export()
        
        with open(filename, 'w', encoding='utf-8') as jsonfile:
            json.dump(formatted_results, jsonfile, indent=2, ensure_ascii=False)
        
        logger.info(f"Results saved to {filename}")

    # --- NEW: Export to Excel ---
    def save_to_excel(self, filename):
        if not self.results:
            return
        formatted_results = self.format_results_for_export()
        df = pd.DataFrame(formatted_results)
        df.to_excel(filename, index=False)
        logger.info(f"Results saved to {filename}")
        print(f"Saved {len(self.results)} results to {filename}")

    # --- NEW: Export to Databases ---
    def save_to_sqlite(self, db_name):
        if not self.results:
            return
        formatted_results = self.format_results_for_export()
        df = pd.DataFrame(formatted_results)
        conn = sqlite3.connect(db_name)
        df.to_sql('businesses', conn, if_exists='append', index=False)
        conn.close()
        logger.info(f"Results saved to SQLite DB: {db_name}")

    def save_to_postgres(self, connection_string):
        if not self.results:
            return
        if not SQLALCHEMY_AVAILABLE:
            logger.error("SQLAlchemy not installed. Cannot export to PostgreSQL. Run: pip install SQLAlchemy psycopg2-binary")
            return
        try:
            formatted_results = self.format_results_for_export()
            df = pd.DataFrame(formatted_results)
            engine = create_engine(normalize_postgres_url(connection_string))
            df.to_sql('businesses', engine, if_exists='append', index=False)
            logger.info("Results saved successfully to PostgreSQL database")
        except Exception as e:
            logger.error(f"PostgreSQL Export Error: {e}")
    
    def export_all(self, output, formats, sqlite_db=None, postgres_conn=None):
        """Run every requested export. One failing export (a CSV open in Excel, a bad database
        URL) is logged and does not stop the others, so scraped results are not lost.
        Returns the names of the exports that failed."""
        formats = set(formats)
        steps = []
        if formats & {'csv', 'both'}:
            steps.append(("CSV", lambda: self.save_to_csv(f"{output}.csv")))
        if formats & {'json', 'both'}:
            steps.append(("JSON", lambda: self.save_to_json(f"{output}.json")))
        if 'excel' in formats:
            steps.append(("Excel", lambda: self.save_to_excel(f"{output}.xlsx")))
        if sqlite_db:
            steps.append(("SQLite", lambda: self.save_to_sqlite(sqlite_db)))
        if postgres_conn:
            steps.append(("PostgreSQL", lambda: self.save_to_postgres(postgres_conn)))

        failed = []
        for name, step in steps:
            try:
                step()
            except Exception as e:
                logger.error(f"{name} export failed: {e}")
                failed.append(name)
        return failed

    def close(self):
        """Close the browser"""
        if hasattr(self, 'driver'):
            self.driver.quit()

def load_cities_from_file(filename):
    cities = []
    
    try:
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
    
    except FileNotFoundError:
        logger.error(f"File {filename} not found")
        return []
    
    return cities

def parse_arguments():
    parser = argparse.ArgumentParser(
        description='Generic Google Maps Scraper - Extract business information from Google Maps',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  python %(prog)s --query "sustainability companies" --cities-file FASHION_CAPITALS.md
  python %(prog)s --query "italian restaurants" --cities "New York, USA" "Paris, France"
  python %(prog)s -q "coffee shops" -c "London, UK" "Tokyo, Japan" -o coffee_shops
  python %(prog)s -q "hotels" --cities-file cities.txt --headless --limit 5
  python %(prog)s -q "gyms" -c "Berlin, Germany" --test
        '''
    )
    
    parser.add_argument('-v', '--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('-q', '--query', required=True, help='Search query for businesses')
    
    city_group = parser.add_mutually_exclusive_group(required=True)
    city_group.add_argument('-c', '--cities', nargs='+', help='List of cities to search')
    city_group.add_argument('-f', '--cities-file', help='File containing cities')
    
    parser.add_argument('-o', '--output', default='google_maps_results', help='Output filename without extension')
    parser.add_argument('--format', nargs='+', choices=['csv', 'json', 'excel', 'both'], default=['both'], help='Output format')
    parser.add_argument('--headless', action='store_true', help='Run browser in headless mode')
    parser.add_argument('--limit', type=int, help='Limit number of cities to process')
    parser.add_argument('--test', action='store_true', help='Test mode: scrape only first city')
    parser.add_argument('--delay', type=int, default=10, help='Delay between cities in seconds (varied by 20%% either way)')
    parser.add_argument('--max-results', type=int, help='Maximum businesses to collect per city (default: all)')
    parser.add_argument('--verbose', action='store_true', help='Verbose output')
    
    # --- NEW ARGUMENTS ---
    parser.add_argument('--workers', type=int, default=1, help='Number of parallel workers (Multi-threading)')
    parser.add_argument('--proxy', type=str, help='Proxy server in format http://ip:port')
    parser.add_argument('--sqlite', type=str, help='Save data to SQLite DB filename (e.g. data.db)')
    parser.add_argument('--postgres', type=str, help='Save data to Postgres DB connection string (e.g. postgresql://user:pass@localhost/dbname)')
    parser.add_argument('--schedule', choices=['daily', 'hourly', 'weekly'], help='Run this scraper on a repeating schedule')
    
    return parser.parse_args()


# --- Worker for Multi-threading ---
def scrape_single_city(city, args, pre_delay=0):
    # pre_delay is set for every city a worker picks up after its first: a cool-down between one worker's cities
    if pre_delay:
        time.sleep(GoogleMapsScraper.jittered_delay(pre_delay))
    scraper = GoogleMapsScraper(headless=args.headless, proxy=args.proxy)
    results = []
    try:
        results = scraper.scrape_cities([city], args.query, delay=args.delay, max_results=args.max_results)
    except Exception as e:
        logger.error(f"Error in worker thread for {city}: {e}")
    finally:
        scraper.close()
    return results

# --- Main Scrape Logic ---
def execute_scraping_job(args, cities):
    print("\nInitializing scraper process...")
    
    all_results = []
    
    if args.workers > 1:
        print(f" Starting MULTI-THREADED mode with {args.workers} concurrent browsers...")
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_city = {
                executor.submit(scrape_single_city, city, args, args.delay if i >= args.workers else 0): city
                for i, city in enumerate(cities)
            }
            for future in as_completed(future_to_city):
                city = future_to_city[future]
                try:
                    res = future.result()
                    all_results.extend(res)
                except Exception as exc:
                    print(f"[ERROR] City {city} generated an exception: {exc}")
    else:
        scraper = GoogleMapsScraper(headless=args.headless, proxy=args.proxy)
        try:
            print("Starting single-threaded scraping process...\n")
            all_results = scraper.scrape_cities(cities, args.query, delay=args.delay, max_results=args.max_results)
        finally:
            print("\nClosing browser...")
            scraper.close()
    
    if all_results:
        exporter = GoogleMapsScraper.for_export(all_results)

        print("\nSaving results...")
        failed = exporter.export_all(args.output, args.format, args.sqlite, args.postgres)
        if failed:
            print(f"[WARNING] These exports failed, see the log above: {', '.join(failed)}")

        print(f"\n{'=' * 60}")
        print("Scraping Results")
        print(f"{'=' * 60}")
        print(f"Total companies found: {len(all_results)}")
        print(f"Cities processed: {len(set(r['city'] for r in all_results))}")

        with_ratings = len([r for r in all_results if r['rating']])
        with_reviews = len([r for r in all_results if r['reviews']])
        with_websites = len([r for r in all_results if r['website']])
        with_emails = len([r for r in all_results if r.get('email')])
        with_phones = len([r for r in all_results if r['phone']])
        with_addresses = len([r for r in all_results if r['address']])
        with_hours = len([r for r in all_results if r.get('hours')])

        print(f"Companies with ratings: {with_ratings} ({with_ratings/len(all_results)*100:.1f}%)")
        print(f"Companies with reviews: {with_reviews} ({with_reviews/len(all_results)*100:.1f}%)")
        print(f"Companies with websites: {with_websites} ({with_websites/len(all_results)*100:.1f}%)")
        print(f"Companies with emails: {with_emails} ({with_emails/len(all_results)*100:.1f}%)")
        print(f"Companies with phones: {with_phones} ({with_phones/len(all_results)*100:.1f}%)")
        print(f"Companies with addresses: {with_addresses} ({with_addresses/len(all_results)*100:.1f}%)")
        print(f"Companies with hours: {with_hours} ({with_hours/len(all_results)*100:.1f}%)")

        print(f"\nSample results:")
        for i, result in enumerate(all_results[:3], 1):
            print(f"\n{i}. {result['company']} ({result['city']})")
            if result['rating']:
                print(f"   Rating: {result['rating']} stars ({result['reviews']} reviews)")
            if result['address']:
                print(f"   Address: {result['address']}")
            if result['website']:
                print(f"   Website: {result['website']}")
            if result.get('email'):
                print(f"   Email: {result['email']}")
            if result['phone']:
                print(f"   Phone: {result['phone']}")

        print(f"\n[DONE] Scraping completed successfully!")
        
    else:
        print("[ERROR] No results found.")


def run_job_safely(args, cities):
    """Scheduled runs must survive a failed cycle, so log the error and wait for the next one"""
    try:
        execute_scraping_job(args, cities)
    except Exception as e:
        logger.error(f"Scheduled run failed, will retry on the next cycle: {e}")


def make_console_safe():
    """Never crash on a character the console encoding cannot show, such as a Greek business
    name on a cp1252 console: print a replacement character instead"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass  # no console (pythonw) or an exotic stream


def main():
    make_console_safe()
    args = parse_arguments()
    
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    print(f"Google Maps Scraper v{__version__}")
    print("=" * 60)
    
    if args.cities_file:
        cities = load_cities_from_file(args.cities_file)
        if not cities:
            print(f"Error: Could not load cities from {args.cities_file}")
            sys.exit(1)
    else:
        cities = args.cities
    
    if args.test:
        cities = cities[:1]
        print(f"TEST MODE: Processing only first city: {cities[0]}")
    elif args.limit:
        cities = cities[:args.limit]
        print(f"Limited to first {len(cities)} cities")
    
    print("Configuration")
    print("-" * 60)
    print(f"Search Query: {args.query}")
    print(f"Cities to process: {len(cities)}")
    print(f"Output file: {args.output}")
    print(f"Output formats: {', '.join(args.format)}")
    print(f"Headless mode: {args.headless}")
    print(f"Workers (Threads): {args.workers}")
    if args.max_results: print(f"Max results per city: {args.max_results}")
    if args.proxy: print(f"Proxy: {args.proxy}")
    if args.schedule: print(f"Schedule: {args.schedule}")
    if args.sqlite: print(f"SQLite DB: {args.sqlite}")
    if args.postgres: print(f"Postgres DB: Enabled")
    print(f"Delay between cities: {args.delay}s")
    print("=" * 60)
    
    if not args.test and not args.schedule:
        response = input("\nProceed with scraping? (y/n): ").lower().strip()
        if response != 'y':
            print("Scraping cancelled.")
            sys.exit(0)
    
    try:
        if args.schedule:
            print(f"\n[SCHEDULE] Scheduler initialized. Job will run: {args.schedule}")
            if args.schedule == 'daily':
                schedule.every().day.at("00:00").do(run_job_safely, args, cities)
            elif args.schedule == 'hourly':
                schedule.every().hour.do(run_job_safely, args, cities)
            elif args.schedule == 'weekly':
                schedule.every().week.do(run_job_safely, args, cities)
            
            # Run immediately once, then schedule
            run_job_safely(args, cities)
            
            print("\n[SCHEDULE] Waiting for next scheduled run... (Press Ctrl+C to exit)")
            while True:
                schedule.run_pending()
                time.sleep(60)
        else:
            execute_scraping_job(args, cities)
            
    except KeyboardInterrupt:
        print("\n[WARNING] Scraping interrupted by user")
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        print(f"[ERROR] Error: {e}")
    finally:
        print("Done!")

if __name__ == "__main__":
    main()