"""Stage 4a — Claim extraction.

The second LLM call, and the hardest thing the PRD asks for.

Up to now the model has only had to judge how important a story is. Here it has
to read several articles about one event and report, fact by fact, WHICH SOURCES
SAY WHAT. That source attribution is the whole point: Stage 4b cannot enforce
"a fact must be corroborated" or "never reconcile conflicting numbers" unless it
knows which outlet claimed what.

Why AI is right for this: two outlets will write "a Rs 10,000 crore outlay" and
"the scheme, worth Rs 10,000 crore," and a third "Rs 10,000-crore". Recognising
those as the same claim is semantic alignment — the classic case where a parser
loses and a language model wins.

Why the model does NOT decide what goes in the digest: it reports claims and
who supports them. Policy — the corroboration rule, the contradiction rule, the
never-average guardrail — lives in validate.py, in Python, where it is
deterministic and auditable. A rule in a prompt is a request. A rule in code is
a rule.

    input  : the top 5 clusters, FULL article text (first full-text use)
    output : per story, a claim list with per-claim source attribution
    next   : validate.py applies PRD sections 11 and 12 to that list
"""

import time

from . import config, llm

SYSTEM_PROMPT = """You extract structured facts from several news articles that \
all describe the same event.

Your job is reporting, not writing. For every factual claim, record exactly which \
of the provided sources state it. This attribution is used downstream to decide \
what may be published, so accuracy about WHO SAID WHAT matters more than anything \
else you do here.

Rules:
- Use only the article text provided. Never add background knowledge of your own.
- List a claim once, even when several sources make it, with every supporting
  source named in supported_by.
- Use the exact source labels given in the article headings.
- Mark a claim "quantitative" when it contains a number, amount, date, count,
  percentage or measurable quantity. Mark it "descriptive" otherwise.
- Mark `significance` "major" for any consequential assertion — a death,
  casualty figure, attack, arrest, court ruling, resignation, official decision,
  or an attribution of blame or responsibility. Everything else is "minor".
- For a quantitative claim, put the figure itself in `value` exactly as written
  (for example "Rs 10,000 crore", "31", "September 18, 2026").
- When sources give DIFFERENT values for the same underlying fact, do not pick
  one and do not average them. Record it in `contradictions` with each source's
  version, and leave it out of `claims`.
- `whats_next` may only describe a future step the articles actually state, and
  it needs the same source attribution as any other claim. Leave its text empty
  if no source says what happens next. Never speculate.
- `what_happened` is a neutral one-line description of the event. Use only names,
  organisations and figures that appear in the articles.

Extract every materially important fact. Omit colour, quotes that add no fact, \
and background that does not help someone understand what happened."""

CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "what_happened": {
            "type": "string",
            "description": "One or two sentences stating the core event.",
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "claim_type": {"type": "string",
                                   "enum": ["quantitative", "descriptive"]},
                    "value": {"type": "string"},
                    "supported_by": {"type": "array", "items": {"type": "string"}},
                    "is_essential": {
                        "type": "boolean",
                        "description": "True if a reader cannot understand the "
                                       "story without this fact.",
                    },
                    "significance": {
                        "type": "string",
                        "enum": ["major", "minor"],
                        "description": "major = a consequential assertion a "
                            "reader would act on or be misled by if wrong "
                            "(deaths, casualties, attacks, arrests, rulings, "
                            "resignations, attributions of blame or "
                            "responsibility, official decisions). minor = "
                            "supporting colour, procedure or background.",
                    },
                },
                "required": ["text", "claim_type", "value", "supported_by",
                             "is_essential", "significance"],
            },
        },
        "contradictions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "versions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source": {"type": "string"},
                                "value": {"type": "string"},
                            },
                            "required": ["source", "value"],
                        },
                    },
                },
                "required": ["topic", "versions"],
            },
        },
        "whats_next": {
            "type": "object",
            "properties": {
                "text": {"type": "string",
                         "description": "Empty string if no source states a next step."},
                "supported_by": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["text", "supported_by"],
        },
    },
    "required": ["what_happened", "claims", "contradictions", "whats_next"],
}


