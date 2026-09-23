"""Stage 3b — Impact scoring.

THIS IS THE FIRST LANGUAGE MODEL CALL IN THE PIPELINE.

Why AI is the right tool here: PRD section 8 defines important news as "news
that has significant real-world impact on a large number of people". That is a
judgment requiring world knowledge — no rule you could write in Python knows
that a Supreme Court ruling on methanol denaturing matters more than a celebrity
airport sighting. Everything up to this point was arithmetic; this genuinely
is not.

PRD section 8 asks for two approaches to be tested:
  Approach 1 — the LLM assigns an impact score directly.
  Approach 2 — the LLM extracts impact indicators, and scoring logic in code
               converts those indicators into a score.

We get BOTH from a single call: the schema asks for the direct score and the
indicators together, then Python derives Approach 2's score from the indicators.
One API call, two competing methods, compared for free on the same input.

    input  : eligible clusters (title + lede per article, source-labelled)
    model  : whatever config.LLM_PROVIDER selects, via llm.generate_structured
    output : {impact_score, indicators, derived_score, headline, reasoning}
    next   : Stage 3c ranks on these and takes the top 5
"""

import json
import time
from typing import Literal

from pydantic import BaseModel, Field

from . import config, llm

# ----------------------------------------------------------------- schema --
# Structured output: we hand Claude this schema and the API constrains
# generation so the reply MUST fit it. No prose to parse, no JSON wrapped in a
# chatty sentence, no 6 AM crash because the model felt conversational.


class ImpactIndicators(BaseModel):
    """Approach 2's raw material — observable properties, not a judgment."""
    geographic_scale: Literal["local", "regional", "national", "international"]
    people_affected: Literal["few", "thousands", "millions", "hundreds_of_millions"]
    is_binding_decision: bool = Field(
        description="True if something was actually decided, enacted, passed or "
                    "ruled. False for statements, proposals, speculation or reaction."
    )
    consequence_severity: Literal["minimal", "moderate", "severe"]
    domain: str = Field(description="Short topic label, e.g. 'trade_policy'.")


class ImpactAssessment(BaseModel):
    headline: str = Field(description="One neutral sentence naming the event.")
    impact_score: int = Field(ge=0, le=3, description="Approach 1: direct 0-3 score.")
    indicators: ImpactIndicators
    reasoning: str = Field(description="One or two sentences justifying the score.")


# ----------------------------------------------------------------- prompt --

SYSTEM_PROMPT = """You assess the real-world impact of news stories for a daily digest.

Important news is news that has significant real-world impact on a large number \
of people. You are NOT judging how interesting, dramatic, or widely covered a \
story is — only how much it materially affects people's lives.

Score on this rubric:
  3 - Major:       large-scale or national impact, or affects a very large population.
  2 - Significant: meaningful impact on a substantial population or region.
  1 - Limited:     relatively smaller impact.
  0 - Minimal:     little meaningful real-world impact.

Guidance:
- A decision that has actually been taken outranks a statement about a possible
  future decision on the same topic.
- Celebrity news, sport results, opinion pieces and human-interest stories are
  usually 0 or 1 however prominently they are covered.
- Deaths, disasters, court rulings, laws, budgets, prices and policies that bind
  people are usually 2 or 3 depending on how many people they reach.
- Judge only from the article text provided. Do not use outside knowledge about
  what happened afterwards.

Fill the indicators from what the text actually says, independently of the score."""


def cluster_to_prompt(cluster, lede_words=None):
    """Build the user message for one cluster.

    We send title + lede only, not full articles. News is an inverted pyramid,
    so the lede carries the facts needed to judge impact, and this keeps the
    scoring pass roughly 5x cheaper than sending full text. Full articles are
    sent later, in Stage 4, and only for the top 5.
    """
    lede_words = lede_words or config.IMPACT_LEDE_WORDS
    parts = [f"{cluster['size']} articles from "
             f"{cluster['source_count']} sources cover this story.\n"]
    for a in cluster["articles"][:config.IMPACT_MAX_ARTICLES]:
        lede = " ".join((a["body_text"] or "").split()[:lede_words])
        parts.append(f"--- {config.SOURCE_NAMES.get(a['source'], a['source'])} ---\n"
                     f"{a['title']}\n{lede}\n")
    return "\n".join(parts)


