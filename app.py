import os
import sys
import datetime
import logging

from flask import Flask, request, jsonify
from pymongo import MongoClient, errors
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment import analyze_sentiment

# ─── Bootstrap & UTF-8 ────────────────────────────────────
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# ─── Logger Setup ─────────────────────────────────────────
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)

os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log', encoding='utf-8', errors='replace')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask & MongoDB Setup ───────────────────────────────
app   = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db     = client['sentiment_db']
logs   = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Project Phase Helpers ───────────────────────────────
def _parse_date(key: str):
    val = os.getenv(key, '')
    try:
        return datetime.datetime.fromisoformat(val).replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None

PROJECT_START = _parse_date('PROJECT_START_DATE')
PROJECT_END   = _parse_date('PROJECT_END_DATE')

def _project_phase(ts_iso: str) -> str:
    """Tag as before/during/after project window."""
    try:
        ts = datetime.datetime.fromisoformat(ts_iso.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
        if PROJECT_START and ts < PROJECT_START:
            return 'before'
        if PROJECT_END and ts > PROJECT_END:
            return 'after'
    except Exception:
        pass
    return 'during'

# ─── Persistence ─────────────────────────────────────────
def _save(doc: dict):
    try:
        logs.insert_one(doc)
    except errors.PyMongoError:
        logger.exception("DB insert failed")

# ─── Core Scrape + Store ─────────────────────────────────
def _scrape_store(keyword: str):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[JOB] scrape '{keyword}' @ {now}")

    # — X.com —
    tweets = scrape_x(keyword, headless=not DEBUG) or []
    logger.info(f"[JOB] X.com returned {len(tweets)} tweets")
    for t in tweets:
        phase = _project_phase(t.get('date',''))
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

    # — Facebook —
    posts = scrape_facebook(keyword, headless=not DEBUG) or []
    logger.info(f"[JOB] Facebook returned {len(posts)} posts")
    for p in posts:
        phase = _project_phase(p.get('post_time',''))
        sent = analyze_sentiment(p['post_text'])
        sent.update(
            platform='facebook',
            text=p['post_text'],
            meta={'post_time': p['post_time']},
            timestamp=now,
            project_phase=phase,
            keyword=keyword
        )
        _save(sent)

# ─── HTTP Endpoint ───────────────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_api():
    data = request.get_json(silent=True) or {}
    logger.debug(f"/scrape payload: {data!r}")

    kws = []
    if isinstance(data.get('keywords'), list):
        kws = [str(k) for k in data['keywords'] if k]
    elif data.get('keyword'):
        kws = [str(data['keyword'])]

    if not kws:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400

    for kw in kws:
        _scrape_store(kw)
    return jsonify(message=f"Scraped {len(kws)} keyword(s): {kws}"), 200

# ─── Scheduler ────────────────────────────────────────────
def _scheduled():
    kws = logs.distinct("keyword")
    logger.info(f"[SCHED] re-scraping stored keywords: {kws}")
    for kw in kws:
        _scrape_store(kw)

sched = BackgroundScheduler()
sched.add_job(_scheduled, 'cron', hour='6,12,18', minute=0, id='daily_scrape_job', replace_existing=True)
sched.start()
logger.info("Scheduler started @ 06,12,18 UTC")

# ─── Run ──────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask (debug={DEBUG})")
    app.run(debug=DEBUG)
