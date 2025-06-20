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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"


def _init_driver(headless: bool):
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/115.0 Safari/537.36")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    driver = webdriver.Chrome(options=opts)
    driver.set_page_load_timeout(30)
    return driver


def _load_cookies(env_key: str, driver, base_url: str):
    path = os.getenv(env_key, "")
    if not path or not os.path.exists(path):
        logger.debug(f"No cookies for {env_key}")
        return
    driver.get(base_url)
    cookies = None
    # try JSON then pickle
    with suppress(Exception):
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
    if not isinstance(cookies, list):
        with suppress(Exception):
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
    if not cookies:
        logger.warning(f"Cookie file at {path} is empty or invalid")
        return
    for c in cookies:
        c.setdefault('sameSite', 'Strict')
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded {len(cookies)} cookies from {path}")


def _parse_tweet(el):
    try:
        text = el.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
    except WebDriverException:
        return None
    tw = {'content': text}
    for key, xp in (('username', ".//div[@dir='ltr']/span"), ('date', 'time')):
        try:
            node = el.find_element(By.XPATH, xp) if key == 'username' else el.find_element(By.TAG_NAME, xp)
            tw[key] = node.text.strip() if key == 'username' else node.get_attribute('datetime')
        except WebDriverException:
            tw[key] = None
    return tw


def scrape_x(keyword: str, headless: bool = False):
    """
    Scrape *all* tweets matching `keyword` from X.com’s live search.
    Returns list of tweet dicts: {'content', 'username', 'date'}.
    """
    logger.info(f"Scraping X.com for '{keyword}'")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies("X_COOKIES_PATH", driver, "https://x.com")
        url = f"https://x.com/search?q={keyword.replace(' ', '%20')}&src=typed_query&f=live"
        driver.get(url)

        # wait for first tweet
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))
        )

        seen = []
        last_h = driver.execute_script(SCROLL_JS)
        while True:
            cards = driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
            for c in cards:
                t = _parse_tweet(c)
                if t and t not in seen:
                    seen.append(t)
                    logger.debug(f"→ tweet: {t['content'][:50]}…")
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            h = driver.execute_script(SCROLL_JS)
            if h == last_h:
                break
            last_h = h

        logger.info(f"Collected {len(seen)} tweets")
        return seen

    except (WebDriverException, TimeoutException):
        logger.exception("Error scraping X.com")
    finally:
        if driver:
            driver.quit()
    return []


def _collect_comments(el, driver):
    with suppress(Exception):
        # expand comments
        btn = el.find_element(By.XPATH, ".//span[contains(text(),'Comment')]")
        driver.execute_script("arguments[0].scrollIntoView()", btn)
        btn.click()
        time.sleep(1)
        nodes = driver.find_elements(
            By.XPATH, "//div[@aria-label='Comment']//span[@dir='ltr']")
        return [n.text.strip() for n in nodes if n.text.strip()]
    return []


def scrape_fb_search_comments(keyword: str, headless: bool = False):
    """
    Search Facebook posts for `keyword` and collect all their comments.
    Returns list of {'post_text', 'comments':[ ... ]}.
    """
    logger.info(f"Scraping Facebook search for '{keyword}'")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies("FB_COOKIES_PATH", driver, "https://www.facebook.com")
        q = keyword.replace(' ', '%20')
        driver.get(f"https://www.facebook.com/search/posts/?q={q}")
        time.sleep(3)

        posts = []
        last_h = driver.execute_script(SCROLL_JS)
        body = driver.find_element(By.TAG_NAME, 'body')
        while True:
            body.send_keys(Keys.END)
            time.sleep(2)
            h = driver.execute_script(SCROLL_JS)
            if h == last_h:
                break
            last_h = h

            cards = driver.find_elements(By.XPATH, "//div[contains(@data-testid,'post_message')]")
            for c in cards:
                txt = c.text.split('\n', 1)[0].strip()
                cmts = _collect_comments(c, driver)
                posts.append({'post_text': txt, 'comments': cmts})
                logger.debug(f"→ fb post: {txt[:50]}… ({len(cmts)} comments)")

        logger.info(f"Collected {len(posts)} Facebook posts")
        return posts

    except Exception:
        logger.exception("Error scraping Facebook")
    finally:
        if driver:
            driver.quit()
    return []
