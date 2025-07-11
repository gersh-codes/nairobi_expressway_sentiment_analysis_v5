import os
import json
import pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# Confirm that both Requests and Selenium are pointing at the same cert bundle
print(">>> REQUESTS_CA_BUNDLE =", repr(os.environ.get("REQUESTS_CA_BUNDLE")))
print(">>> SSL_CERT_FILE      =", repr(os.environ.get("SSL_CERT_FILE")))

# Ensure the cookies directory exists
os.makedirs("cookies", exist_ok=True)

# Launch Chrome so you can log in manually (headless off by default)
opts = Options()
# opts.add_argument("--headless=new")  # uncomment if you don't need to watch
driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=opts
)

# Navigate to X.com login and wait for you to authenticate
driver.get("https://x.com/login")
input("🔐 Please log in to X.com manually, then press Enter here to save cookies…")

# Grab all cookies from the session
cookies = driver.get_cookies()

# 1) Save as JSON (preferred)
json_path = "cookies/x_cookies.json"
with open(json_path, "w", encoding="utf-8") as f_json:
    json.dump(cookies, f_json, indent=2)
print(f"✅ Saved {len(cookies)} cookies to {json_path}")

# 2) Save as pickle (fallback)
pkl_path = "cookies/x_cookies.pkl"
with open(pkl_path, "wb") as f_pkl:
    pickle.dump(cookies, f_pkl)
print(f"✅ Saved {len(cookies)} cookies to {pkl_path}")

driver.quit()
