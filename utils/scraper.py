import os
import time
import json
import pickle
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys

logger = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"


def _init_driver(headless: bool = False):
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if (ssl := os.getenv('SSL_CERT_FILE')):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    return webdriver.Chrome(options=opts)


def _load_cookies(env_key: str, driver, base_url: str):
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        logger.debug(f"No cookies for {env_key} at {path}")
        return
    driver.get(base_url)
    cookies = None
    # try JSON
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    except Exception:
        # fallback pickle
        try:
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
        except Exception:
            logger.warning(f"Couldn’t load cookies from {path}", exc_info=True)
    if not cookies:
        return
    for c in cookies:
        c.setdefault('sameSite', 'Lax')
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded {len(cookies)} cookies from {path}")


def _parse_tweet(e):
    try:
        content = e.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text
    except WebDriverException:
        return None
    tweet = {'content': content.strip()}
    for key, sel in (('username', ".//div[@data-testid='User-Name']//span"),
                     ('date', "//time")):
        try:
            el = e.find_element(By.XPATH, sel) if key == 'username' else e.find_element(By.TAG_NAME, 'time')
            tweet[key] = el.text.strip() if key == 'username' else el.get_attribute('datetime')
        except WebDriverException:
            tweet[key] = None
    return tweet


def _scroll_collect(driver, max_n, xpath, parser):
    out, last_h = [], driver.execute_script(SCROLL_JS)
    body = driver.find_element(By.TAG_NAME, 'body')
    body.send_keys(Keys.END)
    time.sleep(2)
    while len(out) < max_n:
        elems = driver.find_elements(By.XPATH, xpath)
        for e in elems[len(out):max_n]:
            item = parser(e)
            if item:
                out.append(item)
                logger.debug(f"→ {item['content'][:50]}…")
            if len(out) >= max_n:
                break
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_h = driver.execute_script(SCROLL_JS)
        if new_h == last_h:
            break
        last_h = new_h
    return out


def scrape_x(keyword: str, max_results: int = 30, headless: bool = False):
    logger.info(f"Scraping X.com for '{keyword}'")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies("X_COOKIES_PATH", driver, "https://x.com")
        driver.get(f"https://x.com/search?q={keyword}&src=typed_query&f=live")
        time.sleep(5)
        tweets = _scroll_collect(driver, max_results, "//article[@data-testid='tweet']", _parse_tweet)
        logger.info(f"Collected {len(tweets)} tweets")
        return tweets
    except (WebDriverException, TimeoutException):
        logger.exception("X scrape failure")
    finally:
        if driver:
            driver.quit()
    return None


# ─── Facebook Search + Comments ──────────────────────────────────────────────

def _collect_comments(driver, post_elem, max_c: int):
    try:
        btn = post_elem.find_element(By.XPATH, ".//span[contains(text(),'Comment')]")
        driver.execute_script("arguments[0].scrollIntoView()", btn)
        btn.click()
        time.sleep(1)
        comments = driver.find_elements(By.XPATH, "//div[@aria-label='Comment']//span[@dir='ltr']")
        return [c.text for c in comments[:max_c] if c.text.strip()]
    except Exception:
        return []


def scrape_fb_search_comments(keyword: str,
                              max_posts: int = 10,
                              max_comments: int = 20,
                              headless: bool = False):
    logger.info(f"Scraping Facebook search for '{keyword}'")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies("FB_COOKIES_PATH", driver, "https://www.facebook.com")
        q = keyword.replace(' ', '%20')
        driver.get(f"https://www.facebook.com/search/posts/?q={q}")
        time.sleep(3)

        posts, last_h = [], driver.execute_script(SCROLL_JS)
        while len(posts) < max_posts:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_h = driver.execute_script(SCROLL_JS)
            if new_h == last_h:
                break
            last_h = new_h

            cards = driver.find_elements(By.XPATH, "//div[contains(@data-testid,'post_message')]")
            for c in cards[len(posts):max_posts]:
                txt = c.text.split('\n', 1)[0]
                cmts = _collect_comments(driver, c, max_comments)
                posts.append({"post_text": txt, "comments": cmts})
                if len(posts) >= max_posts:
                    break

        logger.info(f"Collected {len(posts)} FB posts")
        return posts
    except Exception:
        logger.exception("FB search failure")
    finally:
        if driver:
            driver.quit()
    return None
