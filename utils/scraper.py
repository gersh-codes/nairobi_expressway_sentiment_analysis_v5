import os
import time
import pickle
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

def scrape_twitter(query, max_tweets=30, scrolls=3):
    tweets = []
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-blink-features=AutomationControlled")
    driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

    # Load cookies if available
    cookies_path = "cookies/x_cookies.pkl"
    driver.get("https://x.com")  # initial page load before setting cookies
    if os.path.exists(cookies_path):
        with open(cookies_path, "rb") as f:
            for cookie in pickle.load(f):
                driver.add_cookie(cookie)
    driver.get(f"https://x.com/search?q={query}&src=typed_query&f=live")

    for _ in range(scrolls):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

    tweets_elements = driver.find_elements(By.XPATH, '//div[@data-testid="tweetText"]')
    for element in tweets_elements[:max_tweets]:
        tweets.append({
            'content': element.text,
            'date': '',  # X.com doesn't expose timestamps easily without deeper scraping
            'username': ''
        })

    driver.quit()
    return tweets

def scrape_facebook(page_name, max_posts=20):
    from facebook_scraper import get_posts
    posts = []
    for post in get_posts(page_name, pages=5, cookies="cookies/fb_cookies.json"):
        if 'text' in post:
            posts.append({
                'text': post['text'],
                'time': post['time'].strftime('%Y-%m-%d %H:%M:%S') if post['time'] else '',
                'likes': post.get('likes', 0),
                'comments': post.get('comments', 0)
            })
        if len(posts) >= max_posts:
            break
    return posts