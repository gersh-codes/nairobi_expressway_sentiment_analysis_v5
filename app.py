import os
import sys
import datetime
import logging

from flask import Flask, request, jsonify, send_file
from pymongo import MongoClient, errors
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
import pandas as pd

# topic-modeling helpers
import matplotlib
matplotlib.use('Agg')   # non-interactive backend
import matplotlib.pyplot as plt  # type: ignore: ensure matplotlib is installed
from utils.topic_modeling import (
    run_topic_modeling_by_phase,
    plot_topic_barchart,
    plot_topic_wordcloud
)
from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment import analyze_sentiment
from utils.cleaning import clean_text, tokenize_and_lemmatize, geocode_location

# ─── Bootstrap & UTF-8 ────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()
import certifi

# Force both requests and urllib3 to use the correct Windows CA bundle
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()
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
topics_col = db['topics']
logger.info('Connected to MongoDB at %s', mongo_uri)

# ─── Project Phase Helpers ─────────────────────────────────
def _parse_date(env_key: str):
    """Parse ISO date from env or return None."""
    s = os.getenv(env_key, '')
    try:
        return datetime.datetime.fromisoformat(s).replace(
            tzinfo=datetime.timezone.utc
        )
    except Exception:
        return None

PROJECT_START = _parse_date('PROJECT_START_DATE')
PROJECT_END   = _parse_date('PROJECT_END_DATE')

def _project_phase(ts_iso: str) -> str:
    """Tag timestamp as before/during/after project window."""
    try:
        ts = datetime.datetime.fromisoformat(
            ts_iso.rstrip('Z')
        ).replace(tzinfo=datetime.timezone.utc)
        if PROJECT_START and ts < PROJECT_START:
            return 'before'
        if PROJECT_END and ts > PROJECT_END:
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
def _scrape_store(keywords: str):
    """Full pipeline: scrape → clean → topics by phase → visualize → sentiment → store."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    logger.info('[JOB] scrape %s @ %s', keywords, now)

    # 1) Scrape X.com posts
    x_raw = scrape_x(keywords, headless=not DEBUG)

    # 2) Clean and tag phase
    texts, phases = [], []
    for rec in x_raw:
        txt = clean_text(rec['content'])
        rec['clean'] = txt
        texts.append(txt)
        phases.append(_project_phase(rec['date']))

    # 3) Fit LDA separately by phase (during/after)
    lda_results = {}
    if any(txt.strip() for txt in texts):
        lda_results = run_topic_modeling_by_phase(
            texts,
            phases,
            num_topics=5,
            num_words=7,
            doc_topic_prior=0.1,
            topic_word_prior=0.01,
            display_rule='fixed'
        )
        # Persist human-friendly names and top keywords
        topics_col.replace_one(
            {'keywords': keywords, 'time': now},
            {
                'keywords': keywords,
                'time': now,
                'topics': {
                    phase: list(topics)  # SonarQube-friendly
                    for phase, (_, _, topics) in lda_results.items()
                }
            },
            upsert=True
        )

    # 4) Visualize & save charts per phase/topic
    os.makedirs('charts', exist_ok=True)
    for phase, (vec, lda_model, topics) in lda_results.items():
        for t in topics:
            pid = f"{phase}-{t['topic_id']}"
            # 4a) Bar chart
            plot_topic_barchart(pid, t['top_keywords'])
            plt.savefig(f"charts/{pid}_bar.png")
            # 4b) Word cloud
            plot_topic_wordcloud(pid, t['full_distribution'])
            plt.savefig(f"charts/{pid}_wc.png")

    # 5) Assign dominant topic & store records
    for rec, txt, phase in zip(x_raw, texts, phases):
        vec_lda = lda_results.get(phase)
        if vec_lda:
            vec, lda_model, topics = vec_lda
            dist = lda_model.transform(vec.transform([txt]))[0]
            dom = int(dist.argmax())
            top_kw = topics[dom]['top_keywords']
        else:
            dom, top_kw = None, []

        sent = analyze_sentiment(txt)
        record = {
            'tokens': tokenize_and_lemmatize(txt),
            'geo': geocode_location(rec['username']),
            'platform': 'x',
            'text': txt,
            'meta': {'username': rec['username'], 'date': rec['date']},
            'timestamp': now,
            'project_phase': phase,
            'keyword': keywords,
            'topic': dom,
            'topic_keywords': top_kw
        }
        sent.update(record)
        _save(sent)

    logger.info('Saved %d X posts', len(x_raw))

    # ─── Repeat for Facebook ───────────────────────────────
    fb_posts = scrape_facebook(keywords)
    seen = set()
    for p in fb_posts:
        key = (p['post_time'], p['post_text'][:30])
        if key in seen:
            continue
        seen.add(key)

        text = clean_text(p['post_text'])
        phase = _project_phase(p['post_time'])
        sent = analyze_sentiment(text)
        record = {
            'tokens': tokenize_and_lemmatize(text),
            'geo': geocode_location(p.get('page')),
            'platform': 'facebook',
            'text': text,
            'meta': {'post_time': p['post_time']},
            'timestamp': now,
            'project_phase': phase,
            'keyword': keywords,
        }
        sent.update(record)
        _save(sent)

    logger.info('[JOB] saved %d FB items', len(seen))

# ─── HTTP: /scrape ────────────────────────────────────────
@app.route('/scrape', methods=['POST'])
def scrape_api():
    data = request.get_json(force=True) or {}
    kws = data.get('keywords') if isinstance(data.get('keywords'), list) else [data.get('keyword')]
    kws = [str(k).strip() for k in kws if k]
    if not kws:
        return jsonify(error="Provide 'keyword' or non-empty list 'keywords'"), 400
    for kw in kws:
        _scrape_store(kw)
    return jsonify(message=f'Scraped {len(kws)} keyword(s)'), 200

# ─── HTTP: /export/<fmt> ──────────────────────────────────
@app.route('/export/<fmt>', methods=['GET'])
def export_data(fmt):
    """Export all stored docs as CSV or JSON."""
    recs = list(logs.find({}, {'_id': 0}))
    df = pd.DataFrame(recs)
    fname = f'export.{fmt}'
    if fmt == 'csv':
        df.to_csv(fname, index=False)
    else:
        df.to_json(fname, orient='records', force_ascii=False)
    return send_file(fname, as_attachment=True)

@app.route('/topics')
def list_topics():
    kws = request.args.getlist('keyword')
    rec = topics_col.find_one({'keywords': kws}, {'_id': 0})
    return jsonify(rec or {})

# ─── Scheduler ────────────────────────────────────────────
def _scheduled():
    for kw in logs.distinct('keyword'):
        _scrape_store(kw)

# Scheduler config to prevent overlaps and delays
executors = {
    'default': ThreadPoolExecutor(1)
}
job_defaults = {
    'coalesce': True,
    'max_instances': 1,
    'misfire_grace_time': 900
}
sched = BackgroundScheduler(executors=executors, job_defaults=job_defaults)

# Run _scheduled only once a day at 6 a.m.
sched.add_job(
    _scheduled,
    'cron',
    hour=6,
    minute=0,
    id='daily_job',
    replace_existing=True,
    max_instances=1,
    coalesce=True,
    misfire_grace_time=900
)
sched.start()

if __name__ == '__main__':
    logger.info('Starting Flask (debug=%s)', DEBUG)
    app.run(debug=DEBUG)
