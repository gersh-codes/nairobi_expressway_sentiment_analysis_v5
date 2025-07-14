from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation

def run_topic_modeling(texts, num_topics=5, num_words=5):
    """
    Fit an LDA model on `texts`, then extract:
      • vectorizer: to turn new docs into count vectors
      • lda: the fitted LDA model
      • topics: list of {topic, keywords} definitions
    """
    # 1) Build term counts
    vectorizer = CountVectorizer(stop_words='english')
    X = vectorizer.fit_transform(texts)

    # 2) Fit LDA
    lda = LatentDirichletAllocation(n_components=num_topics, random_state=42)
    lda.fit(X)

    # 3) Pull out the top words per topic
    words = vectorizer.get_feature_names_out()
    topics = []
    for idx, comp in enumerate(lda.components_):
        top_indices = comp.argsort()[:-num_words - 1:-1]
        keywords = [words[i] for i in top_indices]
        topics.append({'topic': idx, 'keywords': keywords})

    # 4) Return everything needed downstream
    return vectorizer, lda, topics
