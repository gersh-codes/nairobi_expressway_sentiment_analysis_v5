import os
import time
import json
import pickle
import logging
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from selenium.webdriver.common.keys import Keys
from facebook_scraper import get_posts
from contextlib import suppress

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
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        logger.debug("JSON cookie load failed")
    try:
        with open(path, 'rb') as f:
            return pickle.load(f)
    except Exception:
        logger.warning("Pickle cookie load failed", exc_info=True)
    return None


def _load_x_cookies(driver):
    path = os.getenv('X_COOKIES_PATH')
    if not path or not os.path.exists(path):
        logger.warning("No X.com cookies to load")
        return
    driver.get("https://x.com")
    cookies = _try_load_cookies(path) or []
    if not cookies:
        logger.warning("Cookies file empty or unreadable")
        return
    for c in cookies:
        if 'sameSite' in c:
            c['sameSite'] = 'Strict'
        with suppress(Exception):
            driver.add_cookie(c)
    driver.refresh()
    logger.debug(f"Loaded {len(cookies)} cookies; sample: {driver.get_cookies()[:3]}")


def _parse_tweet(elem):
    try:
        content = elem.find_element('xpath', ".//div[@data-testid='tweetText']").text.strip()
    except WebDriverException:
        return None

    tweet = {'content': content}
    for key, xp in (('username', ".//div[@data-testid='User-Name']//span"),
                    ('date',      ".//time")):
        try:
            el = elem.find_element('xpath', xp) if key == 'username' else elem.find_element('tag name', 'time')
            tweet[key] = el.text.strip() if key == 'username' else el.get_attribute('datetime')
        except WebDriverException:
            tweet[key] = None
    return tweet


def _scroll_and_collect(driver, max_results, parse_func):
    results = []
    last_height = driver.execute_script(SCROLL_JS)
    body = driver.find_element('tag name', 'body')
    body.send_keys(Keys.END)
    time.sleep(2)

    while len(results) < max_results:
        elems = driver.find_elements('xpath', "//article[@data-testid='tweet']")
        logger.debug(f"Found {len(elems)} tweet elements")
        for e in elems[len(results):max_results]:
            t = parse_func(e)
            if t:
                results.append(t)
                logger.debug(f"Parsed tweet: {t['content'][:50]}…")
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
        _load_x_cookies(driver)
        driver.get(f"https://x.com/search?q={keyword}&src=typed_query&f=live")
        time.sleep(5)

        tweets = _scroll_and_collect(driver, max_results, _parse_tweet)
        if not tweets:
            logger.warning(f"No tweets found for '{keyword}'")
        else:
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


# ─── Facebook Helpers ─────────────────────────────────────────────────────────

def _fetch_posts_from_page(page, keyword, max_posts, creds):
    """Fetch up to max_posts posts (with comments) containing keyword from one page."""
    collected = []
    try:
        for post in get_posts(page, pages=3, options={"comments": True}, **creds):
            text = post.get('text') or ""
            if keyword.lower() in text.lower():
                entry = {
                    'page': page,
                    'post_text': text,
                    'post_time': (
                        post.get('time').strftime('%Y-%m-%d %H:%M:%S')
                        if post.get('time') else None
                    ),
                    'comments': [
                        c.get('comment_text') for c in post.get('comments_full', [])
                        if c.get('comment_text')
                    ]
                }
                collected.append(entry)
                logger.debug(f"Collected post '{text[:50]}…' with {len(entry['comments'])} comments")
                if len(collected) >= max_posts:
                    break
    except Exception:
        logger.exception(f"Error fetching from Facebook page '{page}'")
    return collected


def scrape_facebook(keyword: str, max_posts: int = 20):
    """
    Scrape a list of known pages for posts containing the keyword,
    then collect their comments.
    """
    logger.info(f"Scraping Facebook for keyword: {keyword}")
    page_list = os.getenv('FB_PAGE_LIST', "KeNHA,Ma3Route,NTVKenya,NationAfrica").split(',')
    creds = {}
    if (email := os.getenv('FACEBOOK_EMAIL')) and (pwd := os.getenv('FACEBOOK_PASSWORD')):
        creds = {'email': email, 'password': pwd}
    else:
        logger.warning("No Facebook credentials; scraping only public posts")

    all_posts = []
    for page in page_list:
        posts = _fetch_posts_from_page(page, keyword, max_posts, creds)
        all_posts.extend(posts)
        if len(all_posts) >= max_posts:
            break

    if not all_posts:
        logger.warning(f"No Facebook posts/comments found for '{keyword}'")
    else:
        logger.info(f"Collected {len(all_posts)} Facebook posts with comments")
    return all_posts
