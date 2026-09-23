"""Stage 2b — Story clustering.

No AI here. Stage 2a turned articles into vectors; this is arithmetic on them.

We compare every article against every other one and group the ones describing
the same event. Two strategies are implemented, selected by config.LINKAGE:

  single   PRD section 7 as written — any pair at or above the threshold is
           grouped, transitively (union-find).
  average  the cluster-level validation section 7 keeps in reserve — a merge
           requires the average cross-pair similarity to clear the threshold.

"average" is the default because single-linkage was measured chaining unrelated
stories into one 31-article cluster on real data. See tune_threshold.py.

Why no vector database: a vector DB exists to search millions of vectors fast.
We have a few hundred per day and discard them after the run. All pairwise
similarities are one matrix multiply that finishes in milliseconds.
"""

from collections import defaultdict

import numpy as np

from . import config, embed, store


class _UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]  # path compression
            i = self.parent[i]
        return i

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def similarity_matrix(vectors):
    """Cosine similarity for unit-length vectors is just the dot product."""
    return vectors @ vectors.T


def group_average(vectors, threshold=None):
    """Average-linkage grouping: merge two groups only when the AVERAGE
    similarity across all their cross-pairs clears the threshold.

    This is the "cluster-level validation" PRD section 7 holds in reserve for
    when single-linkage is shown to produce bad clusters. Single-linkage merges
    on one good pair, so A~B and B~C drags A and C together however unrelated
    they are. Average-linkage asks whether the whole merged group hangs
    together, which is what stops the chain.
    """
    from sklearn.cluster import AgglomerativeClustering

    threshold = config.SIMILARITY_THRESHOLD if threshold is None else threshold
    sim = similarity_matrix(vectors)
    if len(vectors) < 2:
        return [[0]] if len(vectors) == 1 else [], sim

    distance = np.clip(1.0 - sim, 0.0, None)
    np.fill_diagonal(distance, 0.0)

    labels = AgglomerativeClustering(
        n_clusters=None,
        metric="precomputed",
        linkage="average",
        distance_threshold=1.0 - threshold,
    ).fit_predict(distance)

    buckets = defaultdict(list)
    for i, lab in enumerate(labels):
        buckets[int(lab)].append(i)
    return sorted(buckets.values(), key=len, reverse=True), sim


def group(vectors, threshold=None, linkage=None):
    """Returns (clusters, similarity_matrix). Dispatches on linkage strategy."""
    linkage = linkage or config.LINKAGE
    if linkage == "average":
        return group_average(vectors, threshold)
    return group_single(vectors, threshold)


def group_single(vectors, threshold=None):
    """Single-linkage grouping, the literal reading of PRD section 7:
    'if two articles meet the similarity threshold, they can be grouped',
    applied transitively."""
    threshold = config.SIMILARITY_THRESHOLD if threshold is None else threshold
    n = len(vectors)
    sim = similarity_matrix(vectors)

    uf = _UnionFind(n)
    # np.triu_indices gives each unordered pair exactly once, skipping self-pairs.
    rows, cols = np.triu_indices(n, k=1)
    for i, j in zip(rows[sim[rows, cols] >= threshold],
                    cols[sim[rows, cols] >= threshold]):
        uf.union(int(i), int(j))

    buckets = defaultdict(list)
    for i in range(n):
        buckets[uf.find(i)].append(i)
    return sorted(buckets.values(), key=len, reverse=True), sim


def build_clusters(threshold=None, provider=None, linkage=None, verbose=True):
    """Full Stage 2: read ready articles, embed, cluster, return structures."""
    conn = store.connect()
    articles = [dict(r) for r in store.ready_in_window(conn)]
    conn.close()
    if not articles:
        return [], [], None

    provider = provider or embed.active_provider()
    texts = [embed.embedding_input(a["title"], a["body_text"]) for a in articles]
    if verbose:
        print(f"embedding {len(texts)} articles with provider='{provider}' "
              f"({config.LOCAL_EMBED_MODEL if provider == 'local' else config.VOYAGE_EMBED_MODEL})")

    vectors = embed.embed(texts, provider=provider)
    groups, sim = group(vectors, threshold, linkage)

    clusters = []
    for idx, member_ids in enumerate(groups):
        members = [articles[i] for i in member_ids]
        sources = sorted({m["source"] for m in members})
        if len(member_ids) > 1:
            pairs = [sim[a, b] for ai, a in enumerate(member_ids)
                     for b in member_ids[ai + 1:]]
            cohesion = float(np.mean(pairs))
        else:
            cohesion = 1.0
        clusters.append({
            "cluster_id": idx,
            "articles": members,
            "sources": sources,
            "source_count": len(sources),
            "size": len(members),
            "cohesion": round(cohesion, 4),
        })
    return clusters, articles, vectors


if __name__ == "__main__":
    clusters, articles, _ = build_clusters()
    multi = [c for c in clusters if c["size"] > 1]
    eligible = [c for c in clusters if c["source_count"] >= 2]
    print(f"\n{len(articles)} articles -> {len(clusters)} clusters "
          f"({len(multi)} multi-article, {len(eligible)} with 2+ distinct sources)\n")
    for c in eligible[:12]:
        print(f"[cluster {c['cluster_id']:3d}] size={c['size']:2d} "
              f"sources={c['source_count']} cohesion={c['cohesion']:.3f}  "
              f"{','.join(c['sources'])}")
        for a in c["articles"][:4]:
            print(f"      ({a['source'][:4]}) {a['title'][:78]}")
        print()
