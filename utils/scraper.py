import os
import time
import json
import pickle
import logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"


def _init_driver(headless: bool) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    return webdriver.Chrome(options=opts)


def _try_load_cookies(path):
    # try JSON
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        logger.debug("JSON cookie load failed")
    # try pickle
    try:
        with open(path, 'rb') as f:
            return pickle.load(f)
    except Exception:
        logger.warning("Pickle cookie load failed", exc_info=True)
    return None


def _load_cookies_for_site(driver, env_key, base_url):
    path = os.getenv(env_key)
    if not path or not os.path.exists(path):
        logger.warning(f"No cookies found for {base_url}")
        return
    driver.get(base_url)
    cookies = _try_load_cookies(path) or []
    for c in cookies:
        if 'sameSite' in c:
            c['sameSite'] = 'Strict'
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded {len(cookies)} cookies for {base_url}")


def _parse_tweet(elem):
    try:
        content = elem.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
    except WebDriverException:
        return None
    tweet = {'content': content}
    # username
    try:
        tweet['username'] = elem.find_element(
            By.XPATH, ".//div[@data-testid='User-Name']//span"
        ).text.strip()
    except WebDriverException:
        tweet['username'] = None
    # date
    try:
        tweet['date'] = elem.find_element(By.TAG_NAME, 'time').get_attribute('datetime')
    except WebDriverException:
        tweet['date'] = None
    return tweet


def _scroll_and_collect(driver, max_results, parse_func, xpath):
    results = []
    last_height = driver.execute_script(SCROLL_JS)
    body = driver.find_element(By.TAG_NAME, 'body')
    body.send_keys(Keys.END)
    time.sleep(2)

    while len(results) < max_results:
        elems = driver.find_elements(By.XPATH, xpath)
        logger.debug(f"Found {len(elems)} elements")
        for e in elems[len(results):max_results]:
            item = parse_func(e)
            if item:
                results.append(item)
                logger.debug(f"Collected item: {item!r}")
            if len(results) >= max_results:
                break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script(SCROLL_JS)
        if new_height == last_height:
            logger.debug("Reached bottom of page")
            break
        last_height = new_height

    return results


def scrape_x(keyword: str, max_results: int = 30, headless: bool = False):
    logger.info(f"Scraping X.com for keyword: {keyword}")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies_for_site(driver, "X_COOKIES_PATH", "https://x.com")
        driver.get(f"https://x.com/search?q={keyword}&src=typed_query&f=live")
        time.sleep(5)

        tweets = _scroll_and_collect(
            driver, max_results, _parse_tweet,
            "//article[@data-testid='tweet']"
        )
        logger.info(f"Collected {len(tweets)} tweets")
        return tweets

    except (WebDriverException, TimeoutException):
        logger.exception("Selenium error in scrape_x")
    except Exception:
        logger.exception("Unexpected error in scrape_x")
    finally:
        if driver:
            with suppress(Exception):
                driver.quit()
    return None


# ─── Facebook via search UI ────────────────────────────────────────────────────

def _collect_comments_for_post(driver, post_elem, max_comments):
    try:
        # scroll into view and click "Comment"
        btn = post_elem.find_element(By.XPATH, ".//span[contains(text(),'Comment')]")
        driver.execute_script("arguments[0].scrollIntoView()", btn)
        btn.click()
        WebDriverWait(driver, 5).until(
            EC.presence_of_all_elements_located((By.XPATH, "//div[@aria-label='Comment']"))
        )
        boxes = driver.find_elements(By.XPATH,
            "//div[@aria-label='Comment']//span[@dir='ltr']"
        )[:max_comments]
        return [b.text for b in boxes if b.text.strip()]
    except Exception:
        logger.debug("Failed to collect comments for one post", exc_info=True)
        return []


def scrape_fb_search_comments(
    keyword: str,
    max_posts: int = 10,
    max_comments_per_post: int = 20,
    headless: bool = False
):
    logger.info(f"Searching Facebook for keyword: {keyword}")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_cookies_for_site(driver, "FB_COOKIES_PATH", "https://www.facebook.com")
        query = keyword.replace(' ', '%20')
        driver.get(f"https://www.facebook.com/search/posts/?q={query}")
        time.sleep(3)

        posts_data = []
        last_h = driver.execute_script(SCROLL_JS)
        while len(posts_data) < max_posts:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_h = driver.execute_script(SCROLL_JS)
            if new_h == last_h:
                break
            last_h = new_h

            containers = driver.find_elements(
                By.XPATH, "//div[contains(@data-testid,'post_message')]"
            )[:max_posts]
            for ctn in containers:
                text = ctn.text.split('\n', 1)[0]
                comments = _collect_comments_for_post(driver, ctn, max_comments_per_post)
                posts_data.append({"post_text": text, "comments": comments})
                if len(posts_data) >= max_posts:
                    break

        logger.info(f"Collected {len(posts_data)} Facebook posts via search")
        return posts_data

    except Exception:
        logger.exception("Error in scrape_fb_search_comments")
    finally:
        if driver:
            with suppress(Exception):
                driver.quit()
    return None
