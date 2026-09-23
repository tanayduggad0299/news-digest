"""Stage 3a — Eligibility gate.

No AI. Pure Python, and it runs BEFORE the LLM on purpose.

PRD section 8 requires at least 2 of the 3 predefined sources to cover a story.
Note carefully: that means two distinct OUTLETS, not two articles. Six News18
pieces about one event are still one source and do not qualify.

Running this free filter first is the single biggest cost decision in the
pipeline. It takes ~250 clusters down to ~40, so we pay the LLM to score 40
stories instead of 250. Reversing the order would cost 6x for identical output.
"""

from . import config


def filter_eligible(clusters, min_sources=None):
    """Split clusters into (eligible, rejected) by distinct-source count."""
    min_sources = min_sources or config.MIN_DISTINCT_SOURCES
    eligible, rejected = [], []
    for c in clusters:
        (eligible if c["source_count"] >= min_sources else rejected).append(c)
    return eligible, rejected


def summarise(clusters, eligible, rejected):
    return {
        "clusters_in": len(clusters),
        "eligible": len(eligible),
        "rejected": len(rejected),
        "rejected_single_source": sum(1 for c in rejected if c["source_count"] == 1),
        "articles_in_eligible": sum(c["size"] for c in eligible),
    }
