import os
import sys
import logging

from flask import Flask, request, jsonify, Response
from bson.json_util import dumps
from pymongo import MongoClient, errors
from dotenv import load_dotenv

# ─── Load & UTF-8 console ──────────────────────────────────────────────────────
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
DEBUG_MODE = os.getenv('FLASK_ENV') == 'development'
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
db = client['sentiment_db']
logs_col = db['logs']
logger.info(f"Connected to MongoDB at {mongo_uri}")

# ─── Helpers ──────────────────────────────────────────────────────────────────
def _validate(data):
    if not (kw := data.get('keyword')):
        raise ValueError("Missing required field: 'keyword'")
    return kw

def _save(doc):
    try:
        logs_col.insert_one(doc)
        logger.info("Saved to MongoDB")
    except errors.PyMongoError:
        logger.exception("DB insert failed")

from utils.scraper import scrape_x, scrape_fb_search_comments
from utils.sentiment import analyze_sentiment

# ─── Routes ───────────────────────────────────────────────────────────────────
@app.route('/', methods=['GET'])
def home():
    return "Nairobi Expressway Sentiment API", 200

@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.get_json(silent=True) or {}
    try:
        keyword = _validate(data)
    except ValueError as e:
        logger.error(e)
        return jsonify({"error": str(e)}), 400

    # X.com
    x_raw = scrape_x(keyword) or []
    x_res = []
    for t in x_raw:
        txt = t.get('content','')
        if isinstance(txt, str):
            s = analyze_sentiment(txt)
            s.update(platform="x", text=txt, meta=t)
            x_res.append(s)

    # Facebook
    fb_raw = scrape_fb_search_comments(keyword) or []
    fb_res = []
    for post in fb_raw:
        for c in post.get('comments', []):
            if isinstance(c, str):
                s = analyze_sentiment(c)
                s.update(platform="facebook", text=c,
                         meta={"post_text": post["post_text"]})
                fb_res.append(s)

    result = {"keyword": keyword,
              "x_results": x_res,
              "facebook_results": fb_res}
    _save(result)
    return Response(dumps(result), mimetype='application/json'), 200

# ─── App Runner ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info(f"Starting Flask (debug={DEBUG_MODE})")
    app.run(debug=DEBUG_MODE)
