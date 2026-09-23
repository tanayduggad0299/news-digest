"""Stage 3c — Ranking and selection.

No AI. A sort key, deliberately.

PRD section 8 fixes the priority order: real-world impact carries the highest
weight, then source coverage, then recency. Keeping this in code rather than
asking the model to "pick the best five" means the ordering is inspectable and
reproducible — the same scores always produce the same digest.

PRD section 9 is explicit that fewer than five qualifying stories means sending
fewer. There is deliberately no code path that relaxes the gate to fill slots.
"""

from datetime import datetime, timezone

from . import config


def _newest(cluster):
    stamps = []
    for a in cluster["articles"]:
        try:
            stamps.append(datetime.fromisoformat(a["published_at"]))
        except (ValueError, TypeError):
            pass
    return max(stamps) if stamps else datetime.min.replace(tzinfo=timezone.utc)


def rank(scored, score_field=None):
    """Sort scored clusters best-first. Returns a new list."""
    score_field = score_field or config.RANKING_SCORE_FIELD
    return sorted(
        scored,
        key=lambda s: (
            s[score_field],                   # 1. impact — highest weight
            s["cluster"]["source_count"],     # 2. source coverage
            _newest(s["cluster"]),            # 3. recency
        ),
        reverse=True,
    )


def select_top(scored, n=None, min_impact=None):
    """Apply the impact floor, then take at most n.

    The floor matters: PRD section 8 says a story must have *meaningful*
    real-world impact to enter the ranking at all. Without it, a slow news day
    would promote five score-0 stories into the digest purely because nothing
    better existed.
    """
    n = n or config.TOP_N_STORIES
    min_impact = config.MIN_IMPACT_SCORE if min_impact is None else min_impact
    field = config.RANKING_SCORE_FIELD
    qualifying = [s for s in rank(scored) if s[field] >= min_impact]
    return qualifying[:n], len(qualifying)
