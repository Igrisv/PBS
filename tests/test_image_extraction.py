import logging
import json
import sys
import io
from scraper import scrape

# Fix output encoding for Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

logging.basicConfig(level=logging.INFO)

# A more stable product URL (Pokémon TCG on Amazon MX)
test_url = "https://www.amazon.com.mx/dp/B0D1N9N9S8"
print(f"\n[TESTING SCRAPER IMAGE EXTRACTION] URL: {test_url}")

snapshot = scrape("Test Product", test_url)

if snapshot.image_url:
    print(f"OK - Image URL found: {snapshot.image_url}")
else:
    print("FAIL - No Image URL found.")

if snapshot.captcha_detected:
    print("WARN - CAPTCHA detected, extraction might have failed for that reason.")