# ------------------------------------------------------- Approach 2 logic --

_SCALE = {"local": 0, "regional": 1, "national": 3, "international": 3}
_PEOPLE = {"few": 0, "thousands": 1, "millions": 3, "hundreds_of_millions": 4}
_SEVERITY = {"minimal": 0, "moderate": 1, "severe": 2}


def derived_score(ind: ImpactIndicators) -> int:
    """Approach 2: deterministic scoring from the extracted indicators.

    The point of this method is auditability. When a story scores 3 you can see
    exactly which indicator earned each point, and you can retune the weights
    without touching the prompt or re-running the model.
    """
    points = (_SCALE[ind.geographic_scale]
              + _PEOPLE[ind.people_affected]
              + _SEVERITY[ind.consequence_severity]
              + (1 if ind.is_binding_decision else 0))
    if points >= 8:
        return 3
    if points >= 5:
        return 2
    if points >= 2:
        return 1
    return 0


# -------------------------------------------------------------- the call --

# We score several stories per request rather than one call per story.
#
# Why: the Gemini free tier allows only 20 requests per day per model, and one
# call per cluster needs ~31. Batching takes that to 4. It is also the better
# design on a paid provider — fewer round trips, one system prompt instead of
# 31, and lower latency.
#
# The risk of batching is that a model shown eight stories together starts
# ranking them against each other instead of scoring each against the absolute
# rubric. The prompt below counters that explicitly, and the batch stays small
# enough that no story gets lost in the middle.

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "assessments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "story_number": {"type": "integer"},
                    "headline": {"type": "string"},
                    "impact_score": {"type": "integer"},
                    "indicators": {
                        "type": "object",
                        "properties": {
                            "geographic_scale": {"type": "string",
                                "enum": ["local", "regional", "national", "international"]},
                            "people_affected": {"type": "string",
                                "enum": ["few", "thousands", "millions", "hundreds_of_millions"]},
                            "is_binding_decision": {"type": "boolean"},
                            "consequence_severity": {"type": "string",
                                "enum": ["minimal", "moderate", "severe"]},
                            "domain": {"type": "string"},
                        },
                        "required": ["geographic_scale", "people_affected",
                                     "is_binding_decision", "consequence_severity", "domain"],
                    },
                    "reasoning": {"type": "string"},
                },
                "required": ["story_number", "headline", "impact_score",
                             "indicators", "reasoning"],
            },
        },
    },
    "required": ["assessments"],
}

BATCH_INSTRUCTION = """
You will be given several numbered stories in one message.

Score each story INDEPENDENTLY against the absolute rubric above. Do not compare
the stories to one another, do not spread scores across a range, and do not
assume the set contains a mix of scores. If all the stories are major, score
them all 3. If none are, score them all 0.

Return exactly one assessment per story, with story_number matching the number
shown in the heading."""


