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

def _init_driver(headless: bool = True) -> webdriver.Chrome:
    opts = webdriver.ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    ssl_cert = os.getenv('SSL_CERT_FILE')
    if ssl_cert:
        opts.add_argument(f"--ssl-client-certificate={ssl_cert}")
        logger.debug("Configured SSL_CERT_FILE for WebDriver")
    return webdriver.Chrome(options=opts)

def _load_x_cookies(driver: webdriver.Chrome) -> None:
    """
    Load cookies for X.com session. Tries JSON first, then pickle.
    """
    path = os.getenv('X_COOKIES_PATH')
    if not path or not os.path.exists(path):
        logger.warning("No X.com cookies found to load.")
        return

    driver.get("https://x.com")

    cookies = []
    # Attempt JSON loader
    try:
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
            logger.debug(f"Loaded {len(cookies)} cookies from JSON")
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.debug(f"JSON cookie load failed: {e}")

    # Fallback to pickle loader if JSON failed
    if not cookies:
        try:
            with open(path, 'rb') as f:
                cookies = pickle.load(f)
                logger.debug(f"Loaded {len(cookies)} cookies from pickle")
        except pickle.UnpicklingError as e:
            logger.warning(f"Pickle cookie load failed: {e}", exc_info=True)
            return

    if not cookies:
        logger.warning("Cookies file was loaded but contained no usable cookies.")
        return

    for c in cookies:
        if 'sameSite' in c:
            c['sameSite'] = 'Strict'
        try:
            driver.add_cookie(c)
        except Exception as e:
            logger.debug(f"Failed to add cookie: {c} - {e}")
    driver.refresh()

def _parse_tweet(elem) -> dict | None:
    try:
        content = elem.find_element('xpath', './/div[@data-testid="tweetText"]').text.strip()
    except WebDriverException:
        return None

    tweet = {'content': content}
    try:
        tweet['username'] = elem.find_element('xpath', './/div[@data-testid="User-Name"]//span').text.strip()
    except WebDriverException:
        tweet['username'] = None
    try:
        tweet['date'] = elem.find_element('tag name', 'time').get_attribute('datetime')
    except WebDriverException:
        tweet['date'] = None
    return tweet

def scrape_x(keyword: str, max_results: int = 30, headless: bool = True) -> list[dict] | None:
    logger.info(f"Scraping X.com for keyword: {keyword}")
    driver = None
    try:
        driver = _init_driver(headless)
        _load_x_cookies(driver)
        driver.get(f"https://x.com/search?q={keyword}&src=typed_query&f=live")
        time.sleep(5)

        body = driver.find_element('tag name', 'body')
        body.send_keys(Keys.END)
        time.sleep(2)

        tweets: list[dict] = []
        last_height = driver.execute_script(SCROLL_JS)

        while len(tweets) < max_results:
            elems = driver.find_elements('xpath', "//article[@data-testid='tweet']")
            logger.debug(f"Found {len(elems)} tweet elements")
            for e in elems[len(tweets):max_results]:
                tweet = _parse_tweet(e)
                if tweet:
                    tweets.append(tweet)
                    logger.debug(f"Parsed tweet: {tweet}")
                if len(tweets) >= max_results:
                    break

            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(2)
            new_height = driver.execute_script(SCROLL_JS)
            if new_height == last_height:
                logger.debug("Reached bottom of page")
                break
            last_height = new_height

        logger.info(f"Collected {len(tweets)} tweets")
        return tweets

    except (WebDriverException, TimeoutException) as err:
        logger.error(f"Selenium error while scraping X.com: {err}", exc_info=True)
        return None
    except Exception as err:
        logger.error(f"Unexpected error in scrape_x: {err}", exc_info=True)
        return None
    finally:
        if driver:
            with suppress(Exception):
                driver.quit()

def scrape_facebook(page_name: str, max_posts: int = 20) -> list[dict] | None:
    logger.info(f"Scraping Facebook page: {page_name}")
    posts: list[dict] = []
    creds = {}
    email, pwd = os.getenv('FACEBOOK_EMAIL'), os.getenv('FACEBOOK_PASSWORD')
    if email and pwd:
        creds = {'email': email, 'password': pwd}
    else:
        logger.warning("No Facebook credentials provided. Scraper will run without login.")

    try:
        for p in get_posts(page_name, pages=5, **creds):
            text = p.get('text') or ""
            if text:
                posts.append({
                    'text': text,
                    'time': p.get('time').strftime('%Y-%m-%d %H:%M:%S') if p.get('time') else None,
                    'likes': p.get('likes', 0),
                    'comments': p.get('comments', 0)
                })
                logger.debug(f"Scraped FB post: {posts[-1]}")
                if len(posts) >= max_posts:
                    break
        logger.info(f"Collected {len(posts)} Facebook posts")
        return posts

    except Exception as err:
        logger.error(f"Error scraping Facebook: {err}", exc_info=True)
        return None
