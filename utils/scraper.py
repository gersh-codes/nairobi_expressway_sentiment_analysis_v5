import os
import time
import datetime
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import (
    TimeoutException, WebDriverException, NoSuchElementException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')

SCROLL_JS         = "return document.body.scrollHeight"
RETRY_DIV_XPATH   = "//div[@role='button'][.//span[text()='Retry']]"
CAPTCHA_IFRAME    = "//iframe[contains(@src,'captcha')]"
MAX_MANUAL_TRIES  = 3
WINDOW_DAYS       = 7  # chunk size for historical paging

def _init_driver(headless: bool):
    """Initialize Chrome WebDriver."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(60)
    return drv

def _safe_get(drv, url):
    """
    Load a URL, prompting the user to click RETRY if a timeout and
    retry‑widget appear.
    """
    for attempt in range(3):
        try:
            drv.get(url)
            return True
        except TimeoutException:
            logger.warning("Timeout loading %s (%d/3)", url, attempt+1)
            if drv.find_elements(By.XPATH, RETRY_DIV_XPATH):
                logger.info("Please click RETRY in the browser…")
                prev = drv.execute_script(SCROLL_JS)
                WebDriverWait(drv, 60).until(
                    lambda d, p=prev: d.execute_script(SCROLL_JS) > p
                )
            time.sleep(1)
    logger.error("Failed to load %s after retries", url)
    return False

def _detect_captcha(drv, ctx):
    """Abort if a CAPTCHA iframe is present."""
    if drv.find_elements(By.XPATH, CAPTCHA_IFRAME):
        logger.error("%s: CAPTCHA detected; aborting", ctx)
        return True
    return False

def _scroll_collect(drv, collect_fn, ctx):
    """
    Scroll loop that:
     - calls collect_fn() each pass
     - stops when height stalls twice
     - aborts on CAPTCHA
     - allows up to MAX_MANUAL_TRIES manual RETRY waits
    """
    collected, last_h = [], drv.execute_script(SCROLL_JS)
    stable = manual = 0
    logger.debug("%s: start height=%d", ctx, last_h)

    while True:
        # 1) gather new items
        for item in collect_fn():
            if item not in collected:
                collected.append(item)

        # 2) scroll down
        drv.execute_script("window.scrollTo(0,document.body.scrollHeight);")
        time.sleep(2)

        # 3) check for CAPTCHA
        if _detect_captcha(drv, ctx):
            break

        # 4) manual RETRY prompt
        if drv.find_elements(By.XPATH, RETRY_DIV_XPATH):
            manual += 1
            if manual > MAX_MANUAL_TRIES:
                logger.info("%s: manual retry limit reached", ctx)
                break
            logger.warning("%s: RETRY seen (%d/%d); click & wait…",
                           ctx, manual, MAX_MANUAL_TRIES)
            prev = drv.execute_script(SCROLL_JS)
            WebDriverWait(drv, 60).until(
                lambda d, p=prev: d.execute_script(SCROLL_JS) > p
            )
            last_h = drv.execute_script(SCROLL_JS)
            stable = 0
            continue

        # 5) height stability check
        new_h = drv.execute_script(SCROLL_JS)
        logger.debug("%s: scrolled new=%d last=%d", ctx, new_h, last_h)
        if new_h == last_h:
            stable += 1
            if stable >= 2:
                logger.info("%s: no new content; stopping", ctx)
                break
        else:
            last_h, stable = new_h, 0

    return collected

def _collect_window(drv, keyword, since, until):
    """
    Scrape one date window [since, until) for X.com
    """
    # build query URL
    q = f"{keyword} since:{since} until:{until}"
    url = "https://x.com/search?q=" + q.replace(" ", "%20") + "&f=live"
    if not _safe_get(drv, url):
        return []

    # wait for the search box to confirm page load
    WebDriverWait(drv, 30).until(
        EC.presence_of_element_located(
            (By.CSS_SELECTOR, "input[data-testid='SearchBox_Search_Input']")
        )
    )

    def collect_tweets():
        out = []
        for card in drv.find_elements(By.XPATH, "//article[@data-testid='tweet']"):
            try:
                txt = card.find_element(
                    By.XPATH, ".//div[@data-testid='tweetText']"
                ).text.strip()
                usr = card.find_element(
                    By.XPATH, ".//div[@dir='ltr']/span"
                ).text.strip()
                dt  = card.find_element(By.TAG_NAME, "time")\
                         .get_attribute("datetime")
                out.append({"content": txt, "username": usr, "date": dt})
            except WebDriverException:
                continue
        return out

    return _scroll_collect(drv, collect_tweets, f"X {since}->{until}")

def scrape_x(keyword: str, headless: bool=False):
    """
    Scrape all tweets back to 2019 by paging in WINDOW_DAYS chunks.
    Deduplicates across windows by (user,date,preview).
    """
    logger.info("Scraping X.com for '%s'", keyword)
    drv = _init_driver(headless)
    try:
        # optional: load saved cookies
        with suppress(Exception):
            from utils.scraper import _load_cookies
            _load_cookies("X_COOKIES_PATH", drv, "https://x.com")

        start = datetime.date(2019,1,1)
        end   = datetime.date.today()
        delta = datetime.timedelta(days=WINDOW_DAYS)
        all_tweets = []

        # loop through date windows
        while start < end:
            stop = min(start + delta, end)
            window = _collect_window(drv, keyword, start.isoformat(), stop.isoformat())
            all_tweets.extend(window)
            start = stop

        # dedupe
        unique, seen = [], set()
        for t in all_tweets:
            key = (t['username'], t['date'], t['content'][:30])
            if key not in seen:
                seen.add(key)
                unique.append(t)

        logger.info("Collected %d unique tweets", len(unique))
        return unique

    except Exception as e:
        logger.exception("scrape_x error: %s", e)
        return []
    finally:
        drv.quit()

def scrape_facebook(keyword: str, headless: bool=False):
    """
    Scrape Facebook posts via indefinite scroll over <div role="article"> cards.
    """
    logger.info("Scraping Facebook for '%s'", keyword)
    drv = _init_driver(headless)
    try:
        with suppress(Exception):
            from utils.scraper import _load_cookies
            _load_cookies("FB_COOKIES_PATH", drv, "https://facebook.com")

        url = "https://www.facebook.com/search/posts/?q=" + keyword.replace(" ", "%20")
        if not _safe_get(drv, url):
            return []

        time.sleep(4)
        # click “Posts” filter if shown
        with suppress(TimeoutException, NoSuchElementException):
            tab = WebDriverWait(drv, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Posts']"))
            )
            tab.click()
            time.sleep(2)

        def collect_posts():
            out = []
            for art in drv.find_elements(By.XPATH, "//div[@role='article']"):
                try:
                    txt = art.find_element(By.XPATH, ".//div[@dir='auto']").text.strip()
                    ab  = art.find_element(By.TAG_NAME, 'abbr')
                    tm  = ab.get_attribute('data-utime') or ab.get_attribute('title') or ab.text
                    out.append({"post_text": txt, "post_time": tm})
                except (NoSuchElementException, IndexError):
                    continue
            return out

        posts = _scroll_collect(drv, collect_posts, "Facebook")
        logger.info("Collected %d FB posts", len(posts))
        return posts

    except Exception as e:
        logger.exception("scrape_facebook error: %s", e)
        return []
    finally:
        drv.quit()
