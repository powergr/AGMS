"""
Email Extractor - Extract business emails from websites
Version: 1.0.0
Author: Email Extractor Team
License: MIT
"""

import json
import csv
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
import logging
import sys

__version__ = "1.1.0"
__author__ = "Email Extractor Team"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class EmailExtractor:
    def __init__(self, max_workers=5, timeout=10):
        """
        Initialize email extractor
        
        Args:
            max_workers: Number of concurrent threads for processing
            timeout: Request timeout in seconds
        """
        self.max_workers = max_workers
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        
        # Improved email pattern - must not have file extensions before @
        self.email_pattern = re.compile(
            r'\b[A-Za-z0-9][A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        )
        
        # Pages where emails are commonly found
        self.contact_pages = [
            '', 'contact', 'contact-us', 'about', 'about-us', 
            'team', 'support'
        ]
        
        # Domains to exclude (not business emails)
        self.exclude_domains = [
            'example.com', 'domain.com', 'email.com', 'sentry.io',
            'wix.com', 'wordpress.com', 'gmail.com', 'yahoo.com',
            'hotmail.com', 'outlook.com', 'googleusercontent.com',
            'wixpress.com', 'sivusto.com', 'mysite.com', 'sahkoposti.fi'
        ]
        
        # Patterns that indicate a false positive
        self.false_positive_patterns = [
            r'@2x\.',  # Retina image notation
            r'@3x\.',  # Retina image notation
            r'\.(png|jpg|jpeg|gif|svg|webp|ico)$',  # Image extensions
            r'_\d+x\d+@',  # Dimension notation with @
            r'[0-9a-f]{8}-[0-9a-f]{4}-',  # UUID patterns before @
        ]
    
    def is_valid_email(self, email):
        """Check if email is valid and not a false positive"""
        email_lower = email.lower()
        
        # Check for false positive patterns
        for pattern in self.false_positive_patterns:
            if re.search(pattern, email_lower):
                return False
        
        # Check excluded domains
        try:
            email_domain = email_lower.split('@')[1]
            if any(excluded in email_domain for excluded in self.exclude_domains):
                return False
        except IndexError:
            return False
        
        # Email should have at least 3 chars before @ and after @
        parts = email_lower.split('@')
        if len(parts) != 2 or len(parts[0]) < 3 or len(parts[1]) < 3:
            return False
        
        # Check if it looks like a real email (not placeholder)
        placeholder_keywords = ['example', 'test', 'esimerkki', 'placeholder', 'sample']
        if any(keyword in email_lower for keyword in placeholder_keywords):
            return False
        
        return True
    
    def extract_emails_from_text(self, text, website_domain):
        """Extract emails from text, filtering out non-business emails"""
        emails = self.email_pattern.findall(text)
        valid_emails = []
        
        for email in emails:
            email = email.lower().strip()
            
            if not self.is_valid_email(email):
                continue
            
            email_domain = email.split('@')[1]
            
            # Prefer emails matching the website domain
            if website_domain and website_domain in email_domain:
                valid_emails.insert(0, email)
            else:
                valid_emails.append(email)
        
        # Remove duplicates while preserving order
        seen = set()
        unique_emails = []
        for email in valid_emails:
            if email not in seen:
                seen.add(email)
                unique_emails.append(email)
        
        return unique_emails
    
    def get_domain_from_url(self, url):
        """Extract domain from URL"""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            # Remove www. prefix
            if domain.startswith('www.'):
                domain = domain[4:]
            return domain
        except:
            return None
    
    def fetch_page(self, url):
        """Fetch page content with error handling"""
        try:
            response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except requests.exceptions.Timeout:
            logger.debug(f"Timeout fetching {url}")
            return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                logger.debug(f"404 Not Found: {url}")
            else:
                logger.debug(f"HTTP error {e.response.status_code}: {url}")
            return None
        except requests.exceptions.RequestException as e:
            logger.debug(f"Error fetching {url}: {e}")
            return None
    
    def extract_emails_from_website(self, website_url):
        """
        Extract emails from a website by checking multiple pages
        
        Args:
            website_url: The website URL to extract emails from
            
        Returns:
            List of found emails
        """
        if not website_url:
            return []
        
        logger.info(f"Extracting emails from: {website_url}")
        
        domain = self.get_domain_from_url(website_url)
        all_emails = []
        
        # Try homepage first
        html_content = self.fetch_page(website_url)
        
        if not html_content:
            logger.info("Could not fetch homepage")
            return []
        
        # Extract emails from homepage
        emails = self.extract_emails_from_text(html_content, domain)
        all_emails.extend(emails)
        
        # Parse with BeautifulSoup for mailto links
        soup = BeautifulSoup(html_content, 'html.parser')
        for link in soup.find_all('a', href=True):
            if link['href'].startswith('mailto:'):
                email = link['href'].replace('mailto:', '').split('?')[0].lower().strip()
                if self.is_valid_email(email):
                    all_emails.append(email)
        
        # If we found emails on homepage, return them
        if all_emails:
            unique_emails = self.extract_emails_from_text(' '.join(all_emails), domain)
            if unique_emails:
                logger.info(f"Found {len(unique_emails)} email(s): {', '.join(unique_emails)}")
                return unique_emails
        
        # Otherwise, try contact pages
        base_url = website_url.rstrip('/')
        for page in ['contact', 'about', 'contact-us']:
            page_url = f"{base_url}/{page}"
            
            html_content = self.fetch_page(page_url)
            if not html_content:
                continue
            
            emails = self.extract_emails_from_text(html_content, domain)
            all_emails.extend(emails)
            
            # Parse mailto links
            soup = BeautifulSoup(html_content, 'html.parser')
            for link in soup.find_all('a', href=True):
                if link['href'].startswith('mailto:'):
                    email = link['href'].replace('mailto:', '').split('?')[0].lower().strip()
                    if self.is_valid_email(email):
                        all_emails.append(email)
            
            # If we found emails, stop searching
            if all_emails:
                break
            
            # Small delay between requests
            time.sleep(random.uniform(0.3, 0.8))
        
        # Remove duplicates and filter
        unique_emails = self.extract_emails_from_text(' '.join(all_emails), domain)
        
        if unique_emails:
            logger.info(f"Found {len(unique_emails)} email(s): {', '.join(unique_emails)}")
        else:
            logger.info("No emails found")
        
        return unique_emails
    
    def process_single_business(self, business):
        """Process a single business entry"""
        business_id = business.get('id', 'Unknown')
        business_name = business.get('name', 'Unknown')
        website = business.get('website', '')
        
        logger.info(f"Processing #{business_id}: {business_name}")
        
        if not website:
            logger.info(f"No website for {business_name}, skipping")
            return business
        
        # Extract emails
        emails = self.extract_emails_from_website(website)
        
        # Add first email to business data (or empty string)
        business['email'] = emails[0] if emails else ''
        
        # Optionally store all found emails
        if len(emails) > 1:
            business['all_emails'] = ', '.join(emails)
        
        return business
    
    def process_businesses(self, businesses, use_threading=True):
        """
        Process multiple businesses to extract emails
        
        Args:
            businesses: List of business dictionaries
            use_threading: Whether to use multi-threading
            
        Returns:
            Updated list of businesses with emails
        """
        updated_businesses = []
        
        if use_threading:
            logger.info(f"Processing {len(businesses)} businesses with {self.max_workers} workers")
            
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                future_to_business = {
                    executor.submit(self.process_single_business, business): business 
                    for business in businesses
                }
                
                for future in as_completed(future_to_business):
                    try:
                        result = future.result()
                        updated_businesses.append(result)
                    except Exception as e:
                        business = future_to_business[future]
                        logger.error(f"Error processing {business.get('name')}: {e}")
                        updated_businesses.append(business)
        else:
            # Sequential processing
            logger.info(f"Processing {len(businesses)} businesses sequentially")
            for business in businesses:
                updated_business = self.process_single_business(business)
                updated_businesses.append(updated_business)
        
        return updated_businesses
    
    def process_json_file(self, input_file, output_file=None):
        """
        Process businesses from JSON file and add emails
        
        Args:
            input_file: Input JSON file path
            output_file: Output JSON file path (if None, overwrites input)
        """
        if output_file is None:
            output_file = input_file.replace('.json', '_with_emails.json')
        
        logger.info(f"Loading businesses from {input_file}")
        
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                businesses = json.load(f)
        except FileNotFoundError:
            logger.error(f"File not found: {input_file}")
            return
        except json.JSONDecodeError:
            logger.error(f"Invalid JSON file: {input_file}")
            return
        
        logger.info(f"Found {len(businesses)} businesses")
        
        # Process businesses
        updated_businesses = self.process_businesses(businesses)
        
        # Save results
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(updated_businesses, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Saved updated businesses to {output_file}")
        
        # Print statistics
        businesses_with_emails = len([b for b in updated_businesses if b.get('email')])
        logger.info(f"Statistics: {businesses_with_emails}/{len(businesses)} businesses now have emails")
        
        return updated_businesses
    
    def process_csv_file(self, input_file, output_file=None):
        """
        Process businesses from CSV file and add emails
        
        Args:
            input_file: Input CSV file path
            output_file: Output CSV file path (if None, creates new file)
        """
        if output_file is None:
            output_file = input_file.replace('.csv', '_with_emails.csv')
        
        logger.info(f"Loading businesses from {input_file}")
        
        try:
            with open(input_file, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                businesses = list(reader)
        except FileNotFoundError:
            logger.error(f"File not found: {input_file}")
            return
        
        logger.info(f"Found {len(businesses)} businesses")
        
        # Process businesses
        updated_businesses = self.process_businesses(businesses)
        
        # Save results
        if updated_businesses:
            fieldnames = list(updated_businesses[0].keys())
            
            with open(output_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(updated_businesses)
            
            logger.info(f"Saved updated businesses to {output_file}")
            
            # Print statistics
            businesses_with_emails = len([b for b in updated_businesses if b.get('email')])
            logger.info(f"Statistics: {businesses_with_emails}/{len(businesses)} businesses now have emails")
        
        return updated_businesses

def print_version():
    """Print version information"""
    print(f"Email Extractor v{__version__}")
    print(f"Author: {__author__}")
    print("License: MIT")
    print("\nA tool to extract business email addresses from websites")

def print_help():
    """Print detailed help information"""
    help_text = f"""
╔══════════════════════════════════════════════════════════════╗
║              Email Extractor v{__version__}                          ║
╚══════════════════════════════════════════════════════════════╝

DESCRIPTION:
    Extract business email addresses from company websites by analyzing
    homepage and common contact pages. Filters out generic emails and
    false positives.

USAGE:
    python email_extractor.py INPUT_FILE [OPTIONS]

ARGUMENTS:
    INPUT_FILE              Input file containing business data
                           Supported formats: .json, .csv

OPTIONS:
    -h, --help             Show this help message and exit
    -v, --version          Show version information and exit
    -o, --output FILE      Output file path (default: INPUT_with_emails.EXT)
    -w, --workers N        Number of concurrent workers (default: 5)
    -t, --timeout N        Request timeout in seconds (default: 10)
    --sequential           Process sequentially instead of using threads
    --verbose              Enable verbose logging (DEBUG level)
    --quiet                Suppress INFO logs, show only warnings/errors

INPUT FILE FORMAT:
    JSON: Array of objects with 'website' field
          [
            {{"id": 1, "name": "Company", "website": "https://example.com"}},
            ...
          ]
    
    CSV:  Must have a 'website' column header
          id,name,website
          1,Company,https://example.com

OUTPUT:
    Adds 'email' field with primary email address found
    Adds 'all_emails' field if multiple emails are found

EXAMPLES:
    # Basic usage
    python email_extractor.py businesses.json
    
    # Specify output file
    python email_extractor.py businesses.json -o results.json
    
    # Use 10 concurrent workers with 15 second timeout
    python email_extractor.py businesses.csv -w 10 -t 15
    
    # Process sequentially (single-threaded)
    python email_extractor.py businesses.json --sequential
    
    # Verbose mode for debugging
    python email_extractor.py businesses.json --verbose

FEATURES:
    ✓ Concurrent processing for faster extraction
    ✓ Filters out generic emails (gmail, yahoo, etc.)
    ✓ Removes false positives (image filenames, placeholders)
    ✓ Prioritizes emails matching company domain
    ✓ Checks homepage and common contact pages
    ✓ Handles redirects and various URL formats
    ✓ Supports both JSON and CSV formats

NOTES:
    - Respects website response times with random delays
    - Uses proper User-Agent headers
    - Follows redirects automatically
    - Handles 404 and timeout errors gracefully
    
REQUIREMENTS:
    - requests
    - beautifulsoup4

For issues and updates, visit: https://github.com/powergr/agms
"""
    print(help_text)

def main():
    import argparse
    
    # Custom formatter for better help display
    class CustomFormatter(argparse.RawDescriptionHelpFormatter):
        def __init__(self, prog):
            super().__init__(prog, max_help_position=40, width=100)
    
    parser = argparse.ArgumentParser(
        description='Extract business email addresses from websites',
        formatter_class=CustomFormatter,
        epilog="""
Examples:
  %(prog)s businesses.json
  %(prog)s businesses.json -o results.json
  %(prog)s businesses.csv -w 10 -t 15
  %(prog)s businesses.json --sequential --verbose

For detailed help, use: %(prog)s --help-full
        """,
        add_help=False  # We'll add custom help
    )
    
    # Required arguments
    parser.add_argument('input_file', nargs='?', help='Input file (JSON or CSV format)')
    
    # Optional arguments
    parser.add_argument('-h', '--help', action='store_true', help='Show brief help message')
    parser.add_argument('--help-full', action='store_true', help='Show detailed help with examples')
    parser.add_argument('-v', '--version', action='store_true', help='Show version information')
    parser.add_argument('-o', '--output', metavar='FILE', help='Output file path')
    parser.add_argument('-w', '--workers', type=int, default=5, metavar='N',
                       help='Number of concurrent workers (default: 5)')
    parser.add_argument('-t', '--timeout', type=int, default=10, metavar='N',
                       help='Request timeout in seconds (default: 10)')
    parser.add_argument('--sequential', action='store_true',
                       help='Process sequentially instead of using threads')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging (DEBUG level)')
    parser.add_argument('--quiet', action='store_true',
                       help='Suppress INFO logs, show only warnings/errors')
    
    args = parser.parse_args()
    
    # Handle version
    if args.version:
        print_version()
        sys.exit(0)
    
    # Handle help
    if args.help_full:
        print_help()
        sys.exit(0)
    
    if args.help or not args.input_file:
        parser.print_help()
        if not args.input_file:
            print("\nError: INPUT_FILE is required")
            sys.exit(1)
        sys.exit(0)
    
    # Set logging level
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    elif args.quiet:
        logging.getLogger().setLevel(logging.WARNING)
    
    # Validate workers
    if args.workers < 1:
        logger.error("Number of workers must be at least 1")
        sys.exit(1)
    
    if args.workers > 20:
        logger.warning("High number of workers may cause rate limiting. Consider using 5-10.")
    
    # Validate timeout
    if args.timeout < 1:
        logger.error("Timeout must be at least 1 second")
        sys.exit(1)
    
    # Print startup info
    logger.info(f"Email Extractor v{__version__}")
    logger.info(f"Configuration: {args.workers} workers, {args.timeout}s timeout")
    
    # Create extractor
    extractor = EmailExtractor(max_workers=args.workers, timeout=args.timeout)
    
    # Determine file type and process
    if args.input_file.endswith('.json'):
        extractor.process_json_file(args.input_file, args.output)
    elif args.input_file.endswith('.csv'):
        extractor.process_csv_file(args.input_file, args.output)
    else:
        logger.error("Input file must be .json or .csv")
        sys.exit(1)
    
    print("\n✅ Email extraction completed!")

if __name__ == "__main__":
    main()