import os
import sys
import datetime
import logging

from flask import Flask, request, jsonify, Response
from bson.json_util import dumps
from pymongo import MongoClient, errors
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from utils.scraper import scrape_x, scrape_fb_search_comments
from utils.sentiment import analyze_sentiment

# ─── Load & UTF‑8 Setup ───────────────────────────────────────────────────────
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler(sys.stdout)
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)

os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log', encoding='utf-8', errors='replace')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask & DB ───────────────────────────────────────────────────────────────
app = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
app.config['ENV'] = 'development' if DEBUG else 'production'

mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client['sentiment_db']
logs_col = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Project timeline ─────────────────────────────────────────────────────────
def _parse_date(env_key):
    s = os.getenv(env_key, '')
    try:
        return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None

PROJECT_START = _parse_date('PROJECT_START_DATE')
PROJECT_END   = _parse_date('PROJECT_END_DATE')

def _project_phase(ts_iso: str) -> str:
    if not (PROJECT_START and PROJECT_END):
        return 'during'
    try:
        dt = datetime.datetime.fromisoformat(ts_iso.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return 'during'
    if dt < PROJECT_START:
        return 'before'
    if dt > PROJECT_END:
        return 'after'
    return 'during'

# ─── Storage Helper ───────────────────────────────────────────────────────────
def _save(doc: dict):
    try:
        logs_col.insert_one(doc)
    except errors.PyMongoError:
        logger.exception("DB insert failed")

# ─── Scrape + Store Logic ─────────────────────────────────────────────────────
def _scrape_and_store(keyword: str):
    # use timezone-aware now rather than utcnow()
    now_ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[JOB] scrape '{keyword}' @ {now_ts}")

    # X.com
    tweets = scrape_x(keyword, headless=not DEBUG) or []
    for t in tweets:
        phase = _project_phase(t.get('date',''))
        sent = analyze_sentiment(t['content'])
        sent.update(
            platform='x',
            text=t['content'],
            meta=t,
            timestamp=now_ts,
            project_phase=phase,
            keyword=keyword
        )
        _save(sent)
    logger.info(f"[JOB] saved {len(tweets)} X items")

    # Facebook
    posts = scrape_fb_search_comments(keyword, headless=not DEBUG) or []
    saved = 0
    for p in posts:
        phase = _project_phase(p.get('post_time',''))
        for c in p.get('comments', []):
            sent = analyze_sentiment(c)
            sent.update(
                platform='facebook',
                text=c,
                meta={'post_text': p['post_text']},
                timestamp=now_ts,
                project_phase=phase,
                keyword=keyword
            )
            _save(sent)
            saved += 1
    logger.info(f"[JOB] saved {saved} FB comments")

# ─── HTTP Endpoint ───────────────────────────────────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_endpoint():
    data = request.get_json(force=True) or {}

    # support either a single "keyword" or list "keywords"
    kw_single = data.get('keyword')
    kw_list   = data.get('keywords')

    if kw_list and isinstance(kw_list, list):
        keywords = [str(k) for k in kw_list if k]
    elif kw_single:
        keywords = [str(kw_single)]
    else:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400

    # run immediately for each
    for kw in keywords:
        _scrape_and_store(kw)

    return jsonify(message=f"Scraped {len(keywords)} keyword(s): {keywords}"), 200

# ─── Scheduler Setup ──────────────────────────────────────────────────────────
def _scheduled_scrape_all():
    kws = logs_col.distinct("keyword")
    logger.info(f"[SCHED] re‑scraping keywords: {kws}")
    for kw in kws:
        _scrape_and_store(kw)

scheduler = BackgroundScheduler()
scheduler.add_job(
    _scheduled_scrape_all,
    'cron',
    hour='6,12,18',
    minute=0,
    id='daily_scrape_job',
    replace_existing=True
)
scheduler.start()
logger.info("Scheduler started for stored keywords @ 06,12,18 UTC")

# ─── Run App ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask (debug={DEBUG})")
    app.run(debug=DEBUG)
