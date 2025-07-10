import os, pickle
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# Confirm that Python and Requests use your cert bundle
print(">>> REQUESTS_CA_BUNDLE =", repr(os.environ.get("REQUESTS_CA_BUNDLE")))
print(">>> SSL_CERT_FILE      =", repr(os.environ.get("SSL_CERT_FILE")))

os.makedirs("cookies", exist_ok=True)

opts = Options()
# watch the browser so you can solve captchas if needed
# opts.add_argument("--headless=new")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=opts
)

driver.get("https://x.com/login")
input("🔐 Please log in to X.com manually, then press Enter here to save cookies...")

with open("cookies/x_cookies.pkl", "wb") as f:
    pickle.dump(driver.get_cookies(), f)

driver.quit()
print("✅ X.com cookies saved to cookies/x_cookies.pkl")
