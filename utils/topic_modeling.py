from sklearn.feature_extraction.text import CountVectorizer
from sklearn.decomposition import LatentDirichletAllocation
import matplotlib.pyplot as plt
from wordcloud import WordCloud


def run_topic_modeling(texts,
                       num_topics=5,
                       num_words=10,
                       doc_topic_prior=None,
                       topic_word_prior=None,
                       display_rule='fixed',
                       weight_threshold=0.01):
    """
    Fit LDA on `texts` with tunable priors, return:
      • vectorizer: CountVectorizer instance
      • lda: trained LDA model
      • topics: list of dicts per topic:
          - topic_id
          - name (human-friendly label)
          - top_keywords: list of (word, prob)
          - full_distribution: {word: prob}
    display_rule: 'fixed' (top N) or 'threshold' (all above threshold)
    """
    # 1) Convert texts to term-frequency matrix
    vectorizer = CountVectorizer(stop_words='english')
    X = vectorizer.fit_transform(texts)

    # 2) Configure and fit LDA with optional Dirichlet priors
    lda_kwargs = {'n_components': num_topics,
                  'random_state': 42,
                  'learning_method': 'batch'}
    if doc_topic_prior is not None:
        lda_kwargs['doc_topic_prior'] = doc_topic_prior
    if topic_word_prior is not None:
        lda_kwargs['topic_word_prior'] = topic_word_prior
    lda = LatentDirichletAllocation(**lda_kwargs)
    lda.fit(X)

    # 3) Compute full topic-word probability distributions
    #    Normalize each topic row to sum to 1
    comp = lda.components_
    topic_word_dist = comp / comp.sum(axis=1)[:, None]
    feature_names = vectorizer.get_feature_names_out()

    topics = []
    for topic_idx, dist in enumerate(topic_word_dist):
        # Build full distribution dict
        full_dist = {word: float(dist[i]) for i, word in enumerate(feature_names)}
        # Select keywords per display rule
        if display_rule == 'fixed':
            top_inds = dist.argsort()[:-num_words - 1:-1]
        else:
            top_inds = [i for i, v in enumerate(dist) if v >= weight_threshold]
        # Prepare top keywords list
        top_keywords = [(feature_names[i], float(dist[i])) for i in top_inds]
        # Create a simple human-friendly name by joining top 3 words
        name = '_'.join([w for w,_ in top_keywords[:3]])
        topics.append({
            'topic_id': topic_idx,
            'name': name,
            'top_keywords': top_keywords,
            'full_distribution': full_dist
        })
    return vectorizer, lda, topics


def run_topic_modeling_by_phase(texts, phases, **kwargs):
    """
    Fit separate LDA models for each unique phase label in `phases`.
    Returns dict: phase -> (vectorizer, lda, topics).
    """
    results = {}
    # Iterate over each phase (e.g. 'during', 'after')
    for phase in set(phases):
        # Filter texts belonging to this phase
        sub_texts = [t for t, p in zip(texts, phases) if p == phase]
        # Only fit if we have data
        if not sub_texts:
            continue
        vec, lda, topics = run_topic_modeling(sub_texts, **kwargs)
        results[phase] = (vec, lda, topics)
    return results


def plot_topic_barchart(topic_id, top_keywords):
    """
    Plot a horizontal bar chart for a single topic's top keywords.
    """
    words, weights = zip(*top_keywords)
    plt.figure()
    plt.barh(words, weights)
    plt.xlabel('Probability')
    plt.title(f'Topic {topic_id} Top Words')
    plt.gca().invert_yaxis()  # largest on top
    plt.tight_layout()
    plt.show()


def plot_topic_wordcloud(topic_id, full_distribution):
    """
    Generate and display a word cloud for a single topic.
    """
    wc = WordCloud(width=800, height=400, background_color='white')
    wc.generate_from_frequencies(full_distribution)
    plt.figure(figsize=(10, 5))
    plt.imshow(wc, interpolation='bilinear')
    plt.axis('off')
    plt.title(f'Topic {topic_id} Word Cloud')
    plt.tight_layout()
    plt.show()
