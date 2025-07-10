import os
import sys
import datetime
import logging

from flask import Flask, request, jsonify, send_file
from pymongo import MongoClient, errors
from apscheduler.schedulers.background import BackgroundScheduler
import pandas as pd

from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment import analyze_sentiment
from utils.cleaning import clean_text, tokenize_and_lemmatize, geocode_location

# ─── Bootstrap & UTF‑8 ────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()
sys.stdout.reconfigure(encoding='utf-8')

# ─── Logger Setup ─────────────────────────────────────────
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

sh = logging.StreamHandler(sys.stdout)
sh.setLevel(logging.DEBUG if os.getenv('FLASK_ENV') == 'development' else logging.INFO)
sh.setFormatter(fmt)
logger.addHandler(sh)

os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log', encoding='utf-8', errors='replace')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask & MongoDB Setup ─────────────────────────────────
app = Flask(__name__)
DEBUG = os.getenv('FLASK_ENV') == 'development'
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client['sentiment_db']
logs = db['logs']
logger.info('Connected to MongoDB at %s', mongo_uri)

# ─── Project Phase Helpers ─────────────────────────────────
def _parse_date(env_key: str):
    """Parse ISO date from env or return None."""
    s = os.getenv(env_key, '')
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

# ─── Persistence ───────────────────────────────────────────
def _save(doc: dict):
    """Insert a document into MongoDB, log on failure."""
    try:
        logs.insert_one(doc)
    except errors.PyMongoError:
        logger.exception('DB insert failed')

# ─── Scrape, Clean, Analyze & Store ───────────────────────
def _scrape_store(keyword: str):
    """Full pipeline: scrape → clean → analyze → store."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info('[JOB] scrape %s @ %s', keyword, now)

    # — X.com —
    tweets = scrape_x(keyword, headless=not DEBUG)
    seen = set()
    for t in tweets:
        key = (t['username'], t['date'], t['content'][:30])
        if key in seen:
            continue
        seen.add(key)

        text = clean_text(t['content'])
        sent = analyze_sentiment(text)
        sent['tokens'] = tokenize_and_lemmatize(text)
        sent['geo'] = geocode_location(t.get('username'))

        sent.update({
            'platform': 'x',
            'text': text,
            'meta': {'username': t['username'], 'date': t['date']},
            'timestamp': now,
            'project_phase': _project_phase(t['date']),
            'keyword': keyword,
        })
        _save(sent)

    logger.info('[JOB] saved %d X items', len(seen))

    # — Facebook —
    posts = scrape_facebook(keyword, headless=not DEBUG)
    seen = set()
    for p in posts:
        key = (p['post_time'], p['post_text'][:30])
        if key in seen:
            continue
        seen.add(key)

        text = clean_text(p['post_text'])
        sent = analyze_sentiment(text)
        sent['tokens'] = tokenize_and_lemmatize(text)
        sent['geo'] = geocode_location(p.get('page'))

        sent.update({
            'platform': 'facebook',
            'text': text,
            'meta': {'post_time': p['post_time']},
            'timestamp': now,
            'project_phase': _project_phase(p['post_time']),
            'keyword': keyword,
        })
        _save(sent)

    logger.info('[JOB] saved %d FB items', len(seen))

# ─── HTTP Endpoint: /scrape ───────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_api():
    data = request.get_json(force=True) or {}
    kws = data.get('keywords') if isinstance(data.get('keywords'), list) else [data.get('keyword')]
    kws = [str(k).strip() for k in kws if k]
    if not kws:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400

    for kw in kws:
        _scrape_store(kw)
    return jsonify(message='Scraped %d keyword(s)' % len(kws)), 200

# ─── HTTP Endpoint: /export/<fmt> ─────────────────────────
@app.route('/export/<fmt>', methods=['GET'])
def export_data(fmt):
    """Export all stored docs as CSV or JSON file."""
    recs = list(logs.find({}, {'_id': 0}))
    df = pd.DataFrame(recs)
    fname = 'export.%s' % fmt
    if fmt == 'csv':
        df.to_csv(fname, index=False)
    else:
        df.to_json(fname, orient='records', force_ascii=False)
    return send_file(fname, as_attachment=True)

# ─── Scheduler ────────────────────────────────────────────
def _scheduled():
    kws = logs.distinct('keyword')
    for kw in kws:
        _scrape_store(kw)

sched = BackgroundScheduler()
sched.add_job(_scheduled, 'cron', hour='6,12,18', minute=0,
              id='daily_job', replace_existing=True)
sched.start()

if __name__ == '__main__':
    logger.info('Starting Flask (debug=%s)', DEBUG)
    app.run(debug=DEBUG)