def cluster_to_prompt(cluster, max_words=None, max_articles=None):
    """Full article text, clearly labelled by source.

    Labels must be stable and unambiguous, because the model echoes them back in
    supported_by and validate.py matches on them. We use the human-readable
    outlet name and nothing else.
    """
    max_words = max_words or config.CLAIMS_MAX_WORDS_PER_ARTICLE
    max_articles = max_articles or config.CLAIMS_MAX_ARTICLES

    # One article per source first, so a 9-article cluster dominated by one
    # outlet still shows the model every source's version.
    chosen, seen = [], set()
    for a in cluster["articles"]:
        if a["source"] not in seen:
            chosen.append(a)
            seen.add(a["source"])
    for a in cluster["articles"]:
        if len(chosen) >= max_articles:
            break
        if a not in chosen:
            chosen.append(a)

    blocks = [f"{len(chosen)} articles from {len(seen)} sources describe one event.\n"]
    for a in chosen[:max_articles]:
        body = " ".join((a["body_text"] or "").split()[:max_words])
        blocks.append(f"===== SOURCE: {config.SOURCE_NAMES.get(a['source'], a['source'])} =====\n"
                      f"HEADLINE: {a['title']}\n\n{body}\n")
    return "\n".join(blocks)


def extract_cluster(cluster):
    data, usage = llm.generate_structured(
        SYSTEM_PROMPT,
        cluster_to_prompt(cluster),
        CLAIMS_SCHEMA,
        max_tokens=config.CLAIMS_MAX_TOKENS,
    )
    nxt = data.get("whats_next") or {}
    text = (nxt.get("text") or "").strip()
    return {
        "cluster_id": cluster["cluster_id"],
        "what_happened": data["what_happened"],
        "claims": data.get("claims", []),
        "contradictions": data.get("contradictions", []),
        # Carried as a claim-shaped object so validate.py can apply the same
        # corroboration rule. It used to be a bare string that bypassed
        # validation entirely — which is how an invented party name reached a
        # delivered digest without tripping a single guardrail.
        "whats_next_claim": (
            {"text": text, "supported_by": nxt.get("supported_by", []),
             "claim_type": "descriptive", "significance": "major",
             "is_essential": False}
            if text and text.lower() not in ("null", "none", "n/a") else None),
    }, usage


def extract_all(stories, verbose=True):
    """One call per story — this is the accuracy-critical step, so each story
    gets the model's full attention rather than sharing a batch."""
    extracted, failures = [], []
    usage = {"input": 0, "output": 0, "api_calls": 0}

    for i, s in enumerate(stories, 1):
        if i > 1:
            time.sleep(config.LLM_REQUEST_SPACING)
        if verbose:
            print(f"  [{i}/{len(stories)}] {s['headline'][:66]}", flush=True)
        try:
            result, u = extract_cluster(s["cluster"])
            usage["api_calls"] += 1
            usage["input"] += u["input_tokens"]
            usage["output"] += u["output_tokens"]
        except Exception as exc:
            failures.append({"cluster_id": s["cluster"]["cluster_id"],
                             "error": f"{type(exc).__name__}: {exc}"[:200]})
            if verbose:
                print(f"       FAILED {type(exc).__name__}: {str(exc)[:70]}", flush=True)
            continue

        result["story"] = s
        extracted.append(result)
        if verbose:
            q = sum(1 for c in result["claims"] if c["claim_type"] == "quantitative")
            print(f"       {len(result['claims'])} claims ({q} quantitative), "
                  f"{len(result['contradictions'])} contradictions", flush=True)

    price = config.LLM_PRICING[config.LLM_PROVIDER]
    usage["cost_usd"] = round(usage["input"]/1e6*price["in"] + usage["output"]/1e6*price["out"], 4)
    return extracted, failures, usage
