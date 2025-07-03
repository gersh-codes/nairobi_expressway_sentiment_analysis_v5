import os
import pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

print(">>> REQUESTS_CA_BUNDLE =", repr(os.environ.get("REQUESTS_CA_BUNDLE")))
print(">>> SSL_CERT_FILE      =", repr(os.environ.get("SSL_CERT_FILE")))

os.makedirs("cookies", exist_ok=True)

options = Options()
driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

driver.get("https://x.com/login")
input("🔐 Login to X.com manually, then press Enter to save cookies...")

with open("cookies/x_cookies.pkl", "wb") as f:
    pickle.dump(driver.get_cookies(), f)

driver.quit()
print("✅ X.com cookies saved to cookies/x_cookies.pkl")