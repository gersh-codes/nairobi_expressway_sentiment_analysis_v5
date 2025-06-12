#!/usr/bin/env python
import os
import json
import pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# Ensure cookies directory exists
os.makedirs("cookies", exist_ok=True)

# Chrome options
options = Options()
# you can comment out headless if you want to watch the login
# options.add_argument("--headless=new")
options.add_argument("--disable-blink-features=AutomationControlled")

# Launch browser
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# Navigate to Facebook login
driver.get("https://www.facebook.com/login")

print("🔐 Please log in to Facebook in the opened browser window, then press Enter here to save cookies…")
input()

# Get cookies and save as JSON and pickle
cookies = driver.get_cookies()

# JSON format
json_path = "cookies/fb_cookies.json"
with open(json_path, "w", encoding="utf-8") as f_json:
    json.dump(cookies, f_json, indent=2)
print(f"✅ Saved {len(cookies)} cookies to {json_path}")

# Pickle format (optional fallback)
pickle_path = "cookies/fb_cookies.pkl"
with open(pickle_path, "wb") as f_pkl:
    pickle.dump(cookies, f_pkl)
print(f"✅ Saved {len(cookies)} cookies to {pickle_path}")

driver.quit()
