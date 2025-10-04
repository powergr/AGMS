# 🗺️ Google Maps Scraper

A powerful, generic Python-based web scraper for extracting business information from Google Maps. Perfect for market research, lead generation, and competitive analysis.

![Python Version](https://img.shields.io/badge/python-3.7%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.1.0-orange)
![Status](https://img.shields.io/badge/status-active-success)

## ✨ Features

- 🎯 **Generic Search**: Search for any type of business (restaurants, hotels, shops, etc.)
- 🌍 **Multi-City Support**: Scrape multiple cities from a file or command line
- 📊 **Comprehensive Data**: Extract company name, address, phone, website, rating, and reviews
- 🤖 **Auto Cookie Handling**: Automatically handles Google's cookie consent popups
- 💾 **Multiple Formats**: Export data to CSV, JSON, or both
- 🔍 **Smart Pagination**: Automatically loads all available results
- 🎭 **Headless Mode**: Run with or without visible browser
- ⚡ **CLI Interface**: Easy-to-use command-line interface with multiple options
- 🧪 **Test Mode**: Verify settings before running full scraping job

## 📋 Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Command-Line Options](#command-line-options)
- [Examples](#examples)
- [Output Format](#output-format)
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## Installation

### Prerequisites

- Python 3.7 or higher
- Google Chrome browser installed
- Internet connection

### Step 1: Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/google-maps-scraper.git
cd google-maps-scraper
```

### Step 2: Create Virtual Environment (Recommended)

**Windows:**

```bash
python -m venv venv
venv\Scripts\activate
```

**macOS/Linux:**

```bash
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install -r requirements.txt
```

The scraper will automatically download and manage the ChromeDriver for you!

## Quick Start

### Test with a Single City

```bash
python google_maps_scraper.py -q "coffee shops" -c "New York, USA" --test
```

### Search Multiple Cities from File

```bash
python google_maps_scraper.py -q "italian restaurants" -f cities.txt
```

### Search with Custom Output

```bash
python google_maps_scraper.py -q "hotels" -c "Paris, France" "London, UK" -o my_hotels.csv
```

## Usage

### Basic Syntax

```bash
python google_maps_scraper.py -q "SEARCH_QUERY" [-c CITIES | -f CITIES_FILE] [OPTIONS]
```

### Required Arguments

| Argument              | Description                      | Example                          |
| --------------------- | -------------------------------- | -------------------------------- |
| `-q`, `--query`       | Search query for businesses      | `"sustainability companies"`     |
| `-c`, `--cities`      | List of cities (space-separated) | `"New York, USA" "Tokyo, Japan"` |
| `-f`, `--cities-file` | File containing cities           | `cities.txt`                     |

**Note:** You must use either `-c` or `-f`, not both.

## Command-Line Options

### Output Options

| Option           | Description                             | Default               |
| ---------------- | --------------------------------------- | --------------------- |
| `-o`, `--output` | Output filename (without extension)     | `google_maps_results` |
| `--format`       | Output format: `csv`, `json`, or `both` | `both`                |

### Scraping Options

| Option       | Description                           | Default    |
| ------------ | ------------------------------------- | ---------- |
| `--headless` | Run browser in headless mode (no GUI) | `False`    |
| `--limit`    | Limit number of cities to process     | All cities |
| `--test`     | Test mode: scrape only first city     | `False`    |

### Advanced Options

| Option          | Description                    | Default     |
| --------------- | ------------------------------ | ----------- |
| `--delay`       | Delay between cities (seconds) | `10`        |
| `--max-results` | Maximum results per city       | All results |
| `--verbose`     | Show detailed logs             | `False`     |

### Help & Version

| Option            | Description                     |
| ----------------- | ------------------------------- |
| `-h`, `--help`    | Show help message with examples |
| `-v`, `--version` | Show version number             |

## Examples

### 1. Search Sustainability Companies

```bash
python google_maps_scraper.py -q "sustainability companies" -f FASHION_CAPITALS.md
```

### 2. Find Italian Restaurants in Specific Cities

```bash
python google_maps_scraper.py -q "italian restaurants" -c "Rome, Italy" "Naples, Italy" "Milan, Italy"
```

### 3. Scrape Coffee Shops with Custom Output

```bash
python google_maps_scraper.py -q "coffee shops" -c "Seattle, USA" -o coffee_data --format csv
```

### 4. Headless Mode for Server Deployment

```bash
python google_maps_scraper.py -q "gyms" -f cities.txt --headless
```

### 5. Test Before Full Run

```bash
python google_maps_scraper.py -q "bookstores" -f cities.txt --test
```

### 6. Limit Number of Cities

```bash
python google_maps_scraper.py -q "pet stores" -f cities.txt --limit 5
```

### 7. Verbose Mode for Debugging

```bash
python google_maps_scraper.py -q "bakeries" -c "Paris, France" --verbose
```

### 8. JSON Output Only

```bash
python google_maps_scraper.py -q "hotels" -c "Dubai, UAE" --format json
```

## Cities File Format

Create a text file with one city per line:

**cities.txt:**

```text
New York, USA
London, UK
Tokyo, Japan
Paris, France
```

**Or Markdown format (like FASHION_CAPITALS.md):**

```markdown
OK- Paris, France
OK- Milan, Italy

- New York City, USA
- London, UK
```

The scraper handles both formats automatically!

## Output Format

### CSV Output

| Column  | Description       | Example                           |
| ------- | ----------------- | --------------------------------- |
| company | Business name     | "Sustainable Fashion Co."         |
| address | Full address      | "123 Main St, New York, NY 10001" |
| phone   | Phone number      | "+1 212-555-0123"                 |
| website | Business website  | ["https://example.com"]           |
| email   | Email address     | (Usually empty from Maps)         |
| rating  | Average rating    | "4.5"                             |
| reviews | Number of reviews | "234"                             |
| city    | City searched     | "New York, USA"                   |

### JSON Output

```json
[
  {
    "company": "Sustainable Fashion Co.",
    "address": "123 Main St, New York, NY 10001, USA",
    "phone": "+1 212-555-0123",
    "website": "https://example.com",
    "email": "",
    "rating": "4.5",
    "reviews": "234",
    "city": "New York, USA"
  }
]
```

## Troubleshooting

### Common Issues

#### Issue: ChromeDriver not found

**Solution:** The script automatically downloads ChromeDriver using `webdriver-manager`. Make sure you have internet connection on first run.

#### Issue: No results found

**Solutions:**

- Check if your search query is correct
- Try running without `--headless` to see what's happening
- Use `--test` mode to verify with one city first
- Make sure cities are formatted correctly (e.g., "City, Country")

#### Issue: Cookie consent blocking

**Solution:** The script automatically handles cookie popups. If issues persist, try running in non-headless mode.

#### Issue: Timeout errors

**Solutions:**

- Increase delay between cities: `--delay 15`
- Check your internet connection
- Try running in non-headless mode to see if captcha appears

#### Issue: Script finds only 7 businesses

**Solution:** This is fixed in v1.1.0. The script now properly scrolls through all results. Make sure you're using the latest version.

#### Issue: Wrong review numbers

**Solution:** Fixed in v1.0.0. Review extraction now correctly captures full numbers.

### Debug Mode

Run with verbose flag to see detailed logs:

```bash
python google_maps_scraper.py -q "restaurants" -c "Paris, France" --verbose
```

## Performance Tips

1. **Use headless mode** for faster scraping (no GUI rendering)

   ```bash
   --headless
   ```

2. **Test first** before scraping many cities

   ```bash
   --test
   ```

3. **Limit cities** for testing

   ```bash
   --limit 5
   ```

4. **Adjust delay** based on your needs (default is 10 seconds)

   ```bash
   --delay 5  # Faster but riskier
   --delay 15 # Slower but safer
   ```

## Best Practices

### Ethical Scraping

- **Respect robots.txt** and terms of service
- **Don't overload servers** - use appropriate delays
- **Use responsibly** - this tool is for research and legitimate business purposes
- **Check local laws** regarding web scraping in your jurisdiction

### Rate Limiting

The scraper includes built-in delays:

- Random delays between actions (1-6 seconds)
- Configurable delay between cities (default: 10 seconds)
- Random scroll timing to appear more human-like

### Data Quality

- Always verify a sample of results manually
- Use `--test` mode before full scraping runs
- Check for missing data and adjust selectors if needed

## Updates and Versioning

### Version History

**v1.1.0** (Current)

- ✅ Generic business search
- ✅ Multi-city support
- ✅ Auto cookie consent handling
- ✅ Fixed pagination (loads all results)
- ✅ Fixed review number extraction
- ✅ CSV and JSON export
- ✅ CLI interface

### Checking Your Version

```bash
python google_maps_scraper.py --version
```

### Updating

```bash
git pull origin main
pip install -r requirements.txt --upgrade
```

## Contributing

Contributions are welcome! Here's how you can help:

1. **Report bugs** - Open an issue on GitHub
2. **Suggest features** - Share your ideas
3. **Submit PRs** - Fork, create a branch, and submit a pull request
4. **Improve docs** - Help make the documentation better

### Development Setup

```bash
# Clone the repository
git clone https://github.com/YOUR_USERNAME/google-maps-scraper.git
cd google-maps-scraper

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python google_maps_scraper.py -q "test query" -c "Test City" --test
```

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## ⚠️ Disclaimer

This tool is provided for educational and research purposes only. Users are responsible for ensuring their use of this tool complies with:

- Google Maps Terms of Service
- Local laws and regulations regarding web scraping
- Data protection and privacy laws (GDPR, CCPA, etc.)

The authors and contributors are not responsible for any misuse of this tool.

## 🙏 Acknowledgments

- Built with [Selenium](https://www.selenium.dev/) for browser automation
- Uses [webdriver-manager](https://github.com/SergeyPirogov/webdriver_manager) for automatic driver management
- Inspired by the need for accessible business data collection

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/YOUR_USERNAME/google-maps-scraper/issues)
- **Discussions**: [GitHub Discussions](https://github.com/YOUR_USERNAME/google-maps-scraper/discussions)
- **Email**: [your.email@example.com]

## 🌟 Star the Project

If you find this project useful, please consider giving it a star on GitHub! It helps others discover the tool.

## 📈 Roadmap

### Planned Features

- [ ] Support for more data fields (hours, photos, etc.)
- [ ] Proxy support for large-scale scraping
- [ ] Database export (SQLite, PostgreSQL)
- [ ] GUI interface
- [ ] Multi-threaded scraping
- [ ] Email extraction improvements
- [ ] Export to Excel format
- [ ] Scheduling and automation features
- [ ] Docker container
- [ ] API endpoint option

### Future Improvements

- Better error recovery
- Captcha handling
- Progress bars
- Resume interrupted scraping
- Cloud deployment guides

## 💡 Use Cases

- **Market Research**: Find competitors in specific locations
- **Lead Generation**: Build prospect lists for B2B sales
- **Data Analysis**: Analyze business density and ratings by location
- **Directory Building**: Create business directories
- **Academic Research**: Study business distribution patterns
- **SEO Analysis**: Competitor website analysis

## Additional Resources

### Related Projects

- [Google Maps API](https://developers.google.com/maps) - Official API
- [Selenium Documentation](https://selenium-python.readthedocs.io/)
- [Web Scraping Best Practices](https://www.scrapehero.com/web-scraping-best-practices/)

## 🎓 Learning Resources

If you're new to web scraping:

- [Selenium with Python Tutorial](https://selenium-python.readthedocs.io/getting-started.html)
- [Web Scraping Ethics Guide](https://www.promptcloud.com/blog/web-scraping-ethics/)
- [Python for Data Collection](https://realpython.com/python-web-scraping-practical-introduction/)

---

## 📋 Quick Reference

### Most Used Commands

```bash
# Basic search
python google_maps_scraper.py -q "QUERY" -c "CITY"

# From file
python google_maps_scraper.py -q "QUERY" -f cities.txt

# Test mode
python google_maps_scraper.py -q "QUERY" -f cities.txt --test

# Headless mode
python google_maps_scraper.py -q "QUERY" -f cities.txt --headless

# Custom output
python google_maps_scraper.py -q "QUERY" -c "CITY" -o output_name

# CSV only
python google_maps_scraper.py -q "QUERY" -c "CITY" --format csv

# Help
python google_maps_scraper.py --help

# Version
python google_maps_scraper.py --version
```

---

**Made with ❤️ by [Your Name]**

**Last Updated**: October 2025

**Repository**: [github.com/YOUR_USERNAME/google-maps-scraper](https://github.com/YOUR_USERNAME/google-maps-scraper)

![Python](https://img.shields.io/badge/topic-python-blue)
![Selenium](https://img.shields.io/badge/topic-selenium-blue)
![Web Scraping](https://img.shields.io/badge/topic-web--scraping-blue)
![Google Maps](https://img.shields.io/badge/topic-google--maps-blue)
![Data Extraction](https://img.shields.io/badge/topic-data--extraction-blue)
![Scraper](https://img.shields.io/badge/topic-scraper-blue)
![Automation](https://img.shields.io/badge/topic-automation-blue)
