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
from utils.topic_modeling import run_topic_modeling

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
topics_col=db['topics']
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

# ─── Scrape, Clean, Analyze & Store X.com ───────────────────────
def _scrape_store(keywords: str):
    """Full pipeline: scrape → clean → topics → sentiment → store (with dominant topic)."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info('[JOB] scrape %s @ %s', keywords, now)

    # 1) Scrape raw posts
    x_raw=scrape_x(keywords, headless=not DEBUG)
    
    # 2) Clean & tokenize, collect for topic model
    cleaned_texts=[]
    for rec in x_raw:
        txt=clean_text(rec['content'])
        cleaned_texts.append(txt)
        rec['clean']=txt
    # (similarly for fb_raw if you like)
    if not cleaned_texts or all(not txt.strip() for txt in cleaned_texts):
        # nothing to model
        defs = []
        vec = lda = None
    else:
        try:
            # 3) Run LDA on the cleaned corpus
            vec,lda,defs = run_topic_modeling(cleaned_texts, num_topics=5, num_words=7)
        except ValueError as e:
            # catch empty‑vocabulary errors
            logger.warning("Topic modeling skipped: %s", e)
            defs = []
            vec = lda = None
    # Persist the topic definitions for this run
    topics_col.replace_one({'keywords':keywords,'time':now},{
        'keywords':keywords,'time':now,'topics':defs
    }, upsert=True)

        # 4) Assign dominant topic & save X.com items
    #    if lda is None, we’ll force a single “no-topic” bucket
    if lda is not None:
        # compute document–topic distributions
        x_matrix = vec.transform(cleaned_texts)
        dists = lda.transform(x_matrix)
    else:
        # one dummy distribution per document
        dists = [[1.0]] * len(cleaned_texts)  # always pick topic 0 below

    seen = set()
    for i, rec in enumerate(x_raw):
        # pick the highest-probability topic
        dom = int(dists[i].argmax()) if lda is not None else None

        # dedupe by user/date/snippet
        key = (rec['username'], rec['date'], rec['clean'][:30])
        if key in seen:
            continue
        seen.add(key)

        # run sentiment
        sent = analyze_sentiment(rec['clean'])

        # build the document record
        record = {
            'tokens':           tokenize_and_lemmatize(rec['clean']),
            'geo':              geocode_location(rec['username']),
            'platform':         'x',
            'text':             rec['clean'],
            'meta':             {'username': rec['username'], 'date': rec['date']},
            'timestamp':        now,
            'project_phase':    _project_phase(rec['date']),
            'keyword':          keywords,
            'topic':            dom,
            'topic_keywords':   defs[dom]['keywords'] if (lda is not None and defs) else []
        }

        # merge in sentiment scores and persist
        sent.update(record)
        _save(sent)

    logger.info("Saved %d X posts", len(seen))


    # — Facebook —
    posts = scrape_facebook(keywords)
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
            'keyword': keywords,
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

@app.route('/topics')
def list_topics():
    kws=request.args.getlist('keyword')
    rec=topics_col.find_one({'keywords':kws},{'_id':0})
    return jsonify(rec or {})
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
