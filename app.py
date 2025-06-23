# app.py

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

# ─── Bootstrap ─────────────────────────────────────────────────────
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# ─── Logger ───────────────────────────────────────────────────────
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logging.INFO)
sh.setFormatter(fmt)
logger.addHandler(sh)

os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log', encoding='utf-8')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask & MongoDB ─────────────────────────────────────────────
app = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client['sentiment_db']
logs = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Project Phase Logic ─────────────────────────────────────────
def _parse_date(key: str):
    """Load ISO date from env, return UTC-aware datetime or None."""
    s = os.getenv(key, "")
    try:
        return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None

PROJECT_START = _parse_date('PROJECT_START_DATE')
PROJECT_END   = _parse_date('PROJECT_END_DATE')

def _project_phase(ts_iso: str) -> str:
    """Tag a timestamp as before/during/after project window."""
    try:
        ts = datetime.datetime.fromisoformat(ts_iso.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
        if PROJECT_START and ts < PROJECT_START:
            return 'before'
        if PROJECT_END   and ts > PROJECT_END:
            return 'after'
    except Exception:
        pass
    return 'during'

# ─── Persistence ──────────────────────────────────────────────────
def _save(doc: dict):
    """Insert into MongoDB, logging any failure."""
    try:
        logs.insert_one(doc)
    except errors.PyMongoError:
        logger.exception("DB insert failed")

# ─── Scrape + Store ─────────────────────────────────────────────
def _scrape_and_store(keyword: str):
    """Run X and Facebook scrapers, sentiment-analyze & timestamp each item."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[JOB] scrape '{keyword}' @ {now}")

    # X.com
    for t in (scrape_x(keyword, headless=not DEBUG) or []):
        sentiment = analyze_sentiment(t['content'])
        sentiment.update(
            platform='x',
            text=t['content'],
            meta=t,
            timestamp=now,
            project_phase=_project_phase(t.get('date','')),
            keyword=keyword
        )
        _save(sentiment)
    logger.info(f"[JOB] saved X items for '{keyword}'")

    # Facebook
    fb_posts = scrape_facebook(keyword, max_posts=100) or []
    saved = 0
    for p in fb_posts:
        phase = _project_phase(p.get('time',''))
        for c in (p.get('comments') or []):
            sentiment = analyze_sentiment(c)
            sentiment.update(
                platform='facebook',
                text=c,
                meta={'page': p['page'], 'post_text': p['text']},
                timestamp=now,
                project_phase=phase,
                keyword=keyword
            )
            _save(sentiment)
            saved += 1
    logger.info(f"[JOB] saved {saved} FB comments for '{keyword}'")

# ─── HTTP Endpoint ──────────────────────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_endpoint():
    """
    Accepts JSON payload:
      { "keyword": "foo" }
    or
      { "keywords": ["foo","bar"] }
    """
    data = request.get_json(force=True) or {}
    if 'keywords' in data and isinstance(data['keywords'], list):
        kws = [str(k) for k in data['keywords'] if k]
    elif 'keyword' in data:
        kws = [str(data['keyword'])]
    else:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400

    for kw in kws:
        _scrape_and_store(kw)
    return jsonify(message=f"Scraped {len(kws)} keyword(s)"), 200

# ─── Scheduler ────────────────────────────────────────────────────
def _scheduled():
    """Re-scrape every distinct keyword stored in DB."""
    kws = logs.distinct("keyword")
    logger.info(f"[SCHED] re-scraping: {kws}")
    for kw in kws:
        _scrape_and_store(kw)

sched = BackgroundScheduler()
sched.add_job(_scheduled, 'cron', hour='6,12,18', minute=0,
              id='daily_job', replace_existing=True)
sched.start()
logger.info("Scheduler started @ 06,12,18 UTC")

# ─── App Runner ──────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask (debug={DEBUG})")
    app.run(debug=DEBUG)
