import os
import time
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,           # ← Added missing import
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from urllib.parse import quote_plus

logger = logging.getLogger('sentiment_logger')

SCROLL_JS            = "return document.body.scrollHeight"
MAX_MANUAL_RETRIES   = 3
RETRY_XPATH          = "//button[contains(.,'Retry')]"
CAPTCHA_XPATH        = "//iframe[contains(@src,'captcha')]"
X_DOMAIN             = "https://x.com"
FB_DOMAIN            = "https://www.facebook.com"

def _init_driver(headless: bool):
    """Initialize Chrome WebDriver with stealth options."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")

    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(60)
    # Hide webdriver flag & fake plugins/languages
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = {runtime: {}};
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
        """
    })
    return driver

def _load_cookies(env_key: str, driver, domain: str):
    """
    Inject cookies from JSON or pickle file at env_key into `domain`,
    then refresh to apply them.
    """
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        return
    driver.get(domain)
    with suppress(Exception):
        import json, pickle
        try:
            cookies = json.load(open(path, encoding='utf-8'))
        except:
            cookies = pickle.load(open(path, 'rb'))
        for c in cookies or []:
            c['domain'] = ('.facebook.com' if 'facebook' in domain else '.x.com')
            with suppress(Exception):
                driver.add_cookie(c)
    driver.refresh()
    logger.debug("Loaded %d cookies from %s", len(cookies or []), env_key)

def _safe_get(driver, url: str) -> bool:
    """
    Navigate to url, allowing manual RETRY up to MAX_MANUAL_RETRIES times.
    """
    for attempt in range(1, MAX_MANUAL_RETRIES+1):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.warning("Timeout loading %s (%d/%d)", url, attempt, MAX_MANUAL_RETRIES)
            if driver.find_elements(By.XPATH, RETRY_XPATH):
                logger.info("➡️ Click RETRY in browser to continue…")
                prev = driver.execute_script(SCROLL_JS)
                WebDriverWait(driver, 60).until(
                    lambda d, p=prev: d.execute_script(SCROLL_JS) > p
                )
            time.sleep(1)
    logger.error("Failed to load %s after retries", url)
    return False

def _check_captcha(driver, context: str) -> bool:
    """Return True and log if a CAPTCHA frame is detected."""
    if driver.find_elements(By.XPATH, CAPTCHA_XPATH):
        logger.error("%s: CAPTCHA detected – aborting", context)
        return True
    return False

def _handle_retry(driver, context: str, manual_count: int, last_h: int):
    """
    If a RETRY button is present, allow manual click up to limit.
    Returns (new_manual_count, new_last_h, should_continue, should_abort).
    """
    if not driver.find_elements(By.XPATH, RETRY_XPATH):
        return manual_count, last_h, False, False

    manual_count += 1
    if manual_count > MAX_MANUAL_RETRIES:
        logger.info("%s: manual retry limit reached – stopping", context)
        return manual_count, last_h, False, True

    logger.warning("%s: RETRY seen (%d/%d); waiting for new content…",
                   context, manual_count, MAX_MANUAL_RETRIES)
    prev = driver.execute_script(SCROLL_JS)
    WebDriverWait(driver, 60).until(
        lambda d, p=prev: d.execute_script(SCROLL_JS) > p
    )
    new_h = driver.execute_script(SCROLL_JS)
    return manual_count, new_h, True, False

def _scroll_collect(driver, collect_fn, context: str):
    """
    Generic scroll-and-collect loop:
      1) collect via collect_fn()
      2) scroll down
      3) abort on CAPTCHA
      4) allow manual RETRY up to MAX_MANUAL_RETRIES
      5) stop after 2 stable (no-height-change) passes
    """
    items = []
    last_h = driver.execute_script(SCROLL_JS)
    stable = manual = 0
    logger.debug("%s: start height=%d", context, last_h)

    while True:
        # 1) Collect new items
        for rec in collect_fn():
            if rec not in items:
                items.append(rec)

        # 2) Scroll page
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # 3) CAPTCHA check
        if _check_captcha(driver, context):
            break

        # 4) Manual retry handling
        manual, last_h, did_retry, abort = _handle_retry(driver, context, manual, last_h)
        if abort:
            break
        if did_retry:
            stable = 0
            continue

        # 5) Height stability check
        new_h = driver.execute_script(SCROLL_JS)
        logger.debug("%s: scrolled new=%d last=%d", context, new_h, last_h)
        if new_h == last_h:
            stable += 1
            if stable >= 2:
                logger.info("%s: no new content – end scroll", context)
                break
        else:
            last_h, stable = new_h, 0

    return items

def scrape_x(keyword: str, headless: bool=False):
    """
    Scrape *all* live-search tweets for `keyword` on X.com.
    Returns a list of dicts: {content, username, date}.
    """
    logger.info("Scraping X.com for '%s'", keyword)
    driver = _init_driver(headless)
    try:
        _load_cookies("X_COOKIES_PATH", driver, X_DOMAIN)

        # Build and load the search URL
        q = quote_plus(f"{keyword} f=live")
        url = f"{X_DOMAIN}/search?q={q}"
        if not _safe_get(driver, url):
            return []

        # Wait for first tweet to appear
        WebDriverWait(driver, 30).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))
        )

        def collect_tweets():
            out = []
            for card in driver.find_elements(By.XPATH, "//article[@data-testid='tweet']"):
                try:
                    text = card.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                    user = card.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                    date = card.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                    out.append({"content": text, "username": user, "date": date})
                except WebDriverException:
                    continue
            return out

        tweets = _scroll_collect(driver, collect_tweets, "X.com")
        logger.info("Collected %d tweets", len(tweets))
        return tweets

    except Exception as e:
        logger.exception("scrape_x error: %s", e)
        return []
    finally:
        driver.quit()

def scrape_facebook(keyword: str, headless: bool=False):
    """
    Scrape *all* public Facebook posts for `keyword`.
    Returns a list of dicts: {post_text, post_time}.
    """
    logger.info("Scraping Facebook for '%s'", keyword)
    driver = _init_driver(headless)
    try:
        _load_cookies("FB_COOKIES_PATH", driver, FB_DOMAIN)

        url = f"{FB_DOMAIN}/search/posts/?q={quote_plus(keyword)}"
        if not _safe_get(driver, url):
            return []

        # Let feed stabilize
        time.sleep(4)

        # Click “Posts” filter if available
        with suppress(TimeoutException, NoSuchElementException):
            tab = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Posts']"))
            )
            tab.click()
            time.sleep(2)

        def collect_posts():
            out = []
            for card in driver.find_elements(By.XPATH, "//div[@role='article']"):
                try:
                    text = card.find_element(By.CSS_SELECTOR, "div[dir='auto']").text.strip()
                    abbr = card.find_element(By.TAG_NAME, "abbr")
                    when = abbr.get_attribute("data-utime") or abbr.get_attribute("title") or abbr.text
                    out.append({"post_text": text, "post_time": when})
                except WebDriverException:
                    continue
            return out

        posts = _scroll_collect(driver, collect_posts, "Facebook")
        logger.info("Collected %d FB posts", len(posts))
        return posts

    except Exception as e:
        logger.exception("scrape_facebook error: %s", e)
        return []
    finally:
        driver.quit()
