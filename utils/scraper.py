# utils/scraper.py

import os
import time
import json
import pickle
import logging
from contextlib import suppress

from facebook_scraper import get_posts
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"
FB_PAGE_LIST = [
    p.strip() for p in os.getenv("FB_PAGE_LIST", "").split(",") if p.strip()
]


def _init_driver(headless: bool) -> webdriver.Chrome:
    """Initialize Chrome WebDriver with common options."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def _load_cookies(envkey: str, driver, url: str):
    """Load cookies from JSON or pickle into driver for given URL."""
    path = os.getenv(envkey, "")
    if not path or not os.path.exists(path):
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
    if not isinstance(cookies, list):
        logger.warning(f"Invalid cookie file at {path}")
        return
    for c in cookies:
        c.setdefault('sameSite', 'Strict')
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()


def _handle_retry(driver):
    """If X.com shows a Retry button, click it and wait for tweets to reappear."""
    with suppress(Exception):
        btn = driver.find_element(By.XPATH, "//span[text()='Retry']")
        btn.click()
        WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.XPATH, "//article"))
        )
        logger.info("Clicked X.com Retry")


def scrape_x(keyword: str, headless: bool = False) -> list[dict]:
    """
    Scroll through *all* live-search tweets for `keyword`.
    Returns list of {content, username, date}.
    """
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies("X_COOKIES_PATH", driver, "https://x.com")

        url = f"https://x.com/search?q={keyword.replace(' ', '%20')}&f=live"
        driver.get(url)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//article"))
        )

        seen: list[dict] = []
        last_height = driver.execute_script(SCROLL_JS)

        for _ in range(50):  # limit to 50 scrolls
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

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(1)

            # capture last_height in default arg to avoid closure issue
            try:
                WebDriverWait(driver, 5).until(
                    lambda d, lh=last_height: d.execute_script(SCROLL_JS) > lh
                )
            except TimeoutException:
                _handle_retry(driver)

            new_height = driver.execute_script(SCROLL_JS)
            if new_height == last_height:
                break
            last_height = new_height

        logger.info(f"Collected {len(seen)} tweets")
        return seen

    except (WebDriverException, TimeoutException):
        logger.exception("X.com scrape failed")
    finally:
        if driver:
            driver.quit()
    return []


def _scrape_single_fb_page(page: str, keyword: str, max_posts: int, creds: dict) -> list[dict]:
    """
    Helper to scrape up to max_posts from one Facebook page.
    Extracts only those posts containing keyword.
    """
    out = []
    count = 0
    try:
        for post in get_posts(page, pages=10, **creds):
            text = post.get("text") or ""
            if keyword.lower() not in text.lower():
                continue
            out.append({
                "page": page,
                "text": text,
                "time": post.get("time").isoformat() if post.get("time") else None,
                "likes": post.get("likes", 0),
                "comments": post.get("comments", 0)
            })
            count += 1
            if count >= max_posts:
                break
    except Exception:
        logger.exception(f"Error scraping Facebook page '{page}'")
    return out


def scrape_facebook(keyword: str, max_posts: int = 100) -> list[dict]:
    """
    Iterate through FB_PAGE_LIST, collect up to max_posts per page
    whose text contains keyword. Returns flattened list.
    """
    logger.info(f"Scraping Facebook for '{keyword}'")
    creds = {}
    if (path := os.getenv("FB_COOKIES_PATH")):
        creds["cookies"] = path

    all_posts: list[dict] = []
    for page in FB_PAGE_LIST:
        posts = _scrape_single_fb_page(page, keyword, max_posts, creds)
        all_posts.extend(posts)
    logger.info(f"Collected {len(all_posts)} Facebook posts")
    return all_posts
