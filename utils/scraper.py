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

SCROLL_JS          = "return document.body.scrollHeight"
MAX_MANUAL_RETRIES = 3
RETRY_XPATH        = "//button[contains(.,'Retry')]"
CAPTCHA_XPATH      = "//iframe[contains(@src,'captcha')]"
X_DOMAIN           = "https://x.com"
FB_DOMAIN          = "https://www.facebook.com"


def _init_driver(headless: bool):
    """Start Chrome with stealth options."""
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(60)
    # hide webdriver flag
    drv.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
    })
    return drv


def _load_cookies(env_key: str, drv, domain: str):
    """Load JSON or pickle cookies, inject, then refresh."""
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        logger.debug("No cookie file at %s", path)
        return

    with suppress(TimeoutException):
        drv.get(domain)

    cookies = []
    with suppress(Exception):
        import json, pickle
        if path.lower().endswith('.json'):
            with open(path, encoding='utf-8') as f:
                cookies = json.load(f)
        else:
            with open(path, 'rb') as f:
                cookies = pickle.load(f)

        host = domain.split('//', 1)[1].split('/', 1)[0]
        for c in cookies:
            c['domain'] = f".{host}"
            with suppress(Exception):
                drv.add_cookie(c)

    drv.refresh()
    logger.debug("Loaded %d cookies from %s", len(cookies), env_key)


def _safe_get(drv, url: str) -> bool:
    """Navigate, allowing manual Retry clicks on timeout."""
    for i in range(1, MAX_MANUAL_RETRIES + 1):
        try:
            drv.get(url)
            return True
        except TimeoutException:
            logger.warning("Timeout loading %s (%d/%d)", url, i, MAX_MANUAL_RETRIES)
            if drv.find_elements(By.XPATH, RETRY_XPATH):
                logger.info("➡️ Please click RETRY in browser…")
                prev = drv.execute_script(SCROLL_JS)
                WebDriverWait(drv, 60).until(
                    lambda d, p=prev: d.execute_script(SCROLL_JS) > p
                )
            time.sleep(1)
    logger.error("Failed to load %s after retries", url)
    return False


def _check_login_wall(drv) -> bool:
    """Return True if we’re still on a login/signin page."""
    u = drv.current_url.lower()
    if "login" in u or "signin" in u:
        logger.error("At login wall (%s); aborting", drv.current_url)
        return True
    return False


def _check_captcha(drv, ctx: str) -> bool:
    """Abort if CAPTCHA iframe is present."""
    if drv.find_elements(By.XPATH, CAPTCHA_XPATH):
        logger.error("%s: CAPTCHA detected – aborting", ctx)
        return True
    return False


def _handle_retry(drv, ctx: str, manual: int, last_h: int):
    """
    If Retry pill appears, wait for manual click up to limit.
    Returns (manual, new_last_h, did_wait, abort).
    """
    if not drv.find_elements(By.XPATH, RETRY_XPATH):
        return manual, last_h, False, False

    manual += 1
    if manual > MAX_MANUAL_RETRIES:
        logger.info("%s: manual retry limit reached – stopping", ctx)
        return manual, last_h, False, True

    logger.warning("%s: RETRY seen (%d/%d); waiting for new content…",
                   ctx, manual, MAX_MANUAL_RETRIES)
    prev = drv.execute_script(SCROLL_JS)
    WebDriverWait(drv, 60).until(
        lambda d, p=prev: d.execute_script(SCROLL_JS) > p
    )
    return manual, drv.execute_script(SCROLL_JS), True, False


