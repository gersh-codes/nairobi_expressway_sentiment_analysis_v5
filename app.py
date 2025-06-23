import os, sys, datetime, logging
from flask import Flask, request, jsonify
from pymongo import MongoClient, errors
from bson.json_util import dumps
from dotenv import load_dotenv
from apscheduler.schedulers.background import BackgroundScheduler

from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment import analyze_sentiment

# ─── Bootstrap & UTF-8 ────────────────────────────────────────
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# ─── Logger ───────────────────────────────────────────────────
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

# ─── Flask & MongoDB ─────────────────────────────────────────
app   = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db     = client['sentiment_db']
logs   = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Project Phase Helpers ───────────────────────────────────
def _parse_date(key: str):
    s = os.getenv(key, '')
    try:
        return datetime.datetime.fromisoformat(s).replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None

PROJECT_START = _parse_date('PROJECT_START_DATE')
PROJECT_END   = _parse_date('PROJECT_END_DATE')

def _project_phase(ts: str) -> str:
    """Tag each item as before/during/after the project window."""
    try:
        dt = datetime.datetime.fromisoformat(ts.rstrip('Z')).replace(tzinfo=datetime.timezone.utc)
        if PROJECT_START and dt < PROJECT_START:
            return 'before'
        if PROJECT_END and dt > PROJECT_END:
            return 'after'
    except Exception:
        pass
    return 'during'

# ─── Persistence ─────────────────────────────────────────────
def _save(doc: dict):
    try:
        logs.insert_one(doc)
    except errors.PyMongoError:
        logger.exception("DB insert failed")

# ─── Scrape & Store ─────────────────────────────────────────
def _scrape_store(keyword: str):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[JOB] scrape '{keyword}' @ {now}")

    # X.com
    for t in scrape_x(keyword, headless=not DEBUG):
        sent = analyze_sentiment(t['content'])
        sent.update(
            platform='x',
            text=t['content'],
            meta=t,
            timestamp=now,
            project_phase=_project_phase(t.get('date','')),
            keyword=keyword
        )
        _save(sent)
    logger.info(f"[JOB] done X for '{keyword}'")

    # Facebook
    for p in scrape_facebook(keyword, headless=not DEBUG):
        phase = _project_phase(p.get('post_time',''))
        for c in p['comments']:
            sent = analyze_sentiment(c)
            sent.update(
                platform='facebook',
                text=c,
                meta={'post_text': p['post_text'], 'post_time': p['post_time']},
                timestamp=now,
                project_phase=phase,
                keyword=keyword
            )
            _save(sent)
    logger.info(f"[JOB] done FB for '{keyword}'")

# ─── API Endpoint ────────────────────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_api():
    data = request.get_json(force=True) or {}
    kws  = data.get('keywords') or []
    if not isinstance(kws, list):
        kws = [data.get('keyword')] if data.get('keyword') else []
    if not kws:
        return jsonify(error="Provide 'keyword' or non-empty 'keywords'"), 400

    for kw in kws:
        _scrape_store(str(kw))
    return jsonify(message=f"Scraped {len(kws)} keyword(s)"), 200

# ─── Scheduler ───────────────────────────────────────────────
def _scheduled():
    kws = logs.distinct("keyword")
    logger.info(f"[SCHED] re-scraping: {kws}")
    for kw in kws:
        _scrape_store(kw)

sched = BackgroundScheduler()
sched.add_job(_scheduled, 'cron', hour='6,12,18', minute=0,
              id='daily_job', replace_existing=True)
sched.start()
logger.info("Scheduler started @ 06,12,18 UTC")

# ─── Run ─────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask (debug={DEBUG})")
    app.run(debug=DEBUG)
