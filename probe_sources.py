"""Probe candidate news sources for usability FROM THIS MACHINE.

A source that works from a home broadband connection may be blocked from a
datacentre IP, which is exactly what happened to News18: 5/5 extraction locally,
403 Forbidden on 130 of 167 articles from a GitHub Actions runner. Deliverability
of the source is therefore a property of where the code runs, not of the source
alone, and it has to be measured where the code will actually live.

    python probe_sources.py
"""

import time

import feedparser
import requests
import trafilatura

UA = "Mozilla/5.0 (compatible; MorningDigest/0.1; personal reader)"

CANDIDATES = {
    "the_hindu":       "https://www.thehindu.com/news/national/feeder/default.rss",
    "hindustan_times": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
    "news18":          "https://www.news18.com/commonfeeds/v1/eng/rss/india.xml",
    "times_of_india":  "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms",
    "india_today":     "https://www.indiatoday.in/rss/1206578",
    "indian_express":  "https://indianexpress.com/section/india/feed/",
    "the_print":       "https://theprint.in/feed/",
    "business_std":    "https://www.business-standard.com/rss/home_page_top_stories.rss",
    "livemint":        "https://www.livemint.com/rss/news",
    "economic_times":  "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
    "deccan_herald":   "https://www.deccanherald.com/rss/national.rss",
    "the_wire":        "https://thewire.in/rss",
}

SAMPLE = 6          # articles to try per source


def probe(name, url):
    feed = feedparser.parse(url, agent=UA)
    if not feed.entries:
        return {"name": name, "feed": 0, "ok": 0, "blocked": 0, "other": 0,
                "note": f"empty feed (http={getattr(feed, 'status', '?')})"}

    ok = blocked = other = 0
    words = []
    for entry in feed.entries[:SAMPLE]:
        link = (entry.get("link") or "").strip()
        if not link:
            continue
        try:
            resp = requests.get(link, headers={"User-Agent": UA}, timeout=25)
            resp.raise_for_status()
            text = trafilatura.extract(resp.text, include_comments=False,
                                       include_tables=False, favor_precision=True)
            if text and len(text.split()) >= 120:
                ok += 1
                words.append(len(text.split()))
            else:
                other += 1
        except requests.HTTPError as exc:
            code = exc.response.status_code if exc.response is not None else 0
            if code in (401, 403, 429, 451):
                blocked += 1
            else:
                other += 1
        except Exception:
            other += 1
        time.sleep(0.8)

    median = sorted(words)[len(words) // 2] if words else 0
    return {"name": name, "feed": len(feed.entries), "ok": ok,
            "blocked": blocked, "other": other, "median_words": median,
            "note": ""}


def main():
    print(f"{'source':18s}{'feed':>6}{'ok':>5}{'blocked':>9}{'other':>7}"
          f"{'med words':>11}  note")
    usable = []
    for name, url in CANDIDATES.items():
        r = probe(name, url)
        print(f"{r['name']:18s}{r['feed']:>6}{r['ok']:>5}{r['blocked']:>9}"
              f"{r['other']:>7}{r.get('median_words', 0):>11}  {r['note']}")
        if r["ok"] >= SAMPLE - 1 and r["feed"] >= 20:
            usable.append(name)
    print(f"\nUSABLE FROM HERE: {', '.join(usable) if usable else 'none'}")


if __name__ == "__main__":
    main()
