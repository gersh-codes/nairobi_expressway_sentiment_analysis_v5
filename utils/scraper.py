import os
import time
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException,
    WebDriverException,
    NoSuchElementException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')

SCROLL_JS        = "return document.body.scrollHeight"
RETRY_DIV_XPATH  = "//div[@role='button'][.//span[text()='Retry']]"
CAPTCHA_IFRAME   = "//iframe[contains(@src,'captcha')]"
MAX_MANUAL_TRIES = 3

def _init_driver(headless: bool):
    """Initialize Chrome WebDriver."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    d = webdriver.Chrome(options=opts)
    d.set_page_load_timeout(60)
    return d

def _safe_get(driver, url, retries=3):
    """Load URL, allowing manual Retry clicks if needed."""
    for i in range(1, retries+1):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.warning("Timeout loading %s (%d/%d)", url, i, retries)
            if driver.find_elements(By.XPATH, RETRY_DIV_XPATH):
                logger.info("Please click 'Retry' in the browser…")
                prev = driver.execute_script(SCROLL_JS)
                WebDriverWait(driver, 60).until(lambda d, p=prev: d.execute_script(SCROLL_JS) > p)
            time.sleep(1)
    logger.error("Failed to load %s", url)
    return False

def _load_cookies(env_key, driver, domain):
    """Inject saved cookies for a domain."""
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        return
    driver.get(domain)
    with suppress(Exception):
        import json, pickle
        try:
            ck = json.load(open(path, encoding='utf-8'))
        except Exception:
            ck = pickle.load(open(path, 'rb'))
        for c in ck or []:
            c.setdefault('sameSite', 'Lax')
            with suppress(Exception):
                driver.add_cookie(c)
    driver.refresh()

def _detect_captcha(driver, context):
    """Return True if we see a CAPTCHA iframe."""
    if driver.find_elements(By.XPATH, CAPTCHA_IFRAME):
        logger.error("%s: CAPTCHA detected, aborting", context)
        return True
    return False

def _advance_and_check(driver, context, last_h, stable, manual_count):
    """
    Scroll once and handle:
      - CAPTCHA (abort)
      - manual Retry (up to MAX_MANUAL_TRIES)
      - stability count
    Returns (new_h, new_stable, new_manual, should_break)
    """
    # scroll
    driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
    time.sleep(2)

    # captcha?
    if _detect_captcha(driver, context):
        return last_h, stable, manual_count, True

    # manual retry?
    if driver.find_elements(By.XPATH, RETRY_DIV_XPATH):
        manual_count += 1
        if manual_count > MAX_MANUAL_TRIES:
            logger.info("%s: exceeded manual retries", context)
            return last_h, stable, manual_count, True
        logger.warning("%s: Retry pill seen (%d/%d); click and wait…",
                       context, manual_count, MAX_MANUAL_TRIES)
        prev = driver.execute_script(SCROLL_JS)
        WebDriverWait(driver, 60).until(lambda d, p=prev: d.execute_script(SCROLL_JS) > p)
        return driver.execute_script(SCROLL_JS), 0, manual_count, False

    # normal height check
    new_h = driver.execute_script(SCROLL_JS)
    logger.debug("%s: scrolled new=%d last=%d", context, new_h, last_h)
    if new_h == last_h:
        stable += 1
        if stable >= 2:
            logger.info("%s: no new content; stopping", context)
            return new_h, stable, manual_count, True
    else:
        stable = 0
    return new_h, stable, manual_count, False

def _scroll_until_stable(driver, collect_fn, context):
    """
    Drive an indefinite scroll/collect loop until content stops growing
    (or a captcha/retry terminal condition).
    """
    items     = []
    last_h    = driver.execute_script(SCROLL_JS)
    stable    = 0
    manual_ct = 0
    logger.debug("%s: start height=%d", context, last_h)

    while True:
        # collect new
        for rec in collect_fn():
            if rec not in items:
                items.append(rec)
        # advance & check
        last_h, stable, manual_ct, should_break = _advance_and_check(
            driver, context, last_h, stable, manual_ct
        )
        if should_break:
            break

    return items

def scrape_x(keyword: str, headless: bool=False):
    """Scrape all live-search tweets for `keyword`."""
    logger.info("Scraping X.com for '%s'", keyword)
    d = _init_driver(headless)
    try:
        _load_cookies("X_COOKIES_PATH", d, "https://x.com")
        url = f"https://x.com/search?q={keyword.replace(' ','%20')}&f=live"
        if not _safe_get(d, url):
            return []
        WebDriverWait(d, 30).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input[data-testid='SearchBox_Search_Input']"))
        )

        def collect_tweets():
            out = []
            for c in d.find_elements(By.XPATH, "//article[@data-testid='tweet']"):
                try:
                    txt = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                    usr = c.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                    dt  = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                    out.append({"content": txt, "username": usr, "date": dt})
                except WebDriverException:
                    continue
            return out

        tweets = _scroll_until_stable(d, collect_tweets, "X.com")
        logger.info("Collected %d tweets", len(tweets))
        return tweets

    except Exception as e:
        logger.exception("scrape_x error: %s", e)
        return []
    finally:
        d.quit()

def scrape_facebook(keyword: str, headless: bool=False):
    """Scrape all Facebook posts for `keyword`."""
    logger.info("Scraping Facebook for '%s'", keyword)
    d = _init_driver(headless)
    try:
        _load_cookies("FB_COOKIES_PATH", d, "https://facebook.com")
        url = f"https://www.facebook.com/search/posts/?q={keyword.replace(' ','%20')}"
        if not _safe_get(d, url):
            return []
        time.sleep(4)
        with suppress(TimeoutException, NoSuchElementException):
            tab = WebDriverWait(d, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Posts']"))
            )
            tab.click()
            time.sleep(2)

        def collect_posts():
            out = []
            for c in d.find_elements(By.XPATH, "//div[contains(@data-testid,'post_message')]"):
                try:
                    txt = c.text.split('\n',1)[0].strip()
                    ab  = c.find_element(By.TAG_NAME, 'abbr')
                    tm  = ab.get_attribute('data-utime') or ab.get_attribute('title') or ab.text
                    out.append({"post_text": txt, "post_time": tm})
                except (NoSuchElementException, IndexError):
                    continue
            return out

        posts = _scroll_until_stable(d, collect_posts, "Facebook")
        logger.info("Collected %d FB posts", len(posts))
        return posts

    except Exception as e:
        logger.exception("scrape_facebook error: %s", e)
        return []
    finally:
        d.quit()
