import os
import time
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"

def _init_driver(headless: bool):
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(30)
    return drv

def _safe_get(driver, url, retries=3):
    """Load URL, retry on timeout and try clicking any 'Retry' button."""
    for i in range(retries):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.warning(f"Timeout loading {url}, retry {i+1}/{retries}")
            with suppress(Exception):
                btn = driver.find_element(By.XPATH, "//button[contains(text(),'Retry')]")
                btn.click()
            time.sleep(2)
    logger.error(f"Failed to load {url}")
    return False

def scrape_x(keyword: str, headless: bool = False):
    """Scrape ALL live-search tweets for keyword."""
    logger.info(f"Scraping X.com for '{keyword}'")
    driver = _init_driver(headless)
    try:
        # first load domain & cookies
        driver.get("https://x.com")
        # (optional) _load_cookies(...) here if you have a cookies loader
        search = f"https://x.com/search?q={keyword.replace(' ','%20')}&f=live"
        if not _safe_get(driver, search):
            return []

        # wait for first tweet card
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))
        )

        seen, last_h = [], driver.execute_script(SCROLL_JS)
        while True:
            cards = driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
            for c in cards:
                try:
                    txt = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                    usr = c.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                    dt  = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                except WebDriverException:
                    continue
                rec = {"content": txt, "username": usr, "date": dt}
                if rec not in seen:
                    seen.append(rec)
                    logger.debug(f"→ tweet: {txt[:50]}…")
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
            time.sleep(2)
            h = driver.execute_script(SCROLL_JS)
            if h == last_h:
                break
            last_h = h

        logger.info(f"Collected {len(seen)} tweets")
        return seen

    except Exception:
        logger.exception("Unexpected error in scrape_x")
        return []
    finally:
        driver.quit()

def scrape_facebook(keyword: str, headless: bool = False):
    """Scrape ALL Facebook posts for keyword via Selenium search."""
    logger.info(f"Scraping Facebook for '{keyword}'")
    driver = _init_driver(headless)
    try:
        url = f"https://www.facebook.com/search/posts/?q={keyword.replace(' ','%20')}"
        if not _safe_get(driver, url):
            return []

        time.sleep(4)
        # click “Posts” filter
        with suppress(Exception):
            tab = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Posts']"))
            )
            tab.click()
            time.sleep(2)

        posts, last_h = [], driver.execute_script(SCROLL_JS)
        body = driver.find_element(By.TAG_NAME, 'body')

        while True:
            body.send_keys(Keys.END)
            time.sleep(2)
            h = driver.execute_script(SCROLL_JS)
            if h == last_h:
                break
            last_h = h

            cards = driver.find_elements(By.XPATH, "//div[contains(@data-testid,'post_message')]")
            for c in cards:
                try:
                    text = c.text.split('\n',1)[0].strip()
                    abbr = c.find_element(By.TAG_NAME, 'abbr')
                    post_time = abbr.get_attribute('data-utime') or abbr.get_attribute('title') or abbr.text
                except Exception:
                    continue
                posts.append({"post_text": text, "post_time": post_time})
                logger.debug(f"→ fb post: {text[:50]}…")

        logger.info(f"Collected {len(posts)} FB posts")
        return posts

    except Exception:
        logger.exception("Unexpected error in scrape_facebook")
        return []
    finally:
        driver.quit()
