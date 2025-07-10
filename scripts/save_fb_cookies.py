#!/usr/bin/env python
import os, json, pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

os.makedirs("cookies", exist_ok=True)

opts = Options()
# Comment out headless for manual captcha solving
# opts.add_argument("--headless=new")
opts.add_argument("--disable-blink-features=AutomationControlled")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=opts
)

driver.get("https://www.facebook.com/login")
input("🔐 Please log in to Facebook, then press Enter here to save cookies…")

cookies = driver.get_cookies()

# Save JSON (used by scraper)
with open("cookies/fb_cookies.json", "w", encoding="utf-8") as f:
    json.dump(cookies, f, indent=2)
print(f"✅ Saved {len(cookies)} cookies to cookies/fb_cookies.json")

# Save Pickle fallback
with open("cookies/fb_cookies.pkl", "wb") as f:
    pickle.dump(cookies, f)
print(f"✅ Saved {len(cookies)} cookies to cookies/fb_cookies.pkl")

driver.quit()
