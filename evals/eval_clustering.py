"""Clustering eval — turns "does the grouping look right?" into a number.

HOW IT SCORES

For every PAIR of articles in the gold set we ask two questions: did the system
put them in the same cluster, and does the gold label say it should have?

    precision = of the pairs we merged, how many were correct
                low precision -> unrelated stories jammed together
    recall    = of the pairs that should be merged, how many did we catch
                low recall -> the same story appears twice in the digest
    F1        = the two combined, so configs can be compared at a glance

That maps directly onto the three failure modes PRD section 15 asks about:
articles incorrectly merged (precision), articles that should have merged but
did not (recall), and unrelated articles placed together (precision).

A METHODOLOGICAL NOTE

Clustering runs over the WHOLE day's articles, exactly as production does, and
only then do we score the pairs among the gold-labelled subset. Clustering just
the 115 labelled articles would be an easier problem than the real one — fewer
neighbours to be confused by — and would flatter the numbers.

    python evals/eval_clustering.py            # sweep configurations
    python evals/eval_clustering.py --errors   # show what it got wrong
"""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest import cluster, config, embed, store  # noqa: E402

GOLD = Path(__file__).parent / "clustering_gold.jsonl"
DAY_START, DAY_END = "2026-09-21T00:00", "2026-09-22T00:00"


def load_gold():
    with open(GOLD) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_day_articles():
    conn = store.connect()
    rows = conn.execute(
        """SELECT * FROM articles WHERE extract_status='ok'
           AND published_at >= ? AND published_at < ?
           ORDER BY source, published_at""", (DAY_START, DAY_END)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def score(gold, url_to_cluster):
    """Pairwise precision / recall / F1 over the labelled subset."""
    labelled = [g for g in gold if g["url"] in url_to_cluster]
    tp = fp = fn = 0
    false_merges, missed_merges = [], []

    for a, b in combinations(labelled, 2):
        same_gold = a["story_id"] == b["story_id"]
        same_pred = url_to_cluster[a["url"]] == url_to_cluster[b["url"]]
        if same_pred and same_gold:
            tp += 1
        elif same_pred and not same_gold:
            fp += 1
            false_merges.append((a, b))
        elif same_gold and not same_pred:
            fn += 1
            missed_merges.append((a, b))

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1,
            "tp": tp, "fp": fp, "fn": fn, "scored_articles": len(labelled),
            "false_merges": false_merges, "missed_merges": missed_merges}


def run_config(articles, gold, threshold, linkage, vectors):
    groups, _ = cluster.group(vectors, threshold=threshold, linkage=linkage)
    url_to_cluster = {}
    for cid, members in enumerate(groups):
        for i in members:
            url_to_cluster[articles[i]["url"]] = cid
    return score(gold, url_to_cluster)


def main():
    gold = load_gold()
    articles = load_day_articles()
    print(f"gold: {len(gold)} labelled articles, "
          f"{len({g['story_id'] for g in gold})} stories")
    print(f"clustering over the full day: {len(articles)} articles\n")

    texts = [embed.embedding_input(a["title"], a["body_text"]) for a in articles]
    provider = embed.active_provider()
    model = (config.LOCAL_EMBED_MODEL if provider == "local"
             else config.VOYAGE_EMBED_MODEL)
    print(f"embedding provider: {provider} ({model})\n")
    vectors = embed.embed(texts, provider=provider)

    print(f"{'linkage':>9}{'thr':>6}{'precision':>11}{'recall':>9}{'F1':>8}"
          f"{'merged wrong':>14}{'missed':>8}")
    best = None
    for linkage in ("single", "average"):
        for thr in (0.78, 0.80, 0.82, 0.84, 0.86, 0.88, 0.90):
            r = run_config(articles, gold, thr, linkage, vectors)
            print(f"{linkage:>9}{thr:>6.2f}{r['precision']:>11.3f}"
                  f"{r['recall']:>9.3f}{r['f1']:>8.3f}{r['fp']:>14}{r['fn']:>8}")
            if best is None or r["f1"] > best[0]["f1"]:
                best = (r, linkage, thr)
        print()

    r, linkage, thr = best
    print(f"BEST: linkage={linkage} threshold={thr} -> "
          f"F1={r['f1']:.3f} (precision {r['precision']:.3f}, recall {r['recall']:.3f})")
    print(f"current config: linkage={config.LINKAGE} "
          f"threshold={config.SIMILARITY_THRESHOLD}")

    if "--errors" in sys.argv:
        print(f"\n--- WRONGLY MERGED ({len(r['false_merges'])} pairs) ---")
        for a, b in r["false_merges"][:12]:
            print(f"  [{a['source'][:4]}] {a['title'][:62]}")
            print(f"  [{b['source'][:4]}] {b['title'][:62]}\n")
        print(f"--- MISSED ({len(r['missed_merges'])} pairs) ---")
        for a, b in r["missed_merges"][:12]:
            print(f"  [{a['source'][:4]}] {a['title'][:62]}")
            print(f"  [{b['source'][:4]}] {b['title'][:62]}\n")


if __name__ == "__main__":
    main()
