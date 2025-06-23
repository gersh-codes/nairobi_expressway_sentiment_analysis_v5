import os
import sys
import datetime
import logging

from flask import Flask, request, jsonify
from bson.json_util import dumps
from pymongo import MongoClient, errors
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment import analyze_sentiment

# ─── Load environment and ensure UTF-8 output ────────────────────────────────
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# ─── Logger configuration ────────────────────────────────────────────────────
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Console handler
ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)

# File handler
os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log', encoding='utf-8')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask and MongoDB setup ────────────────────────────────────────────────
app = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client['sentiment_db']
collection = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Project timeline helpers ───────────────────────────────────────────────
def _parse_date(var):
    raw = os.getenv(var, '')
    try:
        return datetime.datetime.fromisoformat(raw).replace(
            tzinfo=datetime.timezone.utc)
    except Exception:
        return None

START = _parse_date('PROJECT_START_DATE')
END   = _parse_date('PROJECT_END_DATE')

def _project_phase(iso_ts: str) -> str:
    """Return 'before', 'during', or 'after' based on PROJECT_START/END."""
    try:
        ts = datetime.datetime.fromisoformat(iso_ts.rstrip('Z')).replace(
            tzinfo=datetime.timezone.utc)
    except Exception:
        return 'during'
    if START and ts < START:
        return 'before'
    if END and ts > END:
        return 'after'
    return 'during'

# ─── Database save helper ───────────────────────────────────────────────────
def _save(doc: dict):
    try:
        collection.insert_one(doc)
    except errors.PyMongoError:
        logger.exception("Failed to save document")

# ─── Core scrape→analyze→store flow ─────────────────────────────────────────
def _scrape_and_store(keyword: str):
    """Scrape X and Facebook, analyze sentiment, store results."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[JOB] scrape '{keyword}' @ {now}")

    # 1) X.com
    tweets = scrape_x(keyword, headless=not DEBUG)
    for t in tweets:
        phase = _project_phase(t.get('date', ''))
        sent = analyze_sentiment(t['content'])
        sent.update(
            platform='x',
            text=t['content'],
            meta=t,
            timestamp=now,
            project_phase=phase,
            keyword=keyword
        )
        _save(sent)
    logger.info(f"[JOB] saved {len(tweets)} X items")

    # 2) Facebook
    posts = scrape_facebook(keyword, max_posts=20)
    saved = 0
    for p in posts:
        phase = _project_phase(p.get('time', ''))
        for comment in p.get('comments', []):
            sent = analyze_sentiment(comment)
            sent.update(
                platform='facebook',
                text=comment,
                meta={'post_text': p['text'], 'page': p['page']},
                timestamp=now,
                project_phase=phase,
                keyword=keyword
            )
            _save(sent)
            saved += 1
    logger.info(f"[JOB] saved {saved} FB comments")

# ─── HTTP endpoint to trigger an immediate scrape ──────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_endpoint():
    data = request.get_json(force=True) or {}
    # support either "keyword" or list "keywords"
    kws = data.get('keywords') if isinstance(data.get('keywords'), list) else [data.get('keyword')]
    kws = [str(k).strip() for k in kws if k]
    if not kws:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400

    for kw in kws:
        _scrape_and_store(kw)
    return jsonify(message=f"Scraped {len(kws)} keyword(s): {kws}"), 200

# ─── Scheduler to re-scrape all stored keywords three times daily ───────────
def _scheduled():
    keywords = collection.distinct("keyword")
    logger.info(f"[SCHED] re-scraping {keywords}")
    for kw in keywords:
        _scrape_and_store(kw)

sched = BackgroundScheduler()
sched.add_job(_scheduled, 'cron', hour='6,12,18', minute=0,
              id='daily_scrape', replace_existing=True)
sched.start()
logger.info("Scheduler started @ 06,12,18 UTC")

# ─── Application entrypoint ────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask (debug={DEBUG})")
    app.run(debug=DEBUG)
