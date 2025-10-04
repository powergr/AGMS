import time
import csv
import json
import re
import argparse
import sys
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

# Version
__version__ = "1.1.0"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class GoogleMapsScraper:
    def __init__(self, headless=True):
        self.setup_driver(headless)
        self.results = []
        
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
        
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        chrome_options.add_argument(f"--user-agent={user_agent}")
        
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
            time.sleep(2)
            
            # Try different button selectors for "Reject all" or "Accept all"
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
                                time.sleep(2)
                                return
                except:
                    continue
            
            # Try XPath if CSS selectors fail
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
                            time.sleep(2)
                            return
                    except:
                        continue
            except:
                pass
            
            logger.debug("No cookie consent popup found or already handled")
            
        except Exception as e:
            logger.debug(f"Error handling cookie consent: {e}")
    
    def search_google_maps(self, query, city):
        search_term = f"{query} {city}"
        logger.info(f"Searching for: {search_term}")
        
        try:
            self.driver.get("https://www.google.com/maps")
            time.sleep(random.uniform(3, 5))
            
            # Handle cookie consent popup
            self.handle_cookie_consent()
            
            search_box = self.wait.until(EC.presence_of_element_located((By.ID, "searchboxinput")))
            search_box.clear()
            
            for char in search_term:
                search_box.send_keys(char)
                time.sleep(random.uniform(0.05, 0.15))
            
            search_box.send_keys(Keys.RETURN)
            time.sleep(random.uniform(6, 10))
            
            try:
                self.wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '[role="main"]')))
                logger.info("Search results loaded")
            except TimeoutException:
                logger.warning("Results panel not found")
                return []
            
            self.load_all_results()
            businesses = self.extract_all_businesses()
            return businesses
            
        except Exception as e:
            logger.error(f"Error searching for {search_term}: {e}")
            return []
    
    def load_all_results(self):
        try:
            # Find the scrollable results feed
            time.sleep(3)
            
            # Try multiple selectors for the scrollable container
            scrollable_container = None
            container_selectors = [
                'div[role="feed"]',
                'div.m6QErb[aria-label]',
                '[role="main"] div[tabindex="-1"]',
                'div[aria-label][role="feed"]'
            ]
            
            for selector in container_selectors:
                try:
                    scrollable_container = self.driver.find_element(By.CSS_SELECTOR, selector)
                    if scrollable_container:
                        logger.info(f"Found scrollable container: {selector}")
                        break
                except:
                    continue
            
            if not scrollable_container:
                logger.warning("Could not find scrollable container, using main")
                scrollable_container = self.driver.find_element(By.CSS_SELECTOR, '[role="main"]')
            
            scroll_attempts = 0
            max_scrolls = 25
            no_change_count = 0
            previous_count = 0
            
            while scroll_attempts < max_scrolls:
                # Get current count before scrolling
                current_elements = self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc')
                current_count = len(current_elements)
                
                logger.info(f"Scroll {scroll_attempts + 1}: Currently {current_count} businesses visible")
                
                # Scroll the specific container to the bottom
                self.driver.execute_script(
                    "arguments[0].scrollTop = arguments[0].scrollHeight", 
                    scrollable_container
                )
                
                # Wait for new content to load
                time.sleep(random.uniform(4, 6))
                
                # Check if we got new results after scrolling
                new_elements = self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc')
                new_count = len(new_elements)
                
                if new_count > current_count:
                    logger.info(f"✓ Loaded {new_count - current_count} more businesses (total: {new_count})")
                    no_change_count = 0
                    previous_count = new_count
                else:
                    no_change_count += 1
                    logger.info(f"No new businesses loaded (attempt {no_change_count}/5)")
                    
                    if no_change_count >= 5:
                        logger.info(f"No new results after 5 attempts, stopping scroll")
                        break
                
                scroll_attempts += 1
                
                # Look for "You've reached the end" message
                try:
                    end_messages = self.driver.find_elements(By.XPATH, 
                        "//*[contains(text(), 'reached the end') or contains(text(), 'no more results')]")
                    if end_messages:
                        logger.info("Reached end of results")
                        break
                except:
                    pass
            
            final_count = len(self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc'))
            logger.info(f"✓ Finished scrolling: Total {final_count} businesses found")
            
        except Exception as e:
            logger.error(f"Error during scrolling: {e}")
            import traceback
            traceback.print_exc()
    
    def extract_all_businesses(self):
        businesses = []
        
        try:
            business_elements = self.driver.find_elements(By.CSS_SELECTOR, '.hfpxzc')
            logger.info(f"Found {len(business_elements)} businesses")
            
            for i, element in enumerate(business_elements):
                try:
                    logger.info(f"Processing business {i+1}/{len(business_elements)}")
                    business_data = self.extract_business_info(element)
                    
                    if business_data and business_data.get('company'):
                        businesses.append(business_data)
                        logger.info(f"✓ Extracted: {business_data['company']}")
                        if business_data.get('website'):
                            logger.info(f"  Website: {business_data['website']}")
                    
                    time.sleep(random.uniform(1, 2))
                    
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
            'reviews': ''
        }
        
        try:
            aria_label = element.get_attribute('aria-label')
            if aria_label:
                business_data['company'] = aria_label.strip()
                
                rating_match = re.search(r'(\d+\.?\d*)\s*star', aria_label, re.IGNORECASE)
                if rating_match:
                    business_data['rating'] = rating_match.group(1)
                
                # Extract reviews - FIXED to capture correct numbers
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
                    self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
                    time.sleep(1)
                    element.click()
                    time.sleep(random.uniform(4, 6))
                    self.extract_detailed_info(business_data)
                except Exception as e:
                    logger.warning(f"Could not get detailed info for {business_data['company']}: {e}")
            
            return business_data
            
        except Exception as e:
            logger.warning(f"Error extracting business info: {e}")
            return None
    
    def extract_detailed_info(self, business_data):
        try:
            time.sleep(3)
            self.extract_address(business_data)
            self.extract_phone(business_data)
            self.extract_website_comprehensive(business_data)
            self.extract_rating_reviews(business_data)
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
                        return
                
                try:
                    original_windows = self.driver.window_handles
                    website_element.click()
                    time.sleep(3)
                    
                    new_windows = self.driver.window_handles
                    if len(new_windows) > len(original_windows):
                        self.driver.switch_to.window(new_windows[-1])
                        current_url = self.driver.current_url
                        
                        clean_url = self.clean_website_url(current_url)
                        if clean_url:
                            business_data['website'] = clean_url
                            logger.info(f"Found website via click: {clean_url}")
                        
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
            all_links = self.driver.find_elements(By.TAG_NAME, 'a')
            for link in all_links:
                href = link.get_attribute('href')
                if href and not self.is_google_url(href):
                    if any(domain in href.lower() for domain in ['.com', '.fr', '.co.uk', '.org', '.net', '.de', '.it', '.es']):
                        clean_url = self.clean_website_url(href)
                        if clean_url:
                            business_data['website'] = clean_url
                            logger.info(f"Found website via link scan: {clean_url}")
                            return
        except:
            pass
    
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
            # Extract rating if not already found
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
            
            # Extract review count if not already found - FIXED
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
                        
                        # Try aria-label first (most reliable)
                        aria_label = elem.get_attribute('aria-label')
                        if aria_label:
                            review_match = re.search(r'(\d+)\s+(?:review|avis)', aria_label, re.IGNORECASE)
                            if review_match:
                                business_data['reviews'] = review_match.group(1)
                                logger.debug(f"Found reviews from aria-label: {business_data['reviews']}")
                                break
                        
                        # Try text content
                        review_text = elem.text.strip()
                        if review_text:
                            # Look for numbers in parentheses like "(123)"
                            review_match = re.search(r'\((\d+)\)', review_text)
                            if review_match:
                                business_data['reviews'] = review_match.group(1)
                                logger.debug(f"Found reviews from text: {business_data['reviews']}")
                                break
                            
                            # Look for pattern "123 reviews"
                            review_match = re.search(r'(\d+)\s+review', review_text, re.IGNORECASE)
                            if review_match:
                                business_data['reviews'] = review_match.group(1)
                                logger.debug(f"Found reviews from text: {business_data['reviews']}")
                                break
                    except:
                        continue
                    
        except Exception as e:
            logger.warning(f"Error extracting rating/reviews: {e}")
    
    def scrape_cities(self, cities, query):
        all_results = []
        
        for i, city in enumerate(cities, 1):
            city = city.strip()
            if not city:
                continue
                
            logger.info(f"Processing city {i}/{len(cities)}: {city}")
            
            try:
                businesses = self.search_google_maps(query, city)
                for business in businesses:
                    business['city'] = city
                    all_results.append(business)
                
                logger.info(f"Found {len(businesses)} businesses in {city}")
                time.sleep(random.uniform(8, 12))
                
            except Exception as e:
                logger.error(f"Error processing city {city}: {e}")
                continue
        
        self.results = all_results
        return all_results
    
    def save_to_csv(self, filename):
        if not self.results:
            logger.warning("No results to save")
            return
        
        fieldnames = ['company', 'address', 'phone', 'website', 'email', 'rating', 'reviews', 'city']
        
        with open(filename, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            writer.writeheader()
            for result in self.results:
                writer.writerow(result)
        
        logger.info(f"Results saved to {filename}")
        print(f"Saved {len(self.results)} results to {filename}")
    
    def save_to_json(self, filename):
        if not self.results:
            logger.warning("No results to save")
            return
        
        with open(filename, 'w', encoding='utf-8') as jsonfile:
            json.dump(self.results, jsonfile, indent=2, ensure_ascii=False)
        
        logger.info(f"Results saved to {filename}")
    
    def close(self):
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
  # Search for sustainability companies in cities from file
  python %(prog)s --query "sustainability companies" --cities-file FASHION_CAPITALS.md
  
  # Search for restaurants in specific cities
  python %(prog)s --query "italian restaurants" --cities "New York, USA" "Paris, France"
  
  # Search for coffee shops with custom output
  python %(prog)s -q "coffee shops" -c "London, UK" "Tokyo, Japan" -o coffee_shops.csv
  
  # Run in headless mode with limit
  python %(prog)s -q "hotels" --cities-file cities.txt --headless --limit 5
  
  # Test mode to verify before scraping all cities
  python %(prog)s -q "gyms" -c "Berlin, Germany" --test
        '''
    )
    
    # Version argument
    parser.add_argument(
        '-v', '--version',
        action='version',
        version=f'%(prog)s {__version__}'
    )
    
    # Required arguments
    parser.add_argument(
        '-q', '--query',
        required=True,
        help='Search query for businesses (e.g., "sustainability companies", "italian restaurants", "coffee shops")'
    )
    
    # City input (either file or list)
    city_group = parser.add_mutually_exclusive_group(required=True)
    city_group.add_argument(
        '-c', '--cities',
        nargs='+',
        help='List of cities to search (e.g., "New York, USA" "Paris, France")'
    )
    city_group.add_argument(
        '-f', '--cities-file',
        help='File containing cities (one per line or markdown format)'
    )
    
    # Output options
    parser.add_argument(
        '-o', '--output',
        default='google_maps_results',
        help='Output filename without extension (default: google_maps_results)'
    )
    parser.add_argument(
        '--format',
        choices=['csv', 'json', 'both'],
        default='both',
        help='Output format (default: both)'
    )
    
    # Scraping options
    parser.add_argument(
        '--headless',
        action='store_true',
        help='Run browser in headless mode (no GUI)'
    )
    parser.add_argument(
        '--limit',
        type=int,
        help='Limit number of cities to process'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Test mode: scrape only first city to verify settings'
    )
    
    # Advanced options
    parser.add_argument(
        '--delay',
        type=int,
        default=10,
        help='Delay between cities in seconds (default: 10)'
    )
    parser.add_argument(
        '--max-results',
        type=int,
        help='Maximum results per city (default: all)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Verbose output (show detailed logs)'
    )
    
    return parser.parse_args()

def main():
    args = parse_arguments()
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Display version and header
    print(f"Google Maps Scraper v{__version__}")
    print("=" * 60)
    
    # Load cities
    if args.cities_file:
        cities = load_cities_from_file(args.cities_file)
        if not cities:
            print(f"Error: Could not load cities from {args.cities_file}")
            sys.exit(1)
    else:
        cities = args.cities
    
    # Apply limit or test mode
    if args.test:
        cities = cities[:1]
        print(f"TEST MODE: Processing only first city: {cities[0]}")
    elif args.limit:
        cities = cities[:args.limit]
        print(f"Limited to first {len(cities)} cities")
    
    # Display configuration
    print("Configuration")
    print("-" * 60)
    print(f"Search Query: {args.query}")
    print(f"Cities to process: {len(cities)}")
    print(f"Output file: {args.output}")
    print(f"Output format: {args.format}")
    print(f"Headless mode: {args.headless}")
    print(f"Delay between cities: {args.delay}s")
    print("=" * 60)
    
    # Confirm before starting
    if not args.test:
        response = input("\nProceed with scraping? (y/n): ").lower().strip()
        if response != 'y':
            print("Scraping cancelled.")
            sys.exit(0)
    
    # Initialize scraper
    print("\nInitializing scraper...")
    scraper = GoogleMapsScraper(headless=args.headless)
    
    try:
        print("Starting scraping process...\n")
        results = scraper.scrape_cities(cities, args.query)
        
        if results:
            # Save results
            print("\nSaving results...")
            if args.format in ['csv', 'both']:
                scraper.save_to_csv(f"{args.output}.csv")
            if args.format in ['json', 'both']:
                scraper.save_to_json(f"{args.output}.json")
            
            # Display statistics
            print(f"\n{'=' * 60}")
            print("Scraping Results")
            print(f"{'=' * 60}")
            print(f"📊 Total companies found: {len(results)}")
            print(f"🏙️  Cities processed: {len(set(r['city'] for r in results))}")
            
            with_ratings = len([r for r in results if r['rating']])
            with_reviews = len([r for r in results if r['reviews']])
            with_websites = len([r for r in results if r['website']])
            with_phones = len([r for r in results if r['phone']])
            with_addresses = len([r for r in results if r['address']])
            
            print(f"⭐ Companies with ratings: {with_ratings} ({with_ratings/len(results)*100:.1f}%)")
            print(f"💬 Companies with reviews: {with_reviews} ({with_reviews/len(results)*100:.1f}%)")
            print(f"🌐 Companies with websites: {with_websites} ({with_websites/len(results)*100:.1f}%)")
            print(f"📞 Companies with phones: {with_phones} ({with_phones/len(results)*100:.1f}%)")
            print(f"📍 Companies with addresses: {with_addresses} ({with_addresses/len(results)*100:.1f}%)")
            
            # Show sample results
            print(f"\n🔍 Sample results:")
            for i, result in enumerate(results[:3], 1):
                print(f"\n{i}. {result['company']} ({result['city']})")
                if result['rating']:
                    print(f"   ⭐ {result['rating']} stars ({result['reviews']} reviews)")
                if result['address']:
                    print(f"   📍 {result['address']}")
                if result['website']:
                    print(f"   🌐 {result['website']}")
                if result['phone']:
                    print(f"   📞 {result['phone']}")
            
            print(f"\n✅ Scraping completed successfully!")
            
        else:
            print("❌ No results found.")
    
    except KeyboardInterrupt:
        print("\n⚠️  Scraping interrupted by user")
    except Exception as e:
        logger.error(f"Error occurred: {e}")
        print(f"❌ Error: {e}")
    finally:
        print("\nClosing browser...")
        scraper.close()
        print("Done!")

if __name__ == "__main__":
    main()