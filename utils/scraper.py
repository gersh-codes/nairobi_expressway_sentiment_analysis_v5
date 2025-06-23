import os, time, logging
from contextlib import suppress

from selenium import webdriver
from selenium.common.exceptions import (
    WebDriverException,
    TimeoutException,
    NoSuchElementException,
    ElementClickInterceptedException
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger = logging.getLogger('sentiment_logger')
SCROLL_SCRIPT = "return document.body.scrollHeight"

def _init_driver(headless: bool):
    opts = webdriver.ChromeOptions()
    # mimic a real user
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless:
        opts.add_argument("--headless=new")
    if ssl := os.getenv("SSL_CERT_FILE"):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(30)
    return drv

def _safe_get(driver, url, retries=2):
    """Load a page, retry on Timeout or a ‘Retry’ button."""
    for attempt in range(retries):
        try:
            driver.get(url)
            return True
        except TimeoutException:
            logger.warning(f"Timeout loading {url}, attempt {attempt+1}/{retries}")
            with suppress(Exception):
                # try clicking a Retry button if present
                btn = driver.find_element(By.XPATH, "//button[contains(text(),'Retry')]")
                btn.click()
            time.sleep(2)
    logger.error(f"Failed to load {url} after {retries} retries")
    return False

def scrape_x(keyword: str, headless: bool = False):
    """
    Scrape *all* tweets from X.com live search of `keyword`.
    Returns list of dicts {content, username, date}.
    """
    logger.info(f"Scraping X.com for '{keyword}'")
    driver = _init_driver(headless)
    url = f"https://x.com/search?q={keyword.replace(' ','%20')}&f=live"
    try:
        if not _safe_get(driver, url):
            return []
        # wait for at least one tweet
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))
        )

        tweets, last_h = [], driver.execute_script(SCROLL_SCRIPT)
        while True:
            cards = driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
            for c in cards:
                try:
                    txt = c.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text.strip()
                    usr = c.find_element(By.XPATH, ".//div[@dir='ltr']/span").text.strip()
                    dt  = c.find_element(By.TAG_NAME, "time").get_attribute("datetime")
                except WebDriverException:
                    continue
                rec = {"content": txt, "username": usr, "date": dt}
                if rec not in tweets:
                    tweets.append(rec)
                    logger.debug(f"→ tweet: {txt[:50]}…")
            # scroll
            driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
            time.sleep(2)
            h = driver.execute_script(SCROLL_SCRIPT)
            if h == last_h:
                break
            last_h = h

        logger.info(f"Collected {len(tweets)} tweets")
        return tweets

    except Exception:
        logger.exception("Unexpected error in scrape_x")
        return []
    finally:
        driver.quit()

def scrape_facebook(keyword: str, headless: bool = False):
    """
    Use Selenium to search Facebook posts for `keyword` and scrape their comments.
    Returns list of {post_text, post_time, comments: [...]}
    """
    logger.info(f"Scraping Facebook for '{keyword}'")
    driver = _init_driver(headless)
    search_url = f"https://www.facebook.com/search/posts/?q={keyword.replace(' ','%20')}"
    try:
        if not _safe_get(driver, search_url):
            return []
        time.sleep(4)  # allow dynamic content

        posts, last_h = [], driver.execute_script(SCROLL_SCRIPT)
        body = driver.find_element(By.TAG_NAME, 'body')

        while True:
            # scroll down
            body.send_keys(Keys.END)
            time.sleep(2)
            h = driver.execute_script(SCROLL_SCRIPT)
            if h == last_h:
                break
            last_h = h

            # find each post card
            cards = driver.find_elements(By.XPATH, "//div[contains(@data-testid,'post_message')]")
            for c in cards:
                try:
                    text = c.text.split('\n',1)[0].strip()
                    time_el = c.find_element(By.TAG_NAME, 'abbr')
                    post_time = time_el.get_attribute('title') or time_el.text
                except (NoSuchElementException, IndexError):
                    continue

                # click to expand comments
                comments = []
                with suppress(Exception):
                    btn = c.find_element(By.XPATH, ".//span[contains(text(),'Comment')]")
                    driver.execute_script("arguments[0].scrollIntoView()", btn)
                    btn.click()
                    time.sleep(1)
                    nodes = driver.find_elements(By.XPATH, "//div[@aria-label='Comment']//span[@dir='ltr']")
                    comments = [n.text.strip() for n in nodes if n.text.strip()]

                posts.append({
                    "post_text": text,
                    "post_time": post_time,
                    "comments": comments
                })
                logger.debug(f"→ fb post: {text[:50]}… ({len(comments)} comments)")

        logger.info(f"Collected {len(posts)} FB posts")
        return posts

    except Exception:
        logger.exception("Unexpected error in scrape_facebook")
        return []
    finally:
        driver.quit()
