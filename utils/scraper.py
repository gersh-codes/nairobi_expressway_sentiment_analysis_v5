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
TWEET_XPATH        = "//article[@data-testid='tweet']"
X_SEARCH_URL       = "https://x.com/search?q={q}&f=top"
FB_DOMAIN          = "https://www.facebook.com"
# how many passes with no new tweets to stop
MAX_STABLE         = 3
# seconds to wait for new tweets after scroll
LOAD_TIMEOUT       = 10


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
    # hide webdriver flag for stealth
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return drv


def _load_cookies(env_key: str, driver, domain: str):
    """Inject cookies from disk so we’re past the login wall."""
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
    """Navigate to URL, with up to 3 manual‑retry prompts on timeout."""
    for i in range(1, 4):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.warning("Timeout %d loading %s", i, url)
            time.sleep(1)
    logger.error("Giving up on %s after timeouts", url)
    return False


def _scrape_x_live(driver):
    """
    Custom scroll loop for X.com Top feed:
      - scroll down
      - wait up to LOAD_TIMEOUT for new tweet cards to appear
      - stop once there are MAX_STABLE passes with no new cards
    """
    # wait for first tweet
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.XPATH, TWEET_XPATH)))

    collected = []       # deduped list of tweets
    stable_passes = 0    # how many times in a row count didn't grow

    def fetch_all():
        """Grab all currently on‑page tweets as dicts."""
        out = []
        for card in driver.find_elements(By.XPATH, TWEET_XPATH):
            try:
                text = card.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                user = card.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                date = card.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                out.append((text, user, date))
            except WebDriverException:
                continue
        return out

    # initial fetch
    current = fetch_all()
    for t in current:
        collected.append(t)

    # loop until no growth
    while stable_passes < MAX_STABLE:
        prev_count = len(collected)
        # scroll
        driver.execute_script(SCROLL_JS)
        # wait for new cards or timeout
        end = time.time() + LOAD_TIMEOUT
        while time.time() < end:
            latest = fetch_all()
            if len(latest) > prev_count:
                break
            time.sleep(0.5)
        # merge new ones
        for t in latest:
            if t not in collected:
                collected.append(t)
        # check stability
        if len(collected) == prev_count:
            stable_passes += 1
            logger.debug("No new tweets (pass %d/%d)", stable_passes, MAX_STABLE)
        else:
            stable_passes = 0
            logger.debug("Found %d tweets so far", len(collected))

    # convert tuples back to dicts
    return [{"content": t[0], "username": t[1], "date": t[2]} for t in collected]


def scrape_x(keyword: str, headless: bool=False):
    """
    Scrape *all* Top‑tab tweets for `keyword` on X.com.
    Returns list of {'content','username','date'} dicts.
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
