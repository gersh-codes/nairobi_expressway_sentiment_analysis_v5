# app.py

import os
import logging
from flask import Flask, request, jsonify
from flask import Response
from bson.json_util import dumps
from pymongo import MongoClient, errors
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# ─── Logging Setup ────────────────────────────────────────────────────────────
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(fmt)
logger.addHandler(ch)

# File handler
os.makedirs('logs', exist_ok=True)
fh = logging.FileHandler('logs/app.log')
fh.setLevel(logging.DEBUG)
fh.setFormatter(fmt)
logger.addHandler(fh)

# ─── Flask & MongoDB Setup ────────────────────────────────────────────────────
app = Flask(__name__)
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
app.config['ENV'] = FLASK_ENV
DEBUG_MODE = FLASK_ENV == 'development'

# MongoDB client
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
mongo_db = mongo_client['sentiment_db']      # fixed database
logs_collection = mongo_db['logs']           # fixed collection
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _validate_request(data):
    """
    Ensure request JSON contains 'keyword' and 'page'.
    Returns (keyword, page) or raises ValueError.
    """
    keyword = data.get('keyword')
    page    = data.get('page')
    if not keyword or not page:
        msg = "Missing required fields: 'keyword' and 'page'"
        logger.error(msg)
        raise ValueError(msg)
    return keyword, page

def _save_to_db(doc):
    """
    Attempt to insert a document into MongoDB and log result.
    """
    try:
        logs_collection.insert_one(doc)
        logger.info("Inserted document into MongoDB")
    except errors.PyMongoError as e:
        logger.error(f"Failed to insert into MongoDB: {e}", exc_info=True)

# Import scraper and sentiment after logger is configured
from utils.scraper import scrape_x, scrape_facebook
from utils.sentiment import analyze_sentiment

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def home():
    return "Nairobi Expressway Sentiment API", 200

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.get_json(silent=True) or {}
    try:
        keyword, page = _validate_request(data)
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    # --- Scrape and analyze X.com tweets ---
    logger.info(f"Scraping X.com for keyword: {keyword}")
    x_data = scrape_x(keyword) or []
    x_sentiments = []
    for tweet in x_data:
        # tweet is a dict {'content': ..., 'username': ..., 'date': ...}
        text = tweet.get('content', '')
        if not isinstance(text, str):
            logger.warning(f"Skipping non-string tweet content: {tweet!r}")
            continue
        sent = analyze_sentiment(text)
        sent.update(platform="x", text=text, meta=tweet)
        x_sentiments.append(sent)

    # --- Scrape and analyze Facebook posts ---
    logger.info(f"Scraping Facebook page: {page}")
    fb_data = scrape_facebook(page) or []
    fb_sentiments = []
    for post in fb_data:
        text = post.get('text', '')
        if not isinstance(text, str):
            logger.warning(f"Skipping non-string Facebook post text: {post!r}")
            continue
        sent = analyze_sentiment(text)
        sent.update(platform="facebook", text=text, meta=post)
        fb_sentiments.append(sent)

    # Build document
    result_doc = {
        "keyword": keyword,
        "page": page,
        "x_results": x_sentiments,
        "facebook_results": fb_sentiments
    }

    # Save to MongoDB
    _save_to_db(result_doc)

    # Serialize with bson.json_util.dumps, which handles ObjectId
    return Response(dumps(result_doc), mimetype='application/json'), 200

# ─── App Runner ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask app (debug={DEBUG_MODE})")
    app.run(debug=DEBUG_MODE)
