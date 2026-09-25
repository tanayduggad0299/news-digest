"""Clustering regression test — does a change make the grouping worse?

Self-contained on purpose. The article text lives inside clustering_gold.jsonl,
so this runs identically forever: it does not read the live database, is not
affected by pruning, re-collection or swapping a news source, and needs no API
key. An earlier version read from the database and was silently destroyed when
the collector pruned old articles. An eval that depends on mutable state is not
a regression test.

WHAT IT MEASURES

For every PAIR of articles, two questions: should they be grouped (per the
hand-written answer key), and did we group them?

    found   pairs correctly grouped
    missed  pairs that belong together but were split apart
            -> a real story fragments, each half has fewer sources, and it can
               fail the two-source gate and vanish from the digest
    wrong   pairs grouped that do not belong together
            -> one digest entry describes two unrelated events

EXIT CODE

0 when every bar in config.EVAL_BARS is met, 1 otherwise — so CI fails the
build rather than letting quality drift in unnoticed.

    python evals/run_regression.py            check the current config
    python evals/run_regression.py --sweep    show neighbouring settings too
"""

import json
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest import cluster, config, embed  # noqa: E402

GOLD = Path(__file__).parent / "clustering_gold.jsonl"


def load_gold():
    with open(GOLD) as f:
        return [json.loads(line) for line in f if line.strip()]


def score(gold, labels):
    found = missed = wrong = 0
    wrong_pairs, missed_pairs = [], []
    for (ia, a), (ib, b) in combinations(list(enumerate(gold)), 2):
        same_gold = a["story_id"] == b["story_id"]
        same_pred = labels[ia] == labels[ib]
        if same_pred and same_gold:
            found += 1
        elif same_pred:
            wrong += 1
            wrong_pairs.append((a, b))
        elif same_gold:
            missed += 1
            missed_pairs.append((a, b))

    should = found + missed
    precision = found / (found + wrong) if found + wrong else 1.0
    recall = found / should if should else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"found": found, "missed": missed, "wrong": wrong, "should": should,
            "precision": precision, "recall": recall, "f1": f1,
            "wrong_pairs": wrong_pairs, "missed_pairs": missed_pairs}


def run(gold, vectors, threshold=None, linkage=None):
    groups, _ = cluster.group(vectors, threshold=threshold, linkage=linkage)
    labels = {}
    for cid, members in enumerate(groups):
        for i in members:
            labels[i] = cid
    return score(gold, labels)


def main():
    gold = load_gold()
    vectors = embed.embed(
        [embed.embedding_input(g["title"], g["body_text"]) for g in gold])

    bars = config.EVAL_BARS
    r = run(gold, vectors)

    print(f"CLUSTERING REGRESSION — {len(gold)} articles, "
          f"{len({g['story_id'] for g in gold})} stories, {r['should']} pairs to find")
    print(f"config: linkage={config.LINKAGE} threshold={config.SIMILARITY_THRESHOLD} "
          f"embedder={config.LOCAL_EMBED_MODEL}\n")
    print(f"  found  {r['found']:>4} of {r['should']}")
    print(f"  missed {r['missed']:>4}   (stories split apart)")
    print(f"  wrong  {r['wrong']:>4}   (unrelated stories merged)\n")

    failures = []
    print(f"  {'measure':12}{'result':>9}{'your bar':>10}   status")
    for name, bar in bars.items():
        got = r[name]
        ok = got >= bar
        if not ok:
            failures.append(f"{name} {got:.3f} < {bar}")
        print(f"  {name:12}{got:>9.3f}{bar:>10.2f}   {'PASS' if ok else 'FAIL'}")

    if "--sweep" in sys.argv:
        print(f"\n  {'setting':22}{'found':>7}{'missed':>8}{'wrong':>7}{'f1':>7}")
        for link in ("single", "average"):
            for thr in (0.78, 0.80, 0.82, 0.84, 0.86):
                s = run(gold, vectors, thr, link)
                here = " <-" if (link == config.LINKAGE
                                 and abs(thr - config.SIMILARITY_THRESHOLD) < 1e-9) else ""
                print(f"  {link + ' @ ' + str(thr):22}{s['found']:>7}"
                      f"{s['missed']:>8}{s['wrong']:>7}{s['f1']:>7.3f}{here}")

    if failures:
        print(f"\nREGRESSION: {'; '.join(failures)}")
        print("Clustering got worse than the bar you set. Do not ship this.")
        return 1
    print("\nAll bars met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
