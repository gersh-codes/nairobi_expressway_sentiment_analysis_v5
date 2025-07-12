import os
import time
import logging
from urllib.parse import quote_plus
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, NoSuchElementException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')

# JS snippet to get full document height
SCROLL_JS          = "window.scrollTo(0, document.body.scrollHeight);"
# How many manual “Retry” clicks to allow
MAX_MANUAL_RETRIES = 3
# XPaths for Retry button & CAPTCHA iframe
RETRY_XPATH        = "//button[contains(.,'Retry')]"
CAPTCHA_XPATH      = "//iframe[contains(@src,'captcha')]"
# Domains for cookie injection
X_DOMAIN           = "https://x.com"
FB_DOMAIN          = "https://www.facebook.com"
# CSS/XPath constants
TWEET_XPATH        = "//article[@data-testid='tweet']"
X_SEARCH_URL       = "https://x.com/search?q={q}&f=top"
# Scrolling & timing settings
VIEWPORT_SCROLL    = "window.scrollBy(0, window.innerHeight);"  # one screen at a time
MAX_STABLE_PASSES  = 3      # how many passes with no new tweets to stop
LOAD_WAIT          = 10     # seconds to wait for new tweets after each scroll
RETRY_TIMEOUTS     = 3      # how many times to retry driver.get

def _init_driver(headless: bool):
    """Initialize Chrome WebDriver with stealth settings."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    # mask webdriver property for anti-bot checks
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return driver


def _load_cookies(env_key: str, driver, domain: str):
    """Load and inject cookies so we stay logged in."""
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        return
    driver.get(domain)
    cookies = []
    with suppress(Exception):
        import json, pickle
        try:
            cookies = json.load(open(path, encoding='utf-8'))
        except Exception:
            cookies = pickle.load(open(path, 'rb'))
        for c in cookies:
            c['domain'] = '.x.com'
            with suppress(Exception):
                driver.add_cookie(c)
    driver.refresh()
    logger.debug("Loaded %d cookies from %s", len(cookies), env_key)


def _safe_get(driver, url: str) -> bool:
    """
    Navigate to URL, retrying up to RETRY_TIMEOUTS times on TimeoutException.
    """
    for i in range(1, RETRY_TIMEOUTS + 1):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.warning("Timeout %d/%d loading %s", i, RETRY_TIMEOUTS, url)
            time.sleep(1)
    logger.error("Failed to load %s after %d timeouts", url, RETRY_TIMEOUTS)
    return False


def _scrape_x_live(driver):
    """
    Scroll‑and‑collect for X Top feed:
      - scroll down by one viewport at a time
      - wait up to LOAD_WAIT for new tweets
      - stop after MAX_STABLE_PASSES with no count increase
    """
    # wait for the first tweet element
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.XPATH, TWEET_XPATH)))

    def fetch():
        """Return list of (text, user, date) tuples for all tweets on page."""
        out = []
        for c in driver.find_elements(By.XPATH, TWEET_XPATH):
            try:
                txt = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                usr = c.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                dt  = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                out.append((txt, usr, dt))
            except WebDriverException:
                continue
        return out

    collected = []
    # initial collection
    for t in fetch():
        collected.append(t)

    stable = 0
    # continue until we see MAX_STABLE_PASSES in a row with no growth
    while stable < MAX_STABLE_PASSES:
        prev_count = len(collected)
        # scroll one viewport
        driver.execute_script(VIEWPORT_SCROLL)
        # wait for new tweets or timeout
        deadline = time.time() + LOAD_WAIT
        while time.time() < deadline:
            new_list = fetch()
            if len(new_list) > prev_count:
                break
            time.sleep(0.5)

        # merge any new ones
        for t in new_list:
            if t not in collected:
                collected.append(t)

        # check stability
        if len(collected) == prev_count:
            stable += 1
            logger.debug("No new tweets (pass %d/%d)", stable, MAX_STABLE_PASSES)
        else:
            stable = 0
            logger.debug("Found %d tweets so far", len(collected))

    # convert to dicts
    return [{"content": t[0], "username": t[1], "date": t[2]} for t in collected]


def scrape_x(keyword: str, headless: bool=False):
    """
    Scrape *all* Top‑tab tweets for `keyword` on X.com.
    Returns list of dicts: {'content','username','date'}.
    """
    logger.info("Scraping X.com for '%s' (Top)", keyword)
    driver = _init_driver(headless)
    try:
        _load_cookies("X_COOKIES_PATH", driver, "https://x.com")
        url = X_SEARCH_URL.format(q=quote_plus(keyword))
        if not _safe_get(driver, url):
            return []
        tweets = _scrape_x_live(driver)
        logger.info("Collected %d tweets", len(tweets))
        return tweets
    except Exception as e:
        logger.exception("scrape_x error: %s", e)
        return []
    finally:
        driver.quit()


def _prepare_facebook(driver, keyword: str) -> bool:
    """
    Load FB search, then click “Posts” filter via ActionChains + JS scroll.
    """
    url = f"{FB_DOMAIN}/search/posts/?q={quote_plus(keyword)}"
    if not _safe_get(driver, url):
        return False

    time.sleep(4)
    with suppress(Exception):
        tab = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//span[text()='Posts']"))
        )
        driver.execute_script("arguments[0].scrollIntoView();", tab)
        time.sleep(1)
        ActionChains(driver).move_to_element(tab).click().perform()
        time.sleep(3)
    return True


def scrape_facebook(keyword: str, headless: bool = False):
    """
    Public: scrape all public Facebook posts for `keyword`.
    Returns list of {post_text, post_time}.
    """
    logger.info("Scraping Facebook for '%s'", keyword)
    driver = _init_driver(headless)
    try:
        _load_cookies("FB_COOKIES_PATH", driver, FB_DOMAIN)
        if not _prepare_facebook(driver, keyword):
            return []

        def collect_posts():
            out = []
            cards = driver.find_elements(By.XPATH, "//div[@role='article']")
            for card in cards:
                try:
                    text = card.find_element(By.CSS_SELECTOR, "div[dir='auto']").text.strip()
                    abbr = card.find_element(By.TAG_NAME, "abbr")
                    when = (
                        abbr.get_attribute("data-utime")
                        or abbr.get_attribute("title")
                        or abbr.text
                        or "unknown"
                    )
                    out.append({"post_text": text, "post_time": when})
                except WebDriverException:
                    continue
            return out

        posts = _scroll_collect(driver, collect_posts, "Facebook", max_idle=10)
        logger.info("Collected %d FB posts", len(posts))
        return posts

    except Exception as e:
        logger.exception("scrape_facebook error: %s", e)
        return []
    finally:
        driver.quit()
