from textblob import TextBlob
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from transformers import pipeline
import json
import os

# Lexicon and VADER setup
vader = SentimentIntensityAnalyzer()
multilingual_bert = pipeline("sentiment-analysis", model="nlptown/bert-base-multilingual-uncased-sentiment")

# Load Kiswahili lexicon
SWAHILI_LEXICON_PATH = os.path.join(os.path.dirname(__file__), 'lexicons', 'swahili_lexicon.json')
with open(SWAHILI_LEXICON_PATH, 'r', encoding='utf-8') as f:
    swahili_lexicon = json.load(f)

def is_swahili(text):
    words = text.lower().split()
    sw_count = sum(1 for w in words if w in swahili_lexicon)
    return sw_count / len(words) > 0.3  # Heuristic threshold

def swahili_lexicon_score(text):
    words = text.lower().split()
    score = sum(swahili_lexicon.get(w, 0) for w in words)
    # extracted nested conditional
    if score > 0:
        label = 'positive'
    elif score < 0:
        label = 'negative'
    else:
        label = 'neutral'
    return label

def analyze_sentiment(text):
    textblob_polarity = TextBlob(text).sentiment.polarity
    vader_score = vader.polarity_scores(text)
    bert_label = multilingual_bert(text)[0]
    
    if is_swahili(text):
        swahili_sentiment = swahili_lexicon_score(text)
    else:
        swahili_sentiment = None

    return {
        'text': text,
        'textblob_polarity': textblob_polarity,
        'vader': vader_score,
        'bert_sentiment': bert_label,
        'swahili_sentiment': swahili_sentiment
    }