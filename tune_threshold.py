"""Sweep the similarity threshold and report what each value does.

PRD section 7 calls 0.80 "an initial hypothesis rather than a permanent value",
to be adjusted after observing clustering errors. This script produces the
evidence for that decision.

The diagnostic that matters most is max cluster size. Single-linkage grouping
is transitive, so A~B and B~C merges A and C even when A and C are unrelated.
A runaway cluster is the visible symptom of that chaining.
"""

import numpy as np

from digest import cluster, config, embed, store


def main():
    conn = store.connect()
    articles = [dict(r) for r in store.ready_in_window(conn)]
    conn.close()

    texts = [embed.embedding_input(a["title"], a["body_text"]) for a in articles]
    provider = embed.active_provider()
    print(f"{len(articles)} articles, provider='{provider}'\n")
    vectors = embed.embed(texts, provider=provider)

    sim = cluster.similarity_matrix(vectors)
    iu = np.triu_indices(len(vectors), k=1)
    pairs = sim[iu]
    print("pairwise similarity distribution (this is model-specific):")
    for p in (50, 90, 99, 99.9):
        print(f"   p{p:<5} = {np.percentile(pairs, p):.3f}")
    print(f"   max    = {pairs.max():.3f}\n")

    print(f"{'thr':>6}{'clusters':>10}{'multi':>7}{'elig':>6}{'largest':>9}"
          f"{'2nd':>6}{'med cohesion':>14}")
    for thr in [0.78, 0.80, 0.82, 0.84, 0.85, 0.86, 0.87, 0.88, 0.90, 0.92]:
        groups, _ = cluster.group(vectors, threshold=thr)
        sizes = sorted((len(g) for g in groups), reverse=True)
        multi = [g for g in groups if len(g) > 1]
        elig = [g for g in groups
                if len({articles[i]["source"] for i in g}) >= 2]
        cohs = []
        for g in multi:
            vals = [sim[a, b] for ai, a in enumerate(g) for b in g[ai + 1:]]
            cohs.append(float(np.mean(vals)))
        med = np.median(cohs) if cohs else float("nan")
        print(f"{thr:>6.2f}{len(groups):>10}{len(multi):>7}{len(elig):>6}"
              f"{sizes[0]:>9}{(sizes[1] if len(sizes) > 1 else 0):>6}{med:>14.3f}")


if __name__ == "__main__":
    main()
