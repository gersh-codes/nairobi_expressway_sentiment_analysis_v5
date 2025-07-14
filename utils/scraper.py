import os
import time
import logging
from urllib.parse import quote_plus
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')

# Domains & URLs
X_DOMAIN     = "https://x.com"
SEARCH_FMT   = X_DOMAIN + "/search?q={q}&f={tab}"  # tab: 'top' or 'live'

# Selectors
TWEET_XPATH      = "//article[@data-testid='tweet']"
VIEWPORT_SCROLL  = "window.scrollBy(0, window.innerHeight);"

# Scrolling parameters
LOAD_WAIT        = 10    # seconds to wait after each scroll
MAX_STABLE       = 3     # stop after this many passes with no new tweets


def _init_driver(headless: bool):
    """Initialize Chrome WebDriver with stealth settings."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(60)
    # mask webdriver for anti-bot
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver',{get:() => undefined});"
    })
    return drv


def _safe_get(driver, url: str) -> bool:
    """Navigate to URL, retrying once on TimeoutException."""
    try:
        driver.get(url)
        return True
    except TimeoutException:
        logger.warning("Timeout loading %s; retrying once", url)
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.error("Failed to load %s after retry", url)
            return False


def _load_cookies(env_key: str, driver, domain: str):
    """Inject cookies so we stay logged in on X.com."""
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        return
    driver.get(domain)
    cookies = []
    try:
        import json, pickle
        # prefer JSON, fallback to pickle
        with open(path, encoding='utf-8') as f:
            cookies = json.load(f)
    except Exception:
        try:
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
        except Exception:
            cookies = []
    for c in cookies:
        c['domain'] = '.x.com'
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug("Loaded %d cookies from %s", len(cookies), env_key)


def _fetch_all(driver):
    """
    Return list of (text, user, date) tuples for every tweet on page.
    """
    out = []
    cards = driver.find_elements(By.XPATH, TWEET_XPATH)
    for c in cards:
        try:
            txt = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
            usr = c.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
            dt  = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
            out.append((txt, usr, dt))
        except WebDriverException:
            continue
    return out


def _scrape_tab(driver):
    """
    Scroll‑and‑collect tweets from the current X.com tab.
    Scroll one viewport at a time, pausing LOAD_WAIT for new tweets,
    stopping after MAX_STABLE passes with no growth.
    """
    # wait for tweets to appear
    WebDriverWait(driver, 30).until(
        EC.presence_of_element_located((By.XPATH, TWEET_XPATH))
    )
    collected = _fetch_all(driver)
    stable = 0

    while stable < MAX_STABLE:
        prev_count = len(collected)
        # scroll one viewport
        driver.execute_script(VIEWPORT_SCROLL)

        # wait up to LOAD_WAIT for count to increase
        deadline = time.time() + LOAD_WAIT
        new_list = collected
        while time.time() < deadline:
            new_list = _fetch_all(driver)
            if len(new_list) > prev_count:
                break
            time.sleep(0.5)

        # merge new
        for t in new_list:
            if t not in collected:
                collected.append(t)

        # track stability
        if len(collected) == prev_count:
            stable += 1
            logger.debug("No new posts (pass %d/%d)", stable, MAX_STABLE)
        else:
            stable = 0
            logger.debug("Found %d posts so far", len(collected))

    # convert to dicts
    return [{"content": t[0], "username": t[1], "date": t[2]} for t in collected]


def scrape_x(keywords: str, headless: bool=False):
    """
    Accept either a single keyword or list of keywords.
    Scrape all tweets from both the Top (f=top) and Latest (f=live) tabs for `keyword`.
    De‑duplicates across both tabs (globally).
    Returns list of dicts: {'content','username','date'}.
    """
    logger.info("Scraping X.com for '%s' (Top + Live)", keywords)
    if isinstance(keywords,str): keywords=[keywords]
    driver = _init_driver(headless)
    all_tweets = []
    seen = set()
    try:
        # restore login session
        _load_cookies("X_COOKIES_PATH", driver, X_DOMAIN)
        for kw in keywords:
        # iterate over both tabs
            for tab in ("top", "live"):
                url = SEARCH_FMT.format(q=quote_plus(kw), tab=tab)
                if not _safe_get(driver, url):
                    continue
                tweets = _scrape_tab(driver)
                # dedupe across both tabs
                for t in tweets:
                    key = (t['username'], t['date'], t['content'])
                    if key not in seen:
                        seen.add(key)
                        all_tweets.append(t)

        logger.info("Collected %d unique posts in total", len(all_tweets))
        return all_tweets

    except Exception as e:
        logger.exception("scrape_x error: %s", e)
        return []
    finally:
        driver.quit()

def scrape_facebook(_keywords: str, _headless: bool=False):
    """
    (Temporary stub) Facebook scraping is disabled for now.
    Returns an empty list so the rest of the pipeline still works.
    """
    return []