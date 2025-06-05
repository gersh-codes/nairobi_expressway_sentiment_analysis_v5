import pandas as pd
from rake_nltk import Rake

rake = Rake()

def process_uploaded_file(filepath):
    ext = filepath.split('.')[-1].lower()
    if ext == 'csv':
        df = pd.read_csv(filepath)
    else:
        df = pd.read_excel(filepath)
    
    results = []
    for _, row in df.iterrows():
        text = str(row.values[0])  # assuming the first column has the text
        rake.extract_keywords_from_text(text)
        keywords = rake.get_ranked_phrases()
        results.append({
            'text': text,
            'keywords': keywords[:5]  # Top 5 keywords
        })
    return results