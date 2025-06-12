import os
import logging
from flask import Flask, request, jsonify, Response
from bson.json_util import dumps
from pymongo import MongoClient, errors
from dotenv import load_dotenv

# Load env vars
load_dotenv()

# ─── Logging Setup ────────────────────────────────────────────────────────────
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

# ─── Flask & MongoDB Setup ────────────────────────────────────────────────────
app = Flask(__name__)
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
app.config['ENV'] = FLASK_ENV
DEBUG_MODE = FLASK_ENV == 'development'

mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
mongo_db = mongo_client['sentiment_db']
logs_collection = mongo_db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _validate_request(data):
    keyword = data.get('keyword')
    if not keyword:
        msg = "Missing required field: 'keyword'"
        logger.error(msg)
        raise ValueError(msg)
    return keyword

def _save_to_db(doc):
    try:
        logs_collection.insert_one(doc)
        logger.info("Inserted document into MongoDB")
    except errors.PyMongoError as e:
        logger.error(f"Failed to insert into MongoDB: {e}", exc_info=True)

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
        keyword = _validate_request(data)
    except ValueError as err:
        return jsonify({"error": str(err)}), 400

    # --- X.com tweets ---
    logger.info(f"Scraping X.com for keyword: {keyword}")
    x_data = scrape_x(keyword) or []
    x_sentiments = []
    for tweet in x_data:
        text = tweet.get('content', '')
        if isinstance(text, str):
            sent = analyze_sentiment(text)
            sent.update(platform="x", text=text, meta=tweet)
            x_sentiments.append(sent)
        else:
            logger.warning(f"Skipping non-string tweet: {tweet!r}")

    # --- Facebook comments ---
    logger.info(f"Scraping Facebook for keyword: {keyword}")
    fb_data = scrape_facebook(keyword) or []
    fb_sentiments = []
    for post in fb_data:
        for comment in post.get('comments', []):
            if isinstance(comment, str):
                sent = analyze_sentiment(comment)
                sent.update(
                    platform="facebook",
                    text=comment,
                    meta={"page": post["page"], "post_text": post["post_text"]}
                )
                fb_sentiments.append(sent)
            else:
                logger.warning(f"Skipping non-string comment: {comment!r}")

    # Build and save result
    result_doc = {
        "keyword": keyword,
        "x_results": x_sentiments,
        "facebook_results": fb_sentiments
    }
    _save_to_db(result_doc)

    return Response(dumps(result_doc), mimetype='application/json'), 200

# ─── Runner ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask app (debug={DEBUG_MODE})")
    app.run(debug=DEBUG_MODE)
