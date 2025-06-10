import os
import time
import pickle
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def load_cookies(driver, cookies_path="cookies/x_cookies.pkl"):
    if os.path.exists(cookies_path):
        with open(cookies_path, "rb") as f:
            cookies = pickle.load(f)
        for cookie in cookies:
            # Selenium requires sameSite to be 'Strict' or 'Lax' if present
            if 'sameSite' in cookie:
                cookie['sameSite'] = 'Strict'
            try:
                driver.add_cookie(cookie)
            except Exception:
                # Sometimes cookies fail to load, ignore
                pass

def scrape_twitter(query, max_tweets=30, scrolls=5):
    tweets = []
    options = Options()
    options.add_argument("--headless=new")  # Change to --headless if issues
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    driver.get("https://x.com")  # initial load for setting cookies
    load_cookies(driver)
    driver.refresh()
    time.sleep(4)  # Let page settle after loading cookies

    search_url = f"https://x.com/search?q={query}&src=typed_query&f=live"
    driver.get(search_url)
    time.sleep(3)

    last_height = driver.execute_script("return document.body.scrollHeight")

    while len(tweets) < max_tweets:
        # Collect tweets on page
        tweet_articles = driver.find_elements(By.XPATH, '//article[@data-testid="tweet"]')
        for article in tweet_articles[len(tweets):max_tweets]:
            try:
                content_el = article.find_element(By.XPATH, './/div[@data-testid="tweetText"]')
                content = content_el.text.strip()

                username_el = article.find_element(By.XPATH, './/div[@dir="ltr"]/span')
                username = username_el.text.strip()

                time_el = article.find_element(By.TAG_NAME, 'time')
                date = time_el.get_attribute('datetime') if time_el else ''

                tweets.append({
                    'content': content,
                    'username': username,
                    'date': date
                })
            except Exception:
                # Skip if any element missing
                continue

        if len(tweets) >= max_tweets:
            break

        # Scroll to load more tweets
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

    driver.quit()
    return tweets[:max_tweets]

def scrape_facebook(page_name, max_posts=20):
    from facebook_scraper import get_posts

    posts = []
    # Note: Requires fb_cookies.json in cookies/ to access private pages if needed
    cookies_path = "cookies/fb_cookies.json"
    for post in get_posts(page_name, pages=5, cookies=cookies_path):
        if 'text' in post and post['text']:
            posts.append({
                'text': post['text'],
                'time': post['time'].strftime('%Y-%m-%d %H:%M:%S') if post['time'] else '',
                'likes': post.get('likes', 0),
                'comments': post.get('comments', 0)
            })
        if len(posts) >= max_posts:
            break
    return posts
