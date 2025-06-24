import os
import json
from textblob import TextBlob
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from transformers import pipeline

# ─── Setup ─────────────────────────────────────────────────
vader = SentimentIntensityAnalyzer()
multilingual_bert = pipeline(
    "sentiment-analysis",
    model="nlptown/bert-base-multilingual-uncased-sentiment"
)

# Load Kiswahili lexicon
LEX_PATH = os.path.join(os.path.dirname(__file__), 'lexicons', 'swahili_lexicon.json')
with open(LEX_PATH, encoding='utf-8') as f:
    sw_lex = json.load(f)

# ─── Helpers ───────────────────────────────────────────────
def is_swahili(text: str) -> bool:
    words = text.lower().split()
    if not words:
        return False
    sw_count = sum(1 for w in words if w in sw_lex)
    return (sw_count / len(words)) > 0.3

def swahili_lexicon_score(text: str) -> str:
    score = sum(sw_lex.get(w, 0) for w in text.lower().split())
    if score > 0:
        return 'positive'
    elif score < 0:
        return 'negative'
    return 'neutral'

# ─── Main API ──────────────────────────────────────────────
def analyze_sentiment(text: str) -> dict:
    tb = TextBlob(text).sentiment.polarity
    vd = vader.polarity_scores(text)
    bt = multilingual_bert(text)[0]
    sw = swahili_lexicon_score(text) if is_swahili(text) else None

    return {
        'text': text,
        'textblob_polarity': tb,
        'vader': vd,
        'bert_sentiment': bt,
        'swahili_sentiment': sw
    }
