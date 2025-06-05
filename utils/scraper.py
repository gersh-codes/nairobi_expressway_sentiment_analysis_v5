import snscrape.modules.twitter as sntwitter
import facebook_scraper

def scrape_twitter(query, max_tweets=100):
    tweets = []
    for i, tweet in enumerate(sntwitter.TwitterSearchScraper(query).get_items()):
        if i >= max_tweets:
            break
        tweets.append({
            'content': tweet.content,
            'date': tweet.date.strftime('%Y-%m-%d %H:%M:%S'),
            'username': tweet.user.username
        })
    return tweets

def scrape_facebook(page_name, max_posts=20):
    from facebook_scraper import get_posts
    posts = []
    for i, post in enumerate(get_posts(page_name, pages=1)):
        if i >= max_posts:
            break
        posts.append({
            'text': post['text'],
            'time': post['time'].strftime('%Y-%m-%d %H:%M:%S') if post['time'] else '',
            'likes': post.get('likes', 0),
            'comments': post.get('comments', 0)
        })
    return posts