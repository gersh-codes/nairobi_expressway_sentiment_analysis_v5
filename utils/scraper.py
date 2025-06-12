import os
import time
import json
import pickle
import logging
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.keys import Keys
from facebook_scraper import get_posts, search_pages
from contextlib import suppress

logger = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"


# ─── Helpers for X.com ─────────────────────────────────────────────────────────

def _init_driver(headless: bool) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    return webdriver.Chrome(options=opts)

def _load_x_cookies(driver):
    path = os.getenv('X_COOKIES_PATH')
    if not path or not os.path.exists(path):
        logger.warning("No X.com cookies to load")
        return
    driver.get("https://x.com")
    cookies = _try_load_cookies(path)
    if not cookies:
        return
    for c in cookies:
        if 'sameSite' in c:
            c['sameSite'] = 'Strict'
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded cookies into browser: {len(cookies)} items; sample: {driver.get_cookies()[:3]}")

def _try_load_cookies(path):
    # JSON attempt
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.debug("JSON cookie load failed: %s", e)
    # Pickle fallback
    try:
        with open(path, 'rb') as f:
            return pickle.load(f)
    except Exception as e:
        logger.warning("Pickle cookie load failed: %s", e, exc_info=True)
    logger.warning("No usable cookies found at %s", path)
    return None

def _scroll_and_collect(driver, max_results, parse_func):
    tweets = []
    last_height = driver.execute_script(SCROLL_JS)
    body = driver.find_element('tag name', 'body')
    # initial scroll
    body.send_keys(Keys.END)
    time.sleep(2)

    while len(tweets) < max_results:
        elems = driver.find_elements('xpath', "//article[@data-testid='tweet']")
        logger.debug("Found %d tweet elements", len(elems))
        for e in elems[len(tweets):max_results]:
            if t := parse_func(e):
                tweets.append(t)
                logger.debug("Parsed tweet content: %.50s", t['content'])
            if len(tweets) >= max_results:
                break

        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script(SCROLL_JS)
        if new_height == last_height:
            logger.debug("Reached bottom of page")
            break
        last_height = new_height

    return tweets

def _parse_tweet(elem):
    try:
        content = elem.find_element('xpath', ".//div[@data-testid='tweetText']").text.strip()
    except WebDriverException:
        return None
    tweet = {'content': content}
    for key, xpath in (('username', ".//div[@data-testid='User-Name']//span"),
                       ('date',    ".//time")):
        try:
            el = elem.find_element('xpath', xpath)
            tweet[key] = el.text.strip() if key=='username' else el.get_attribute('datetime')
        except WebDriverException:
            tweet[key] = None
    return tweet


# ─── Main scrape_x ─────────────────────────────────────────────────────────────

def scrape_x(keyword: str, max_results: int = 30, headless: bool = False):
    logger.info("Scraping X.com for keyword: %s", keyword)
    driver = None
    try:
        driver = _init_driver(headless)
        _load_x_cookies(driver)
        driver.get(f"https://x.com/search?q={keyword}&src=typed_query&f=live")
        time.sleep(5)

        results = _scroll_and_collect(driver, max_results, _parse_tweet)
        if not results:
            logger.warning("No tweets found for '%s'", keyword)
        else:
            logger.info("Collected %d tweets", len(results))
        return results

    except (WebDriverException, TimeoutException) as e:
        logger.error("Selenium error in scrape_x: %s", e, exc_info=True)
    except Exception as e:
        logger.error("Unexpected error in scrape_x: %s", e, exc_info=True)
    finally:
        if driver:
            with suppress(Exception):
                driver.quit()
    return None


# ─── Helpers for Facebook ──────────────────────────────────────────────────────

def _facebook_direct_search(keyword, max_posts, creds):
    posts = []
    for p in get_posts(keyword, pages=5, **creds):
        text = p.get('text') or ""
        if text:
            posts.append({
                'text': text,
                'time': p.get('time').strftime('%Y-%m-%d %H:%M:%S') if p.get('time') else None,
                'likes': p.get('likes', 0),
                'comments': p.get('comments', 0)
            })
        if len(posts) >= max_posts:
            break
    return posts

def _facebook_page_fallback(keyword, max_posts, creds):
    posts = []
    for page in search_pages(keyword):
        for p in get_posts(page['page_name'], pages=3, **creds):
            text = p.get('text') or ""
            if text:
                posts.append({
                    'text': text,
                    'time': p.get('time').strftime('%Y-%m-%d %H:%M:%S') if p.get('time') else None,
                    'likes': p.get('likes', 0),
                    'comments': p.get('comments', 0)
                })
            if len(posts) >= max_posts:
                return posts
    return posts


# ─── Main scrape_facebook ──────────────────────────────────────────────────────

def scrape_facebook(keyword: str, max_posts: int = 20):
    logger.info("Searching Facebook for keyword: %s", keyword)
    creds = {}
    if (e:=os.getenv('FACEBOOK_EMAIL')) and (p:=os.getenv('FACEBOOK_PASSWORD')):
        creds = {'email': e, 'password': p}
    else:
        logger.warning("No Facebook credentials provided; searching public posts only")

    # Attempt direct keyword-based posts
    posts = _facebook_direct_search(keyword, max_posts, creds)
    if posts:
        logger.info("Collected %d public posts via direct search", len(posts))
        return posts

    # Fallback to page-based search
    logger.warning("Direct search yielded 0 posts; falling back to page search")
    posts = _facebook_page_fallback(keyword, max_posts, creds)
    if posts:
        logger.info("Collected %d posts via page fallback", len(posts))
    else:
        logger.warning("No Facebook posts found via any method")
    return posts
