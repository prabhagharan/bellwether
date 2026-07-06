from bellwether.discovery.connectors import youtube_feed_url, x_binding, domain_of, discover_feed_links


def test_youtube_feed_url():
    assert youtube_feed_url("UC123") == "https://www.youtube.com/feeds/videos.xml?channel_id=UC123"


def test_x_binding():
    assert x_binding("@jack") == ("x", {"handle": "jack"})
    assert x_binding("jack") == ("x", {"handle": "jack"})


def test_domain_of():
    assert domain_of("https://www.Example.com/path") == "example.com"
    assert domain_of("example.com") == "example.com"


def test_discover_feed_links():
    html = '''<html><head>
      <link rel="alternate" type="application/rss+xml" href="/feed.xml">
      <link rel="alternate" type="application/atom+xml" href="https://x.com/atom">
      <link rel="stylesheet" href="/s.css">
    </head></html>'''
    assert discover_feed_links(html) == ["/feed.xml", "https://x.com/atom"]
