# 🗺️ Another Google Maps Scraper (AGMS)

A powerful, generic Python-based web scraper for extracting business information from Google Maps. Perfect for market research, lead generation, and competitive analysis.

**13/11/2026 UPDATE:**
The script has updated to work with all the latest changes from Google. Please open an issue if you find a problem. Small bug fixes.

**Added an email extractor, because Google Maps does not have emails in companies.**

![Python Version](https://img.shields.io/badge/python-3.14%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.3.2-orange)
![Status](https://img.shields.io/badge/status-active-success)

## ✨ Features

- 🎯 **Generic Search**: Search for any business (restaurants, hotels, shops, etc.)
- 🌍 **Multi-City Support**: Scrape multiple cities from a file or command line
- 📊 **Comprehensive Data**: Company name, address, phone, website, email, rating, reviews, hours, and photo
- ✉️ **Email Extraction**: Automatically scans each business's website for a contact email
- 🤖 **Auto Cookie Handling**: Automatically handles Google's cookie consent popups
- 💾 **Multiple Formats**: Export to CSV, JSON, Excel, SQLite, or PostgreSQL, in any combination
- 🔍 **Smart Pagination**: Automatically loads all available results
- 🎭 **Headless Mode**: Run with or without visible browser
- ⚡ **CLI Interface**: Easy-to-use command-line interface with multiple options
- 🖱️ **GUI Interface**: Full-featured desktop app (`google_maps_scraper_gui.py`) with the same capabilities as the CLI. See [docs/GUI_setup_guide.md](docs/GUI_setup_guide.md) and [docs/CLI-vs-GUI.md](docs/CLI-vs-GUI.md)
- 🧵 **Multi-Threaded**: Run several browsers in parallel to scrape faster
- 🌐 **Proxy Support**: Route requests through a proxy server
- ⏰ **Scheduling**: Re-run a job automatically on a daily, hourly, or weekly interval
- 🧪 **Test Mode**: Verify settings before running full scraping job

## 📋 Table of Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Usage](#usage)
- [Command-Line Options](#command-line-options)
- [Examples](#examples)
- [Output Format](#output-format)
- [Troubleshooting](#troubleshooting)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

## Installation

### Windows installer (easiest, no Python needed)

1. Download `AGMS-Setup-<version>.exe` from the [latest release](https://github.com/powergr/AGMS/releases/latest).
2. Run it. It installs for your own account, so it needs no administrator rights.
3. Start **AGMS** from the Start menu. You still need Google Chrome installed.

Results are saved in `Documents\AGMS`. Windows may show a "Windows protected your PC" warning, because the setup file is not code signed. Choose **More info**, then **Run anyway**. You can compare the download with the `.sha256` file on the release page.

The installed app is the graphical version. To use the command line, or to run from source, follow the steps below.

### Prerequisites

- Python 3.7 or higher
- Google Chrome browser installed
- Internet connection

### Step 1: Clone the Repository

```bash
git clone https://github.com/powergr/AGMS.git
cd AGMS
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
python google_maps_scraper.py -q "hotels" -c "Paris, France" "London, UK" -o my_hotels
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

| Option           | Description                                                                                | Default               |
| ---------------- | ------------------------------------------------------------------------------------------ | --------------------- |
| `-o`, `--output` | Output filename (without extension)                                                        | `google_maps_results` |
| `--format`       | One or more of `csv`, `json`, `excel`, `both` (space-separated, e.g. `--format csv excel`) | `both`                |

### Scraping Options

| Option       | Description                                                         | Default    |
| ------------ | ------------------------------------------------------------------- | ---------- |
| `--headless` | Run browser in headless mode (no GUI)                               | `False`    |
| `--limit`    | Limit number of cities to process                                   | All cities |
| `--test`     | Test mode: scrape only first city                                   | `False`    |
| `--workers`  | Parallel workers (concurrent browsers, multi-threaded scraping)     | `1`        |
| `--proxy`    | Proxy server, e.g. `http://ip:port`                                 | None       |
| `--schedule` | Repeat the whole run on an interval: `daily`, `hourly`, or `weekly` | None       |

### Advanced Options

| Option          | Description                                                                                             | Default     |
| --------------- | ------------------------------------------------------------------------------------------------------- | ----------- |
| `--delay`       | Delay between cities (seconds)                                                                          | `10`        |
| `--max-results` | Most businesses to collect per city. Scrolling stops as soon as enough listings are loaded              | All results |
| `--verbose`     | Show detailed logs                                                                                      | `False`     |
| `--sqlite`      | Also save results to a SQLite DB file, e.g. `data.db`                                                   | None        |
| `--postgres`    | Also save results to PostgreSQL via a connection string, e.g. `postgresql://user:pass@localhost/dbname` | None        |

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

Both CSV and JSON exports share the same field set (see `format_results_for_export` in
[google_maps_scraper.py](google_maps_scraper.py)):

### CSV Output

| Column      | Description                                                                             | Example                           |
| ----------- | --------------------------------------------------------------------------------------- | --------------------------------- |
| id          | Row number within this export                                                           | 1                                 |
| name        | Business name                                                                           | "Sustainable Fashion Co."         |
| description | Reserved for future use, currently blank                                                | ""                                |
| rating      | Average rating                                                                          | "4.5"                             |
| reviewCount | Number of reviews                                                                       | "234"                             |
| phone       | Phone number                                                                            | "+1 212-555-0123"                 |
| email       | Email address scraped from the business website (see [Email Extraction](#new-commands)) | "" if none found                  |
| website     | Business website                                                                        | "https://..."                     |
| address     | Full address                                                                            | "123 Main St, New York, NY 10001" |
| hours       | Opening hours, if shown on the listing                                                  | "Mon-Fri 9 AM-5 PM"               |
| image       | URL of the listing's primary photo                                                      | "https://..."                     |
| verified    | `"true"` if a rating was found, else `"false"`                                          | "true"                            |
| tags        | Reserved for future use, currently blank                                                | ""                                |
| city        | City searched                                                                           | "New York, USA"                   |

### JSON Output

```json
[
  {
    "id": 1,
    "name": "Sustainable Fashion Co.",
    "description": "",
    "rating": "4.5",
    "reviewCount": "234",
    "phone": "+1 212-555-0123",
    "email": "",
    "website": "https://example.com",
    "address": "123 Main St, New York, NY 10001, USA",
    "hours": "Mon-Fri 9 AM-5 PM",
    "image": "https://example.com/photo.jpg",
    "verified": "true",
    "tags": "",
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

## Known Limitations

- **Non-interactive runs need `--test` or `--schedule`.** Without one of those two flags, the CLI
  pauses for a `y/n` confirmation before scraping. For Task Scheduler, cron, or another unattended
  pipeline, add `--test` for a dry run or `--schedule`. A bare
  `python google_maps_scraper.py -q ... -f cities.txt --headless` will hang, or crash with
  `EOFError`, while it waits for input that never arrives.
- **`--sqlite` and `--postgres` always append.** There is no deduplication. Repeated runs, especially
  with `--schedule`, insert the same businesses again on every run instead of updating existing rows.

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
- Configurable delay between cities (default: 10 seconds, varied by 20% either way)
- Random scroll timing to appear more human-like

### Data Quality

- Always verify a sample of results manually
- Use `--test` mode before full scraping runs
- Check for missing data and adjust selectors if needed

## Updates and Versioning

### Version History

## v1.3.2 (Current)

- ✅ Fixed the Python icon showing in the taskbar. The icon now loads from the app folder, so it works from any launch location, and the app sets its own Windows taskbar identity
- ✅ `--delay` now works in the CLI. It was accepted but ignored, and a fixed 8 to 12 second wait was used instead. The wait is now the delay you set, varied by 20% either way
- ✅ With `--workers`, each worker now waits the delay between its own cities, instead of idling for 8 to 12 seconds after every city
- ✅ Stop in the GUI now takes effect within moments, even in the middle of a large city, and the businesses found so far are saved
- ✅ Closing the GUI window during a scrape now asks first, then shuts down every browser it started so no Chrome processes are left running
- ✅ GUI error popups now appear. A blank number box, such as Limit Cities or Parallel Workers, is caught when you press Start, with a clear message
- ✅ Scheduled runs in the CLI and the GUI now survive a failed cycle and try again on the next one. Scheduled GUI runs no longer open a dialog after every cycle
- ✅ Exports no longer start a second browser. If one export fails, for example a CSV that is open in Excel, the other formats are still saved and the failure is reported
- ✅ Website detection now ignores the results list and social, booking, delivery and review links, so a wrong site's email is no longer attached. It also accepts sites on any domain ending, such as `.gr`
- ✅ Email choice is now stable. Junk addresses are skipped, and an address on the business's own domain wins
- ✅ Maps is now requested in English, so ratings, hours and photos are found on systems set to another language
- ✅ Fixed the `-o` examples, which added `.csv` twice
- ✅ `--max-results` and the GUI "Max Results Per City" box now work. They were accepted but ignored before. Scrolling stops as soon as enough listings are loaded, which also saves time
- ✅ The CLI no longer crashes on characters the console cannot show, such as a Greek business name on older Python versions. The scheduler messages are now plain text
- ✅ Saved GUI settings no longer contain passwords. Proxy and database passwords are dropped when you save, and you are told to enter them again after loading
- ✅ Save Logs and Save Settings now work with emoji and non-English text on every Python version
- ✅ The GUI maximizes on launch on Linux too, instead of failing at startup
- ✅ The proxy password is no longer written to the log, and unused code was removed
- ✅ Added a Windows setup file. A GitHub Actions workflow builds `AGMS.exe`, tests it (including a real Chrome start), and packages it with Inno Setup. The installed app saves its results in `Documents\AGMS`
- ✅ Fixed PostgreSQL export. With SQLAlchemy 2.1, a plain `postgresql://` URL looked for a driver this project does not install, so the export failed. It now uses `psycopg2`, and `postgres://` URLs work too
- ✅ Fixed the PostgreSQL example in this README, which could never work

## v1.3.1

- ✅ GUI now has full feature parity with the CLI: proxy support, multi-threaded workers, Excel/SQLite/PostgreSQL export, and scheduling (daily/hourly/weekly)
- ✅ Fixed the GUI's CSV/JSON export, which was missing the `hours` and `city` fields and always leaving `image` blank. It now shares the CLI's own exporter code, so both versions produce identical output
- ✅ The GUI's "Verbose Logging" checkbox now actually works, and the detailed per-business logs from the scraping engine show up in the GUI's log panel
- ✅ Fixed a threading bug where the GUI updated its progress bar and logs directly from a background thread, which is unsafe and could misbehave once multiple workers were involved

## v1.3.0

- ✅ Added `hours` and `image` (primary photo) fields to every export
- ✅ Proxy support (`--proxy`)
- ✅ Multi-threaded scraping (`--workers`)
- ✅ Database export to SQLite (`--sqlite`) and PostgreSQL (`--postgres`)
- ✅ Excel export (`--format excel`)
- ✅ Scheduling and automation (`--schedule daily|hourly|weekly`)
- ✅ Automatic email extraction from each business's website

## v1.0.0 – v1.2.0 (baseline)

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
git clone https://github.com/powergr/AGMS.git
cd AGMS

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python google_maps_scraper.py -q "test query" -c "Test City" --test
```

### Building the Windows installer

A GitHub Actions workflow builds the setup file for you.

1. Set the same `__version__` in `google_maps_scraper.py` and `google_maps_scraper_gui.py`.
2. Push a tag that matches it, for example `v1.3.2`.
3. The workflow in `.github/workflows/windows-installer.yml` builds the app with PyInstaller, runs a self-test on it, and builds the setup file with Inno Setup. It then attaches `AGMS-Setup-1.3.2.exe` and its checksum to a GitHub release.

To try it without a release, open the **Actions** tab, choose **Windows installer**, and select **Run workflow**. The setup file is kept as a downloadable artifact on that run.

To build on your own machine, install Inno Setup 6.3 or newer, then run:

```bash
pip install -r requirements.txt pyinstaller
powershell -ExecutionPolicy Bypass -File build_windows.ps1
```

The setup file appears in `dist\installer`. Add `-SkipInstaller` to build and test only the app, which lands in `dist\AGMS`. The self-test is part of the app, so you can also run `AGMS.exe --self-test` and read `agms_selftest.txt` in your temp folder. Add `--online` to also make an HTTPS request and start Chrome. The build script does this by default, so it needs Chrome and an internet connection. Use `-SkipOnlineTest` to leave those out.

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

- **Issues**: [GitHub Issues](https://github.com/powergr/AGMS/issues)
- **Discussions**: [GitHub Discussions](https://github.com/powergr/AGMS/discussions)
- **Email**: [powergr@gmail.com]

## 🌟 Star the Project

If you find this project useful, please consider giving it a star on GitHub! It helps others discover the tool.

## 📈 Roadmap

### Shipped in v1.3.0 – v1.3.1

_(originally CLI-only in v1.3.0; the GUI reached the same feature set in v1.3.1. See [Version History](#version-history).)_

- ✅ Support for more data fields (hours, photos, etc.)
- ✅ Proxy support for large-scale scraping
- ✅ Database export (SQLite, PostgreSQL)
- ✅ GUI interface
- ✅ Multi-threaded scraping
- ✅ Email extraction improvements
- ✅ Export to Excel format
- ✅ Scheduling and automation features

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

## New commands

1. **Multi-threading (Fast Scraping):**

   ```bash
   python google_maps_scraper.py -q "gyms" -f cities.txt --workers 3 --headless
   ```

   _Note: Ensure you have enough RAM. Each worker opens a new Chrome instance._

2. **Database Exports:**
   _SQLite:_

   ```bash
   python google_maps_scraper.py -q "plumbers" -c "Miami, USA" --sqlite my_database.db
   ```

   _PostgreSQL:_

   ```bash

   python google_maps_scraper.py -q "plumbers" -c "Miami, USA" --postgres "postgresql://admin:password@localhost/maps"
   ```

3. **Excel Export:**

   ```bash
   python google_maps_scraper.py -q "hotels" -c "Paris" --format excel
   ```

4. **Proxies:**

   ```bash
   python google_maps_scraper.py -q "lawyers" -c "London" --proxy http://yourproxy.com:8080
   ```

5. **Scheduling Automation:**

   ```bash
   python google_maps_scraper.py -q "coffee shops" -c "Berlin" --schedule daily
   ```

   _Note: This runs the job immediately once, then keeps the script running in the foreground and
   re-runs it at the start of each following interval (`daily` = every midnight, `hourly` = every
   hour, `weekly` = every week). Press Ctrl+C to stop it. A run that fails is logged, and the next
   cycle tries again._

6. **Email Extraction:**
   This works entirely in the background automatically. The script reads the `website` URL generated by Google Maps, uses `requests` and `BeautifulSoup` to scan the homepage html, checks for `<a href="mailto:...">` attributes, and falls back to a RegEx pattern to find unlinked emails on the landing page. It populates the new `email` field in exports.

---

## Made with ❤️ by Pashalis Laoutaris

**Last Updated**: November 2026

**Repository**: [github.com/powergr/AGMS](https://github.com/powergr/AGMS)

![Python](https://img.shields.io/badge/topic-python-blue)
![Selenium](https://img.shields.io/badge/topic-selenium-blue)
![Web Scraping](https://img.shields.io/badge/topic-web--scraping-blue)
![Google Maps](https://img.shields.io/badge/topic-google--maps-blue)
![Data Extraction](https://img.shields.io/badge/topic-data--extraction-blue)
![Scraper](https://img.shields.io/badge/topic-scraper-blue)
![Automation](https://img.shields.io/badge/topic-automation-blue)
