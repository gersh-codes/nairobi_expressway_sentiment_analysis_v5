import os
from flask import Flask, jsonify, request
from pymongo import MongoClient, errors
from dotenv import load_dotenv
import logging

# Load environment variables from .env file (works in development):contentReference[oaicite:10]{index=10}.
load_dotenv()

# Logging setup: file + console handlers:contentReference[oaicite:11]{index=11}:contentReference[oaicite:12]{index=12}.
logger = logging.getLogger('sentiment_logger')
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# File handler: debug logs to logs/app.log
os.makedirs('logs', exist_ok=True)  # ensure logs directory exists
file_handler = logging.FileHandler('logs/app.log')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Console handler: info+ logs to stdout
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

logger.info("Starting Flask app with logging enabled")

app = Flask(__name__)

# Configure Flask environment
FLASK_ENV = os.getenv('FLASK_ENV', 'production')
app.config['ENV'] = FLASK_ENV
logger.info(f"Flask ENV: {FLASK_ENV}")

# MongoDB setup using environment variable
mongo_uri = os.getenv('MONGODB_URI', 'mongodb://localhost:27017/')
try:
    mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
    mongo_db = mongo_client['sentiment_db']  # database name as required
    logger.info("Connected to MongoDB")
except Exception as e:
    logger.error(f"MongoDB connection failed: {e}", exc_info=True)
    mongo_client = None
    mongo_db = None

from utils.scraper import scrape_x, scrape_facebook  # our scraper module

@app.route('/scrape', methods=['POST'])
def scrape():
    """
    Endpoint to scrape sentiment data from X.com and Facebook.
    Expects JSON with 'keyword' for X and 'page' for Facebook.
    """
    data = request.get_json() or {}
    keyword = data.get('keyword', '')
    page = data.get('page', '')
    if not keyword or not page:
        msg = "Missing 'keyword' or 'page' parameter"
        logger.error(msg)
        return jsonify({"error": msg}), 400

    logger.info(f"Scraping X for keyword: {keyword}")
    try:
        x_data = scrape_x(keyword)
    except Exception as e:
        logger.error(f"X scraping encountered an error: {e}", exc_info=True)
        x_data = None

    logger.info(f"Scraping Facebook for page: {page}")
    try:
        fb_data = scrape_facebook(page)
    except Exception as e:
        logger.error(f"Facebook scraping encountered an error: {e}", exc_info=True)
        fb_data = None

    # Combine results
    result_doc = {
        "keyword": keyword,
        "page": page,
        "x_results": x_data,
        "facebook_results": fb_data
    }

    # Insert into MongoDB, if connected
    if mongo_db:
        try:
            mongo_db.logs.insert_one(result_doc)
            logger.info("Inserted scraped data into MongoDB")
        except errors.PyMongoError as e:
            logger.error(f"MongoDB insert failed: {e}", exc_info=True)

    return jsonify(result_doc), 200

if __name__ == "__main__":
    # Only enable debug if explicitly set to development
    debug_mode = (FLASK_ENV == 'development')
    logger.info(f"Running Flask app, debug={debug_mode}")
    app.run(debug=debug_mode)
    
