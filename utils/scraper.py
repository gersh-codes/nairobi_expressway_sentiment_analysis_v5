import os
import time
import logging
from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException
from facebook_scraper import get_posts

# Use the same logger (or create a new one for the scraper)
logger = logging.getLogger('sentiment_logger')

def scrape_x(keyword):
    """
    Scrape X.com (Twitter) for tweets containing the keyword.
    Uses Selenium; handles cookies and SSL if provided.
    """
    # Example: use Selenium with Chrome WebDriver
    try:
        options = webdriver.ChromeOptions()
        # If SSL_CERT_FILE is set, ensure the driver uses it (if needed)
        ssl_cert = os.getenv('SSL_CERT_FILE')
        if ssl_cert:
            options.add_argument(f"--ssl-client-certificate={ssl_cert}")
            logger.debug("Using SSL_CERT_FILE for requests")

        # If we have a path to cookies for X, load them (optional)
        cookies_path = os.getenv('X_COOKIES_PATH')
        driver = webdriver.Chrome(options=options)
        if cookies_path and os.path.exists(cookies_path):
            try:
                import json
                cookies = json.load(open(cookies_path))
                for cookie in cookies:
                    driver.add_cookie(cookie)
                logger.debug("Loaded cookies for X.com session")
            except Exception as e:
                logger.warning(f"Failed to load cookies: {e}")

        driver.get(f"https://x.com/search?q={keyword}")
        time.sleep(3)  # wait for page to load (adjust as needed)

        tweets = []
        # Example scraping logic: find tweet elements
        try:
            elements = driver.find_elements('xpath', "//article")  # sample XPath
            for elem in elements[:10]:  # limit to first 10 results
                text = elem.text
                tweets.append(text)
        except Exception as e:
            logger.error(f"Error parsing X.com page: {e}", exc_info=True)

        driver.quit()
        logger.info(f"Scraped {len(tweets)} tweets from X.com")
        return tweets

    except (WebDriverException, TimeoutException) as e:
        logger.error(f"Selenium failed for X.com: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error in scrape_x: {e}", exc_info=True)
        return None

def scrape_facebook(page_name):
    """
    Scrape Facebook public posts from a page using facebook_scraper.
    """
    try:
        posts = []
        # Optionally, use credentials if set
        email = os.getenv('FACEBOOK_EMAIL')
        password = os.getenv('FACEBOOK_PASSWORD')
        kwargs = {"email": email, "password": password} if email and password else {}
        # Fetch first page of posts
        for post in get_posts(page_name, pages=1, **kwargs):
            posts.append({
                "text": post.get('text'),
                "time": str(post.get('time')),
                "likes": post.get('likes'),
            })
        logger.info(f"Scraped {len(posts)} posts from Facebook page '{page_name}'")
        return posts
    except Exception as e:
        logger.error(f"facebook_scraper failed: {e}", exc_info=True)
        return None
