import os
import time
import datetime
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')

# Constants
SCROLL_JS        = "return document.body.scrollHeight"
CAPTCHA_IFRAME   = "//iframe[contains(@src,'captcha')]"
RETRY_BUTTON    = "//button[contains(.,'Retry')]"
WINDOW_DAYS     = 7  # X.com history pagination
MAX_MANUAL_WAIT = 3  # Manual Retry limit

def _init_driver(headless: bool):
    """Configure and return a Chrome WebDriver."""
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

def _load_cookies(env_key: str, drv, domain: str):
    """
    Load cookies from file and inject them into the WebDriver session
    to restore a logged-in state.
    """
    path = os.getenv(env_key, '')
    if not path or not os.path.exists(path):
        return

    drv.get(domain)
    with suppress(Exception):
        import json, pickle
        try:
            cookies = json.load(open(path, encoding='utf-8'))
        except Exception:
            cookies = pickle.load(open(path, 'rb'))
        for c in cookies:
            # force the domain to match
            c['domain'] = c.get('domain', '').lstrip('.') or ".twitter.com"
            with suppress(Exception):
                drv.add_cookie(c)
    drv.refresh()
    logger.debug('Loaded %d cookies from %s', len(cookies), env_key)

def _safe_get(drv, url: str) -> bool:
    """
    Attempt to navigate to `url`. If TimeoutException occurs,
    prompt user to click the RETRY button up to MAX_MANUAL_WAIT times.
    """
    for attempt in range(1, MAX_MANUAL_WAIT + 1):
        try:
            drv.get(url)
            return True
        except TimeoutException:
            logger.warning('Timeout loading %s (%d/%d)', url, attempt, MAX_MANUAL_WAIT)
            if drv.find_elements(By.XPATH, RETRY_BUTTON):
                logger.info('Please click RETRY in the browser to continue...')
                prev = drv.execute_script(SCROLL_JS)
                WebDriverWait(drv, 60).until(
                    lambda d, p=prev: d.execute_script(SCROLL_JS) > p
                )
            time.sleep(1)
    logger.error('Failed to load %s after manual retries', url)
    return False

def _scroll_collect(drv, collect_fn, context: str):
    """
    Generic scroll-collect loop that:
      1. calls `collect_fn` each pass
      2. scrolls until no new height twice in a row
      3. aborts on CAPTCHA iframe
      4. allows up to MAX_MANUAL_WAIT manual waits on RETRY
    """
    items = []
    last_h = drv.execute_script(SCROLL_JS)
    stable = manual = 0
    logger.debug('%s: start height=%d', context, last_h)

    while True:
        # collect new
        for rec in collect_fn():
            if rec not in items:
                items.append(rec)

        # scroll
        drv.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)

        # CAPTCHA?
        if drv.find_elements(By.XPATH, CAPTCHA_IFRAME):
            logger.error('%s: CAPTCHA detected—abort', context)
            break

        # detect manual retry prompt
        if drv.find_elements(By.XPATH, RETRY_BUTTON):
            manual += 1
            if manual > MAX_MANUAL_WAIT:
                logger.info('%s: manual retry limit reached—stop', context)
                break
            logger.warning('%s: retry button seen (%d/%d); waiting...', context, manual, MAX_MANUAL_WAIT)
            prev = drv.execute_script(SCROLL_JS)
            WebDriverWait(drv, 60).until(
                lambda d, p=prev: d.execute_script(SCROLL_JS) > p
            )
            last_h = drv.execute_script(SCROLL_JS)
            stable = 0
            continue

        # check height stability
        new_h = drv.execute_script(SCROLL_JS)
        logger.debug('%s: scrolled new=%d last=%d', context, new_h, last_h)
        if new_h == last_h:
            stable += 1
            if stable >= 2:
                logger.info('%s: no new content—end scroll', context)
                break
        else:
            last_h, stable = new_h, 0

    return items

def _collect_window(drv, keyword: str, since: str, until: str):
    """
    Scrape one date‑range window on X.com using the 'since:… until:…' syntax.
    """
    query = f"{keyword} since:{since} until:{until}"
    url = "https://twitter.com/search?q=" + query.replace(' ', '%20') + "&f=live"
    if not _safe_get(drv, url):
        return []

    # wait for at least one tweet card
    WebDriverWait(drv, 30).until(
        EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))
    )

    def collect_tweets():
        out = []
        for c in drv.find_elements(By.XPATH, "//article[@data-testid='tweet']"):
            try:
                txt = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                usr = c.find_element(By.XPATH, ".//span[contains(@class,'username')]").text.strip()
                dt  = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                out.append({"content": txt, "username": usr, "date": dt})
            except WebDriverException:
                continue
        return out

    return _scroll_collect(drv, collect_tweets, f"X {since}->{until}")

def scrape_x(keyword: str, headless: bool=False):
    """
    Scrape all tweets back to 2019 in WINDOW_DAYS chunks,
    dedupe and return the full list.
    """
    logger.info("Scraping X.com for '%s'", keyword)
    drv = _init_driver(headless)
    try:
        # restore session if cookies exist
        with suppress(Exception):
            _load_cookies("X_COOKIES_PATH", drv, "https://twitter.com")

        start = datetime.date(2019, 1, 1)
        end   = datetime.date.today()
        delta = datetime.timedelta(days=WINDOW_DAYS)
        all_tweets = []

        while start < end:
            stop = min(start + delta, end)
            window = _collect_window(drv, keyword, start.isoformat(), stop.isoformat())
            all_tweets.extend(window)
            start = stop

        # final dedupe
        unique, seen = [], set()
        for t in all_tweets:
            key = (t['username'], t['date'], t['content'][:30])
            if key not in seen:
                seen.add(key)
                unique.append(t)

        logger.info("Collected %d unique tweets", len(unique))
        return unique

    except Exception as e:
        logger.exception('scrape_x error: %s', e)
        return []
    finally:
        drv.quit()

def scrape_facebook(keyword: str, headless: bool=False):
    """
    Scrape Facebook public posts for keyword by indefinite scroll
    over <div role="article"> cards.
    """
    logger.info("Scraping Facebook for '%s'", keyword)
    drv = _init_driver(headless)
    try:
        with suppress(Exception):
            _load_cookies("FB_COOKIES_PATH", drv, "https://www.facebook.com")

        url = "https://www.facebook.com/search/posts/?q=" + keyword.replace(' ', '%20')
        if not _safe_get(drv, url):
            return []

        time.sleep(4)  # let feed render

        # filter to “Posts”
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
                except Exception:
                    continue
            return out

        posts = _scroll_collect(drv, collect_posts, "Facebook")
        logger.info("Collected %d FB posts", len(posts))
        return posts

    except Exception as e:
        logger.exception('scrape_facebook error: %s', e)
        return []
    finally:
        drv.quit()
