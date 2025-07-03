import os, time, logging
from contextlib import suppress
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

logger    = logging.getLogger('sentiment_logger')
SCROLL_JS = "return document.body.scrollHeight"

def _init_driver(headless):
    opts = webdriver.ChromeOptions()
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("window-size=1280,1024")
    if headless: opts.add_argument("--headless=new")
    if ssl := os.getenv('SSL_CERT_FILE'):
        opts.add_argument(f"--ssl-client-certificate={ssl}")
    drv = webdriver.Chrome(options=opts)
    drv.set_page_load_timeout(30)
    return drv

def _safe_get(driver, url, retries=3):
    for i in range(retries):
        try:
            driver.get(url); return True
        except TimeoutException:
            logger.warning(f"Timeout {i+1}/{retries} loading {url}")
            with suppress(Exception):
                btn=driver.find_element(By.XPATH,"//button[contains(text(),'Retry')]"); btn.click()
            time.sleep(2)
    logger.error(f"Cannot load {url}"); return False

def _load_cookies(key, driver, domain):
    path = os.getenv(key,"")
    if path and os.path.exists(path):
        driver.get(domain)
        with suppress(Exception):
            import json,pickle
            ck = None
            try: ck=json.load(open(path,encoding='utf-8'))
            except Exception: ck=pickle.load(open(path,'rb'))
            for c in ck or []:
                c.setdefault('sameSite','Lax')
                with suppress(Exception): driver.add_cookie(c)
        driver.refresh()

def scrape_x(keyword, headless=False):
    """Scrape all live-search tweets for a keyword."""
    drv = _init_driver(headless)
    try:
        _load_cookies("X_COOKIES_PATH", drv, "https://x.com")
        url=f"https://x.com/search?q={keyword.replace(' ','%20')}&f=live"
        if not _safe_get(drv,url): return []
        # wait for search input → avoids false‑positives
        WebDriverWait(drv,30).until(EC.presence_of_element_located((By.CSS_SELECTOR,"input[data-testid='SearchBox_Search_Input']")))
        tweets, last_h = [], drv.execute_script(SCROLL_JS)
        while True:
            cards = drv.find_elements(By.XPATH,"//article[@data-testid='tweet']")
            for c in cards:
                try:
                    txt = c.find_element(By.XPATH,".//div[@data-testid='tweetText']").text
                    usr = c.find_element(By.XPATH,".//div[@dir='ltr']/span").text
                    dt  = c.find_element(By.TAG_NAME,"time").get_attribute("datetime")
                except WebDriverException:
                    continue
                rec={"content":txt,"username":usr,"date":dt}
                if rec not in tweets:
                    tweets.append(rec); logger.debug("→ tweet: "+txt[:50])
            drv.execute_script("window.scrollTo(0,document.body.scrollHeight);"); time.sleep(2)
            h=drv.execute_script(SCROLL_JS)
            if h==last_h: break
            last_h=h
        logger.info(f"Collected {len(tweets)} tweets"); return tweets

    except Exception:
        logger.exception("Unexpected in scrape_x"); return []
    finally:
        drv.quit()

def scrape_facebook(keyword, headless=False):
    """Search Facebook posts for a keyword via Selenium."""
    drv = _init_driver(headless)
    try:
        _load_cookies("FB_COOKIES_PATH", drv, "https://facebook.com")
        url=f"https://www.facebook.com/search/posts/?q={keyword.replace(' ','%20')}"
        if not _safe_get(drv,url): return []
        time.sleep(4)
        # filter to “Posts”
        with suppress(Exception):
            tab=WebDriverWait(drv,10).until(EC.element_to_be_clickable((By.XPATH,"//span[text()='Posts']")))
            tab.click(); time.sleep(2)
        posts, last_h = [], drv.execute_script(SCROLL_JS)
        body = drv.find_element(By.TAG_NAME,'body')
        while True:
            body.send_keys(Keys.END); time.sleep(2)
            h=drv.execute_script(SCROLL_JS)
            if h==last_h: break
            last_h=h
            cards = drv.find_elements(By.XPATH,"//div[contains(@data-testid,'post_message')]")
            for c in cards:
                try:
                    txt = c.text.split('\n',1)[0]
                    ab = c.find_element(By.TAG_NAME,'abbr')
                    tm = ab.get_attribute('data-utime') or ab.get_attribute('title') or ab.text
                except Exception:
                    continue
                posts.append({"post_text":txt,"post_time":tm})
                logger.debug("→ fb post: "+txt[:50])
        logger.info(f"Collected {len(posts)} FB posts"); return posts

    except Exception:
        logger.exception("Unexpected in scrape_facebook"); return []
    finally:
        drv.quit()
