import re, emoji, datetime
import warnings
from bs4 import MarkupResemblesLocatorWarning, BeautifulSoup
import nltk
from nltk.corpus import stopwords
from nltk.stem import WordNetLemmatizer
from geopy.geocoders import Nominatim

# ─── NLTK Setup ───────────────────────────────────────────────────────────
nltk.download('punkt'); nltk.download('stopwords'); nltk.download('wordnet'); nltk.download('punkt_tab')
STOP = set(stopwords.words('english'))
LEMM = WordNetLemmatizer()
GEO  = Nominatim(user_agent="sentiment_app", timeout=10)

def clean_text(text: str) -> str:
    """Strip HTML, URLs, mentions, emojis; normalize whitespace & case."""
    # Silence the annoying “looks like a URL” warning
    warnings.filterwarnings("ignore", category=MarkupResemblesLocatorWarning)
    # HTML → text
    text = BeautifulSoup(text, "html.parser").get_text()
    # URLs & mentions
    text = re.sub(r'(http\S+|www\S+)', '', text)
    text = re.sub(r'@\w+', '', text)
    text = text.replace('#','')
    # emojis
    text = emoji.replace_emoji(text, replace="")
    # whitespace & lowercase
    return re.sub(r'\s+',' ', text).strip().lower()

def tokenize_and_lemmatize(text: str) -> list:
    """Tokenize, remove stop-words, non-alpha, and lemmatize."""
    tokens = nltk.word_tokenize(text)
    toks = [t for t in tokens if t.isalpha() and t not in STOP]
    return [LEMM.lemmatize(t) for t in toks]

def geocode_location(loc: str):
    """Return {'lat','lon'} or None for a free-form location."""
    if not loc: return None
    try:
        res = GEO.geocode(loc)
        return {'latitude': res.latitude, 'longitude': res.longitude} if res else None
    except Exception:
        return None
