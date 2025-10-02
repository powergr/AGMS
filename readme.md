A completely generic version with comprehensive command-line arguments.

## 🎯 **Key Features:**

### **Command-Line Arguments:**

**Required:**
- `-q, --query` - Search query (e.g., "sustainability companies", "italian restaurants")
- `-c, --cities` OR `-f, --cities-file` - Cities list or file

**Output Options:**
- `-o, --output` - Output filename (default: google_maps_results)
- `--format` - csv, json, or both (default: both)

**Scraping Options:**
- `--headless` - Run without GUI
- `--limit` - Limit number of cities
- `--test` - Test mode (only first city)
- `--delay` - Delay between cities (default: 10s)
- `-v, --verbose` - Detailed logging

## 📖 **Usage Examples:**

### **1. Basic Usage:**
```bash
# Sustainability companies from file
python generic_maps_scraper.py --query "sustainability companies" --cities-file FASHION_CAPITALS.md

# Or shorter:
python generic_maps_scraper.py -q "sustainability companies" -f FASHION_CAPITALS.md
```

### **2. Search Different Business Types:**
```bash
# Italian restaurants
python generic_maps_scraper.py -q "italian restaurants" -c "New York, USA" "Paris, France"

# Coffee shops
python generic_maps_scraper.py -q "coffee shops" -c "London, UK" "Tokyo, Japan" -o coffee_shops

# Hotels with headless mode
python generic_maps_scraper.py -q "hotels" -f cities.txt --headless

# Gyms with limit
python generic_maps_scraper.py -q "gyms" -c "Berlin, Germany" "Madrid, Spain" --limit 1
```

### **3. Test Before Full Run:**
```bash
# Test with first city only
python generic_maps_scraper.py -q "bakeries" -f cities.txt --test
```

### **4. Custom Output:**
```bash
# Save only CSV
python generic_maps_scraper.py -q "pet stores" -c "Seattle, USA" --format csv -o pet_stores

# Save only JSON
python generic_maps_scraper.py -q "bookstores" -f cities.txt --format json
```

## 🆘 **Help Command:**
```bash
python generic_maps_scraper.py --help
```

This shows all options with examples!

## 🚀 **Quick Start:**

**For your sustainability companies:**
```bash
python generic_maps_scraper.py -q "sustainability companies" -f FASHION_CAPITALS.md
```

**For testing first:**
```bash
python generic_maps_scraper.py -q "sustainability companies" -f FASHION_CAPITALS.md --test
```

**Different business (e.g., vegan restaurants):**
```bash
python generic_maps_scraper.py -q "vegan restaurants" -c "Los Angeles, USA" "San Francisco, USA"
```

## 📊 **Enhanced Statistics:**

The script now shows:
- Total companies found
- Percentage with ratings, reviews, websites, phones, and addresses
- Sample results preview
- Cities processed

```markdown
![Python Version](https://img.shields.io/badge/python-3.7%2B-blue)
![License](https://img.shields.io/badge/license-MIT-green)
![Version](https://img.shields.io/badge/version-1.0.0-orange)
![Status](https://img.shields.io/badge/status-active-success)
```