def _advance_and_check(drv, ctx: str, last_h: int, stable: int, manual: int):
    """
    Scroll once and handle:
      - CAPTCHA (abort)
      - manual Retry (via _handle_retry)
      - stability count
    Returns (new_h, new_stable, new_manual, should_break).
    """
    drv.execute_script("window.scrollTo(0,document.body.scrollHeight);")
    time.sleep(2)

    if _check_captcha(drv, ctx):
        return last_h, stable, manual, True

    manual, new_h, waited, abort = _handle_retry(drv, ctx, manual, last_h)
    if abort:
        return new_h, stable, manual, True
    if waited:
        return new_h, 0, manual, False

    logger.debug("%s: scrolled new=%d last=%d", ctx, new_h, last_h)
    if new_h == last_h:
        stable += 1
        if stable >= 2:
            logger.info("%s: no new content – end scroll", ctx)
            return new_h, stable, manual, True
    else:
        stable = 0
    return new_h, stable, manual, False


def _scroll_collect(drv, collect_fn, ctx: str):
    """
    Generic scroll‑and‑collect:
      1) run collect_fn()
      2) advance via _advance_and_check
      3) dedupe + accumulate until stop
    """
    items = []
    last_h = drv.execute_script(SCROLL_JS)
    stable = manual = 0
    logger.debug("%s: start height=%d", ctx, last_h)

    while True:
        for rec in collect_fn():
            if rec not in items:
                items.append(rec)

        last_h, stable, manual, stop = _advance_and_check(drv, ctx, last_h, stable, manual)
        if stop:
            break

    return items


def _scrape_x_live(drv):
    """
    After landing on search page, click 'Top' then scroll‑collect tweets.
    """
    try:
        tab = WebDriverWait(drv, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//a[.//span[text()='Top']]"))
        )
        drv.execute_script("arguments[0].click();", tab)
        time.sleep(2)
    except TimeoutException:
        logger.debug("No Top tab found; proceeding with default")

    WebDriverWait(drv, 15).until(
        EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))
    )

    def collect_tweets():
        out = []
        for card in drv.find_elements(By.XPATH, "//article[@data-testid='tweet']"):
            try:
                text = card.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                user = card.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                date = card.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                out.append({"content": text, "username": user, "date": date})
            except WebDriverException:
                continue
        return out

    return _scroll_collect(drv, collect_tweets, "X.com")


def scrape_x(keyword: str, headless: bool=False):
    """
    Scrape live‐search tweets from the Top tab for `keyword`.
    """
    logger.info("Scraping X.com for '%s'", keyword)
    drv = _init_driver(headless)
    try:
        _load_cookies("X_COOKIES_PATH", drv, X_DOMAIN)
        url = f"{X_DOMAIN}/search?q={quote_plus(keyword)}&f=live"
        if not _safe_get(drv, url) or _check_login_wall(drv):
            return []
        tweets = _scrape_x_live(drv)
        logger.info("Collected %d tweets", len(tweets))
        return tweets

    except Exception as e:
        logger.exception("scrape_x error: %s", e)
        return []
    finally:
        drv.quit()


def scrape_facebook(keyword: str, headless: bool=False):
    """
    Scrape public FB posts for `keyword` via JS‐click on “Posts” then scroll.
    """
    logger.info("Scraping Facebook for '%s'", keyword)
    drv = _init_driver(headless)
    try:
        _load_cookies("FB_COOKIES_PATH", drv, FB_DOMAIN)
        url = f"{FB_DOMAIN}/search/posts/?q={quote_plus(keyword)}"
        if not _safe_get(drv, url) or _check_login_wall(drv):
            return []

        time.sleep(4)  # let page settle
        with suppress(Exception):
            tab = WebDriverWait(drv, 10).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Posts']"))
            )
            drv.execute_script("arguments[0].scrollIntoView();arguments[0].click();", tab)
            time.sleep(2)

        def collect_posts():
            out = []
            for card in drv.find_elements(By.XPATH, "//div[@role='article']"):
                try:
                    text = card.find_element(By.CSS_SELECTOR, "div[dir='auto']").text.strip()
                    abbr = card.find_element(By.TAG_NAME, "abbr")
                    when = (abbr.get_attribute("data‑utime")
                            or abbr.get_attribute("title")
                            or abbr.text)
                    out.append({"post_text": text, "post_time": when})
                except WebDriverException:
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
