import os, sys, re, datetime, logging
from flask import Flask, request, jsonify, send_file
from pymongo import MongoClient, errors
from apscheduler.schedulers.background import BackgroundScheduler
from bson.json_util import dumps
import pandas as pd

from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment   import analyze_sentiment
from utils.cleaning    import clean_text, tokenize_and_lemmatize, geocode_location

# ─── Bootstrap & UTF‑8 ─────────────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# ─── Logger ─────────────────────────────────────────────────────────────
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logging.DEBUG if os.getenv('FLASK_ENV')=='development' else logging.INFO)
sh.setFormatter(fmt)
logger.addHandler(sh)
os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log', encoding='utf-8')
fh.setLevel(logging.DEBUG); fh.setFormatter(fmt); logger.addHandler(fh)

# ─── Flask & MongoDB ────────────────────────────────────────────────────
app = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
client = MongoClient(os.getenv('MONGODB_URI','mongodb://localhost:27017'), serverSelectionTimeoutMS=5000)
db   = client['sentiment_db']; logs = db['logs']
logger.info("Connected to MongoDB")

# ─── Project Phase Helpers ─────────────────────────────────────────────
def _parse_date(key):
    try:
        return datetime.datetime.fromisoformat(os.getenv(key)).replace(tzinfo=datetime.timezone.utc)
    except Exception: return None
PROJECT_START = _parse_date('PROJECT_START_DATE')
PROJECT_END   = _parse_date('PROJECT_END_DATE')
def _project_phase(ts):
    try:
        dt = datetime.datetime.fromisoformat(ts.rstrip('Z')).astimezone(datetime.timezone.utc)
        if PROJECT_START and dt < PROJECT_START: return 'before'
        if PROJECT_END   and dt > PROJECT_END:   return 'after'
    except Exception: pass
    return 'during'

# ─── Save Helper ────────────────────────────────────────────────────────
def _save(doc):
    try:
        logs.insert_one(doc)
    except errors.PyMongoError:
        logger.exception("DB insert failed")

# ─── Scrape, Clean, Analyze & Store ─────────────────────────────────────
def _scrape_store(keyword):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info(f"[JOB] scrape '{keyword}' @ {now}")

    # — X.com —
    raw_tweets = scrape_x(keyword, headless=not DEBUG)
    seen = set()
    for t in raw_tweets:
        # dedupe key
        key = (t['username'], t['date'])
        if key in seen: continue
        seen.add(key)
        # clean & normalize
        cleaned = clean_text(t['content'])
        # sentiment
        sent = analyze_sentiment(cleaned)
        # tokens
        sent['tokens'] = tokenize_and_lemmatize(cleaned)
        # geocode user location if any
        sent['geo'] = geocode_location(t.get('username'))
        # polymeta
        sent.update({
            'platform'      : 'x',
            'text'          : cleaned,
            'meta'          : {'username': t['username'], 'date': t['date']},
            'timestamp'     : now,
            'project_phase' : _project_phase(t['date']),
            'keyword'       : keyword
        })
        _save(sent)
    logger.info(f"[JOB] saved {len(seen)} X items")

    # — Facebook —
    raw_posts = scrape_facebook(keyword, headless=not DEBUG)
    seen = set()
    for p in raw_posts:
        key = p['post_time']
        if key in seen: continue
        seen.add(key)
        cleaned = clean_text(p['post_text'])
        sent = analyze_sentiment(cleaned)
        sent['tokens'] = tokenize_and_lemmatize(cleaned)
        sent['geo'] = geocode_location(p.get('page'))
        sent.update({
            'platform'      : 'facebook',
            'text'          : cleaned,
            'meta'          : {'post_time': p['post_time']},
            'timestamp'     : now,
            'project_phase' : _project_phase(p['post_time']),
            'keyword'       : keyword
        })
        _save(sent)
    logger.info(f"[JOB] saved {len(seen)} FB items")

# ─── API: Scrape Endpoint ────────────────────────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_api():
    data = request.get_json(force=True) or {}
    kws = data.get('keywords') if isinstance(data.get('keywords'), list) else [data.get('keyword')]
    kws = [str(k) for k in kws if k]
    if not kws:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400
    for kw in kws:
        _scrape_store(kw)
    return jsonify(message=f"Scraped {len(kws)} keyword(s)"), 200

# ─── API: Export Endpoint ───────────────────────────────────────────────
@app.route('/export/<fmt>', methods=['GET'])
def export_data(fmt):
    recs = list(logs.find({}, {'_id':0}))
    df = pd.DataFrame(recs)
    fname = f"export.{fmt}"
    if fmt=='csv':
        df.to_csv(fname, index=False)
    else:
        df.to_json(fname, orient='records')
    return send_file(fname, as_attachment=True)

# ─── Scheduler ─────────────────────────────────────────────────────────
def _scheduled():
    kws = logs.distinct("keyword")
    for kw in kws: _scrape_store(kw)

sched = BackgroundScheduler()
sched.add_job(_scheduled, 'cron', hour='6,12,18', minute=0, id='daily_job', replace_existing=True)
sched.start()

if __name__=="__main__":
    logger.info(f"Starting Flask (debug={DEBUG})")
    app.run(debug=DEBUG)