def batch_to_prompt(clusters, lede_words=None):
    lede_words = lede_words or config.IMPACT_LEDE_WORDS
    blocks = []
    for n, c in enumerate(clusters, 1):
        lines = [f"===== STORY {n} =====",
                 f"({c['size']} articles from {c['source_count']} sources)"]
        for a in c["articles"][:config.IMPACT_MAX_ARTICLES]:
            lede = " ".join((a["body_text"] or "").split()[:lede_words])
            lines.append(f"--- {config.SOURCE_NAMES.get(a['source'], a['source'])} ---\n"
                         f"{a['title']}\n{lede}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def score_batch(clusters):
    """Score one batch of clusters in a single call. Returns (results, usage)."""
    data, usage = llm.generate_structured(
        SYSTEM_PROMPT + BATCH_INSTRUCTION,
        batch_to_prompt(clusters),
        BATCH_SCHEMA,
        max_tokens=config.IMPACT_MAX_TOKENS,
    )
    by_number = {a["story_number"]: a for a in data.get("assessments", [])}
    results = []
    for n, c in enumerate(clusters, 1):
        a = by_number.get(n)
        if a is None:          # model skipped one; caller retries it separately
            continue
        ind = ImpactIndicators(**a["indicators"])
        results.append({
            "cluster_id": c["cluster_id"],
            "headline": a["headline"],
            "impact_score_llm": max(0, min(3, int(a["impact_score"]))),
            "impact_score_derived": derived_score(ind),
            "indicators": ind.model_dump(),
            "reasoning": a["reasoning"],
            "cluster": c,
        })
    return results, usage


def score_all(clusters, verbose=True):
    """Score every cluster, salvaging batches that fail.

    A failed batch used to drop all 8 of its stories silently, which is how a
    503 cost us 27% of one day's news. Batching multiplies the blast radius of
    any single failure, so failures must be recovered rather than logged: we
    re-split a failed batch into halves, and finally retry stragglers one at a
    time. Only a story that fails alone is genuinely lost.
    """
    scored, refusals, errors = [], [], []
    usage = {"input": 0, "output": 0, "api_calls": 0}
    first = [True]

    def attempt(batch, label):
        if not first[0]:
            time.sleep(config.LLM_REQUEST_SPACING)
        first[0] = False
        usage["api_calls"] += 1
        results, u = score_batch(batch)
        usage["input"] += u["input_tokens"]
        usage["output"] += u["output_tokens"]
        if verbose:
            for r in results:
                flag = "  <- methods disagree" if r["impact_score_llm"] != r["impact_score_derived"] else ""
                print(f"    llm={r['impact_score_llm']} derived={r['impact_score_derived']}  "
                      f"{r['headline'][:60]}{flag}", flush=True)
        return results

    def process(batch, label, depth=0):
        """Try a batch; on failure split it, down to single stories."""
        try:
            got = attempt(batch, label)
        except llm.LLMRefusal as exc:
            if len(batch) == 1:
                refusals.append({"cluster_id": batch[0]["cluster_id"], "reason": str(exc)[:120]})
                if verbose:
                    print(f"    REFUSED cluster {batch[0]['cluster_id']}: {str(exc)[:60]}", flush=True)
                return
            # A refusal on a batch may be caused by one story; isolate it.
            got = []
        except Exception as exc:
            if len(batch) == 1:
                errors.append({"cluster_id": batch[0]["cluster_id"],
                               "error": f"{type(exc).__name__}: {exc}"[:200]})
                if verbose:
                    print(f"    LOST cluster {batch[0]['cluster_id']}: {type(exc).__name__}", flush=True)
                return
            if verbose:
                print(f"    {label} failed ({type(exc).__name__}) — splitting {len(batch)} stories",
                      flush=True)
            got = []

        scored.extend(got)
        done = {r["cluster_id"] for r in got}
        missing = [c for c in batch if c["cluster_id"] not in done]
        if not missing:
            return
        if len(missing) == len(batch) and len(batch) > 1:
            mid = len(batch) // 2
            process(batch[:mid], f"{label}a", depth + 1)
            process(batch[mid:], f"{label}b", depth + 1)
        else:
            for c in missing:                     # model skipped these
                process([c], f"{label}-solo", depth + 1)

    size = config.IMPACT_BATCH_SIZE
    batches = [clusters[i:i + size] for i in range(0, len(clusters), size)]
    for bi, batch in enumerate(batches, 1):
        if verbose:
            print(f"  batch {bi}/{len(batches)} ({len(batch)} stories)...", flush=True)
        process(batch, f"batch{bi}")

    price = config.LLM_PRICING[config.LLM_PROVIDER]
    usage["cost_usd"] = round(usage["input"]/1e6*price["in"] + usage["output"]/1e6*price["out"], 4)
    usage["provider"] = config.LLM_PROVIDER
    usage["model"] = config.LLM_MODELS[config.LLM_PROVIDER]
    return scored, refusals, errors, usage
