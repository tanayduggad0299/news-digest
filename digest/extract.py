"""Stage 1b — Article text extraction.

Visits each collected URL and pulls out the clean article body: no navigation,
no adverts, no "related stories" boxes. Still no AI — trafilatura uses
structural heuristics that work across sites, so we maintain no per-site rules.

    input  : articles with extract_status='pending' inside the lookback window
    output : body_text filled in, extract_status set to 'ok' or 'failed'
    next   : Stage 2 embeds title + lede of every 'ok' article
"""

import sys
import time

import requests
import trafilatura

from . import config, store


def clean_body(text, title):
    """Drop leading navigation crumbs and a repeated headline line.

    trafilatura occasionally keeps a breadcrumb ("homevideos") or echoes the
    headline as the first line. Both get embedded in Stage 2 and add noise.
    """
    lines = [ln.strip() for ln in text.split("\n")]
    norm_title = title.strip().lower()

    while lines:
        first = lines[0].lower()
        if not first:
            lines.pop(0)
        elif first in config.BODY_JUNK_PREFIXES:
            lines.pop(0)
        elif first == norm_title:
            lines.pop(0)
        else:
            break

    return "\n".join(ln for ln in lines if ln).strip()


def fetch_text(url, title=""):
    """Returns (body_text, word_count). Raises on any failure."""
    resp = requests.get(
        url,
        headers={"User-Agent": config.USER_AGENT},
        timeout=config.FETCH_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()

    text = trafilatura.extract(
        resp.text,
        include_comments=False,
        include_tables=False,
        favor_precision=True,
    )
    if not text:
        raise ValueError("trafilatura found no article body")

    text = clean_body(text, title)
    words = len(text.split())
    if words < config.MIN_BODY_WORDS:
        raise ValueError(f"body too short ({words} words) — likely paywall or gallery")

    return text, words


def extract_pending(limit=None, verbose=True):
    conn = store.connect()
    rows = store.pending_in_window(conn)
    if limit:
        rows = rows[:limit]

    stats = {"ok": 0, "failed": 0}
    reasons = {}

    for i, row in enumerate(rows, 1):
        try:
            text, words = fetch_text(row["url"], row["title"])
            store.save_body(conn, row["url"], text, words)
            stats["ok"] += 1
            status = f"ok   {words:5d}w"
        except Exception as exc:
            store.save_failure(conn, row["url"], exc)
            stats["failed"] += 1
            key = type(exc).__name__ if not isinstance(exc, ValueError) else str(exc)[:40]
            reasons[key] = reasons.get(key, 0) + 1
            status = f"FAIL {type(exc).__name__}"

        conn.commit()
        if verbose:
            print(f"  [{i:3d}/{len(rows)}] {status}  {row['source']:16s} {row['title'][:54]}")
        time.sleep(config.POLITE_DELAY_SECONDS)

    conn.close()
    if verbose:
        print(f"\nextracted: {stats['ok']} ok, {stats['failed']} failed")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {n:3d}x {reason}")
    return stats, reasons


if __name__ == "__main__":
    cap = int(sys.argv[1]) if len(sys.argv) > 1 else None
    extract_pending(limit=cap)
