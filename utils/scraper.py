import os
import time
import json
import logging
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from facebook_scraper import get_posts

logger = logging.getLogger('sentiment_logger')

# JS snippet constant for scrolling
SCROLL_JS = "return document.body.scrollHeight"

def _init_driver(headless: bool = True) -> webdriver.Chrome:
    """Initialize Chrome WebDriver with options."""
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
    """Load cookies for X.com session if path provided."""
    path = os.getenv('X_COOKIES_PATH')
    if not path or not os.path.exists(path):
        logger.debug("No X.com cookies to load")
        return

    try:
        with open(path, 'r', encoding='utf-8') as f:
            cookies = json.load(f)
        driver.get("https://x.com")
        for c in cookies:
            if 'sameSite' in c:
                c['sameSite'] = 'Strict'
            driver.add_cookie(c)
        driver.refresh()
        logger.debug(f"Loaded {len(cookies)} cookies for X.com")
    except (json.JSONDecodeError, OSError) as err:
        logger.warning(f"Failed to load X.com cookies: {err}", exc_info=True)

def _parse_tweet(elem) -> dict | None:
    """Extract content, username, and date from a tweet element."""
    try:
        content = elem.find_element('xpath', './/div[@data-testid="tweetText"]').text.strip()
    except WebDriverException:
        return None

    tweet = {'content': content}
    try:
        tweet['username'] = elem.find_element('xpath', './/div[@dir="ltr"]/span').text.strip()
    except WebDriverException:
        tweet['username'] = None

    try:
        tweet['date'] = elem.find_element('tag name', 'time').get_attribute('datetime')
    except WebDriverException:
        tweet['date'] = None

    return tweet

def scrape_x(keyword: str, max_results: int = 30, headless: bool = True) -> list[dict] | None:
    """
    Scrape X.com for tweets containing `keyword`.
    Returns list of tweet dicts or None on fatal error.
    """
    logger.info(f"Scraping X.com for keyword: {keyword}")
    try:
        driver = _init_driver(headless)
        _load_x_cookies(driver)
        driver.get(f"https://x.com/search?q={keyword}&src=typed_query&f=live")
        time.sleep(3)

        tweets: list[dict] = []
        last_height = driver.execute_script(SCROLL_JS)

        while len(tweets) < max_results:
            elems = driver.find_elements('xpath', '//article')
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

        driver.quit()
        logger.info(f"Collected {len(tweets)} tweets")
        return tweets

    except (WebDriverException, TimeoutException) as err:
        logger.error(f"Selenium error while scraping X.com: {err}", exc_info=True)
        try:
            driver.quit()
        except Exception:
            pass
        return None
    except Exception as err:
        logger.error(f"Unexpected error in scrape_x: {err}", exc_info=True)
        return None

def scrape_facebook(page_name: str, max_posts: int = 20) -> list[dict] | None:
    """
    Scrape public posts from a Facebook page using facebook_scraper.
    Returns list of post dicts or None on error.
    """
    logger.info(f"Scraping Facebook page: {page_name}")
    posts: list[dict] = []
    # Optional credentials
    creds = {}
    email = os.getenv('FACEBOOK_EMAIL')
    pwd   = os.getenv('FACEBOOK_PASSWORD')
    if email and pwd:
        creds = {'email': email, 'password': pwd}

    try:
        for p in get_posts(page_name, pages=5, **creds):
            text = p.get('text') or ""
            if text:
                post_data = {
                    'text': text,
                    'time': p.get('time').strftime('%Y-%m-%d %H:%M:%S') if p.get('time') else None,
                    'likes': p.get('likes', 0),
                    'comments': p.get('comments', 0)
                }
                posts.append(post_data)
                logger.debug(f"Scraped FB post: {post_data}")
                if len(posts) >= max_posts:
                    break
        logger.info(f"Collected {len(posts)} Facebook posts")
        return posts

    except Exception as err:
        logger.error(f"Error scraping Facebook: {err}", exc_info=True)
        return None
