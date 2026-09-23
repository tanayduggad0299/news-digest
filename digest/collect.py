"""Stage 1a — Collection.

Reads the six RSS feeds and records every article it has not seen before.
No AI here: this is three outlets' published XML, parsed and filed.

    input  : config.FEEDS (six URLs)
    output : rows in the `articles` table with title/url/published_at set
             and extract_status='pending'
"""

import sys
import time
from datetime import datetime, timezone

import feedparser

from . import config, store


def _parse_feed(url, attempts=3):
    """feedparser with retries, because a transient blip must not drop a source."""
    feed = None
    for i in range(attempts):
        feed = feedparser.parse(url, agent=config.USER_AGENT)
        if feed.entries:
            return feed
        if i < attempts - 1:
            time.sleep(2 * (i + 1))
    return feed


def clean_title(title):
    """Strip the outlet's own name off the end of an RSS title."""
    for suffix in config.TITLE_SUFFIXES:
        if title.endswith(suffix):
            title = title[: -len(suffix)]
    return title.strip()


def is_excluded(url):
    """Video, gallery and web-story pages carry no usable article text."""
    low = url.lower()
    return any(pattern in low for pattern in config.EXCLUDE_URL_PATTERNS)


def _published_utc(entry):
    """RSS dates arrive in several formats; feedparser normalises them to a
    time tuple. Returns an ISO-8601 UTC string, or None if unparseable."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    return datetime(*parsed[:6], tzinfo=timezone.utc).isoformat()


def collect(verbose=True):
    conn = store.connect()
    totals = {"seen": 0, "new": 0, "no_date": 0, "excluded": 0,
              "empty_feeds": []}
    per_feed = []

    for source, section, url in config.FEEDS:
        feed = _parse_feed(url)
        if not feed.entries:
            # feedparser NEVER raises: on a network error or a malformed
            # response it returns an object with zero entries. Left unchecked,
            # an entire source vanishes from the digest in silence — which is
            # exactly what happened once here, and with a 2-source eligibility
            # rule it quietly halves the number of stories that can qualify.
            totals["empty_feeds"].append(f"{source}/{section}")
            if verbose:
                print(f"  {source:16s} {section:6s} EMPTY FEED "
                      f"(status={getattr(feed, 'status', '?')}, "
                      f"bozo={getattr(feed, 'bozo', '?')}) — SOURCE LOST")
            continue
        new_here = 0

        for entry in feed.entries:
            link = (entry.get("link") or "").strip()
            title = clean_title((entry.get("title") or "").strip())
            if not link or not title:
                continue

            if is_excluded(link):
                totals["excluded"] += 1
                continue

            published = _published_utc(entry)
            if published is None:
                totals["no_date"] += 1
                continue

            totals["seen"] += 1
            if store.upsert_stub(conn, url=link, source=source, section=section,
                                 title=title, published_at=published):
                new_here += 1

        conn.commit()
        totals["new"] += new_here
        per_feed.append((source, section, len(feed.entries), new_here))
        if verbose:
            print(f"  {source:16s} {section:6s} feed={len(feed.entries):3d}  new={new_here:3d}")
        time.sleep(config.POLITE_DELAY_SECONDS)

    # Keep the committed database bounded (see store.prune).
    pruned = store.prune(conn)
    conn.close()
    if verbose:
        if pruned:
            print(f"  pruned {pruned} articles older than the window")
    if verbose:
        print(f"\ncollected: {totals['seen']} items seen, {totals['new']} new, "
              f"{totals['excluded']} excluded (video/gallery), "
              f"{totals['no_date']} skipped (no date)")
        if totals["empty_feeds"]:
            print(f"WARNING: {len(totals['empty_feeds'])} feed(s) returned nothing: "
                  f"{', '.join(totals['empty_feeds'])}")
            missing = {f.split('/')[0] for f in totals['empty_feeds']}
            complete = {s for s, _, _ in config.FEEDS if s not in missing}
            if len(complete) < config.MIN_DISTINCT_SOURCES:
                print(f"CRITICAL: only {len(complete)} source(s) collected; "
                      f"the {config.MIN_DISTINCT_SOURCES}-source rule cannot be met.")
    return totals, per_feed


if __name__ == "__main__":
    print(f"Collecting from {len(config.FEEDS)} feeds "
          f"at {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n")
    collect()
    sys.exit(0)
