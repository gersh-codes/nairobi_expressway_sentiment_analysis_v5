import os
import logging

from flask import Flask, request, jsonify, Response
from bson.json_util import dumps
from pymongo import MongoClient, errors
from dotenv import load_dotenv

# ─── Load & Logging ────────────────────────────────────────────────────────────
load_dotenv()
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)

os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask & MongoDB ───────────────────────────────────────────────────────────
app = Flask(__name__)
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
DEBUG_MODE = FLASK_ENV == 'development'

mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client['sentiment_db']
logs_collection = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _validate(data):
    kw = data.get('keyword')
    if not kw:
        raise ValueError("Missing required field: 'keyword'")
    return kw

def _save(doc):
    try:
        logs_collection.insert_one(doc)
        logger.info("Saved document to MongoDB")
    except errors.PyMongoError:
        logger.exception("Failed to save to MongoDB")

from utils.scraper import scrape_x, scrape_fb_search_comments
from utils.sentiment import analyze_sentiment

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def home():
    return "Nairobi Expressway Sentiment API", 200

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.get_json(silent=True) or {}
    try:
        keyword = _validate(data)
    except ValueError as e:
        logger.error(str(e))
        return jsonify({"error": str(e)}), 400

    # X.com
    x_raw = scrape_x(keyword) or []
    x_results = []
    for t in x_raw:
        txt = t.get('content','')
        if isinstance(txt, str):
            s = analyze_sentiment(txt)
            s.update(platform="x", text=txt, meta=t)
            x_results.append(s)

    # Facebook via search & comments
    fb_raw = scrape_fb_search_comments(keyword) or []
    fb_results = []
    for post in fb_raw:
        for com in post.get('comments', []):
            if isinstance(com, str):
                s = analyze_sentiment(com)
                s.update(platform="facebook", text=com, meta={"post_text": post["post_text"]})
                fb_results.append(s)

    result = {"keyword": keyword, "x_results": x_results, "facebook_results": fb_results}
    _save(result)
    return Response(dumps(result), mimetype='application/json'), 200

# ─── Runner ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask app (debug={DEBUG_MODE})")
    app.run(debug=DEBUG_MODE)
