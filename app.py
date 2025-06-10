from flask import Flask, request, jsonify
from utils.scraper import scrape_twitter, scrape_facebook
from pymongo import MongoClient
from textblob import TextBlob
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

client = MongoClient("mongodb://localhost:27017/")
db = client.sentiment_db
logs_collection = db.logs

def analyze_sentiment(text):
    blob = TextBlob(text)
    polarity = blob.sentiment.polarity
    sentiment = (
        "positive" if polarity > 0 else
        "negative" if polarity < 0 else
        "neutral"
    )
    return {"polarity": polarity, "sentiment": sentiment}

@app.route('/')
def home():
    return "Nairobi Expressway Sentiment API"

@app.route('/scrape/twitter', methods=['POST'])
def twitter_scrape():
    data = request.get_json()
    query = data.get('query', 'Nairobi Expressway')
    tweets = scrape_twitter(query)

    results = []
    for tweet in tweets:
        sentiment = analyze_sentiment(tweet['content'])
        sentiment.update({
            "platform": "twitter",
            "text": tweet['content'],
            "meta": tweet
        })
        logs_collection.insert_one(sentiment)
        results.append(sentiment)

    return jsonify(results)

@app.route('/scrape/facebook', methods=['POST'])
def facebook_scrape():
    data = request.get_json()
    page = data.get('page', 'NairobiExpressway')  # Accept custom public page name
    posts = scrape_facebook(page)

    results = []
    for post in posts:
        sentiment = analyze_sentiment(post['text'])
        sentiment.update({
            "platform": "facebook",
            "text": post['text'],
            "meta": post
        })
        logs_collection.insert_one(sentiment)
        results.append(sentiment)

    return jsonify(results)

if __name__ == '__main__':
    print("Device set to use cpu")
    app.run(debug=True)
