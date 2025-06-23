import os
import time
import json
import pickle
import logging
import datetime
from contextlib import suppress
from facebook_scraper import get_posts
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

logger = logging.getLogger('sentiment_logger')

SCROLL_JS    = "return document.body.scrollHeight"
FB_PAGE_LIST = [
    p.strip().lower()
    for p in os.getenv("FB_PAGE_LIST", "").split(",")
    if p.strip()
]

def _init_driver(headless: bool) -> webdriver.Chrome:
    """Create a Chrome WebDriver with optional headless mode."""
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

def _load_cookies(envkey: str, driver, url: str):
    """Load JSON or pickle cookies from env-specified path into driver."""
    path = os.getenv(envkey, "")
    if not path or not os.path.exists(path):
        logger.debug(f"No cookies for {envkey}")
        return
    driver.get(url)
    cookies = None
    with suppress(Exception):
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    if not isinstance(cookies, list):
        with suppress(Exception):
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
    if not cookies:
        logger.warning(f"Invalid cookie file at {path}")
        return
    for c in cookies:
        c.setdefault('sameSite', 'Strict')
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded {len(cookies)} cookies")

def scrape_x(keyword: str, headless: bool = False) -> list[dict]:
    """
    Scroll through *all* live-search tweets for `keyword`.
    Returns list of {content, username, date}.
    """
    logger.info(f"Scraping X.com for '{keyword}'")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies("X_COOKIES_PATH", driver, "https://x.com")

        url = f"https://x.com/search?q={keyword.replace(' ', '%20')}&f=live"
        driver.get(url)
        time.sleep(5)

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
                tweet = {"content": txt, "username": usr, "date": dt}
                if tweet not in seen:
                    seen.append(tweet)
                    logger.debug(f"→ tweet: {txt[:50]}…")
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
            time.sleep(2)
            h = driver.execute_script(SCROLL_JS)
            if h == last_h:
                break
            last_h = h

        logger.info(f"Collected {len(seen)} tweets")
        return seen

    except (WebDriverException, TimeoutException):
        logger.exception("X.com scrape failed")
    finally:
        if driver:
            driver.quit()
    return []

def scrape_facebook(keyword: str, max_posts: int = 20) -> list[dict]:
    """
    For each FB_PAGE_LIST entry, fetch up to `max_posts` posts containing keyword,
    returning a combined list of {page,text,time,likes,comments}.
    """
    logger.info(f"Scraping Facebook for '{keyword}'")
    all_posts = []
    creds = {"cookies": os.getenv("FB_COOKIES_PATH")}

    for page in FB_PAGE_LIST:
        count = 0
        try:
            for post in get_posts(page, pages=5, **creds):
                text = post.get('text') or ""
                if keyword.lower() not in text.lower():
                    continue
                all_posts.append({
                    "page": page,
                    "text": text,
                    "time": post.get("time").isoformat() if post.get("time") else None,
                    "likes": post.get("likes", 0),
                    "comments": post.get("comments", 0)
                })
                logger.debug(f"→ FB {page}: {text[:50]}…")
                count += 1
                if count >= max_posts:
                    break
        except Exception:
            logger.exception(f"Error scraping FB page '{page}'")

    logger.info(f"Collected {len(all_posts)} Facebook posts")
    return all_posts
