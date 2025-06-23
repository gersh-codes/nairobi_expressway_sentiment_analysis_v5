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

logger = logging.getLogger('sentiment_logger')

# ─── Constants ───────────────────────────────────────────────────────────────
SCROLL_JS    = "return document.body.scrollHeight"
FB_PAGE_LIST = [
    p.strip().lower() 
    for p in os.getenv("FB_PAGE_LIST", "").split(",") 
    if p.strip()
]

# ─── WebDriver initialization ────────────────────────────────────────────────
def _init_driver(headless: bool) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    return webdriver.Chrome(options=opts)

# ─── Cookie loader for both X and Facebook ───────────────────────────────────
def _load_cookies(env_key: str, driver, base_url: str):
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        logger.debug(f"No cookies found for {env_key}")
        return
    driver.get(base_url)
    cookies = None
    # try JSON
    with suppress(Exception):
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    # fallback pickle
    if not isinstance(cookies, list):
        with suppress(Exception):
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
    if not cookies:
        logger.warning(f"No valid cookies in {path}")
        return
    for c in cookies:
        c.setdefault('sameSite', 'Strict')
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded {len(cookies)} cookies from {path}")

# ─── X.com scraping ──────────────────────────────────────────────────────────
def scrape_x(keyword: str, headless: bool = False) -> list[dict]:
    """
    Full scroll of X.com live search for `keyword`.
    Returns list of {'content','username','date'}.
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
                    content = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                    user    = c.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                    date    = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                except WebDriverException:
                    continue
                tweet = {"content": content, "username": user, "date": date}
                if tweet not in seen:
                    seen.append(tweet)
                    logger.debug(f"→ tweet: {content[:50]}…")
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

# ─── Facebook page‐based scraping ─────────────────────────────────────────────
def _scrape_fb_page(page: str, keyword: str, max_posts: int) -> list[dict]:
    """
    Scrape up to `max_posts` from one Facebook page using facebook_scraper,
    filtering by keyword presence.
    """
    results = []
    creds = {"cookies": os.getenv("FB_COOKIES_PATH")}
    count = 0

    try:
        for post in get_posts(page, pages=5, **creds):
            text = post.get('text') or ""
            if keyword.lower() in text.lower():
                results.append({
                    "page": page,
                    "text": text,
                    "time": post.get("time").isoformat() if post.get("time") else None,
                    "likes": post.get("likes", 0),
                    "comments": post.get("comments", 0)
                })
                count += 1
                logger.debug(f"→ FB post from {page}: {text[:50]}…")
            if count >= max_posts:
                break
    except Exception:
        logger.exception(f"Error scraping FB page '{page}'")

    return results

def scrape_facebook(keyword: str, max_posts: int = 20) -> list[dict]:
    """
    Iterate FB_PAGE_LIST, call `_scrape_fb_page` on each,
    and collect up to `max_posts` posts per page.
    """
    logger.info(f"Scraping Facebook for keyword '{keyword}'")
    all_posts = []
    for page in FB_PAGE_LIST:
        page_posts = _scrape_fb_page(page, keyword, max_posts)
        all_posts.extend(page_posts)
        if len(all_posts) >= max_posts * len(FB_PAGE_LIST):
            break

    logger.info(f"Collected {len(all_posts)} Facebook posts")
    return all_posts
