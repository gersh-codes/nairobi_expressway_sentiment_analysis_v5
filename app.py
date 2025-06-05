from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import pandas as pd
from pymongo import MongoClient

from utils.scraper import scrape_twitter, scrape_facebook
from utils.sentiment import analyze_sentiment
from utils.topic_modeling import run_topic_modeling
from utils.qualitative import process_uploaded_file

UPLOAD_FOLDER = './uploads'
ALLOWED_EXTENSIONS = {'csv', 'xlsx'}

app = Flask(__name__)
CORS(app)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Ensure uploads directory exists
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# MongoDB setup
client = MongoClient("mongodb://localhost:27017/")
db = client["expressway_analysis"]
logs_collection = db["sentiment_logs"]

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    return jsonify({"message": "Nairobi Expressway Sentiment API running."})

@app.route('/scrape/twitter', methods=['POST'])
def twitter_scrape():
    data = request.get_json()
    query = data.get('query')
    tweets = scrape_twitter(query)
    return jsonify(tweets)

@app.route('/scrape/facebook', methods=['POST'])
def facebook_scrape():
    data = request.get_json()
    page = data.get('page')
    posts = scrape_facebook(page)
    return jsonify(posts)

@app.route('/sentiment', methods=['POST'])
def sentiment():
    data = request.get_json()
    texts = data.get('texts', [])
    stakeholder = data.get('stakeholder', 'unknown')

    results = []
    for text in texts:
        analysis = analyze_sentiment(text)
        analysis['stakeholder'] = stakeholder
        analysis['text'] = text
        logs_collection.insert_one(analysis)
        results.append(analysis)

    return jsonify(results)

@app.route('/topic-modeling', methods=['POST'])
def topic_modeling():
    data = request.get_json()
    texts = data.get('texts', [])
    topics = run_topic_modeling(texts)
    return jsonify(topics)

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)
        processed_data = process_uploaded_file(filepath)
        return jsonify(processed_data)
    else:
        return jsonify({'error': 'Invalid file type'}), 400

if __name__ == '__main__':
    app.run(debug=True, port=5000)
