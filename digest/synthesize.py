"""Stage 5 — Synthesis.

The third and final LLM call: turn validated claims into the four-part story
format from PRD section 5.

The critical design decision is what the model is allowed to see. It gets the
PUBLISHED CLAIM LIST and nothing else — never the original articles. That is
what makes hallucination structurally hard rather than merely discouraged: the
model cannot repeat a fact it was never shown. Every guardrail in section 14
("do not invent facts", "do not introduce outside information") is enforced by
the shape of the input, not only by asking nicely in the prompt.

Two things still need checking after generation, because a model can restate
what it was given incorrectly even when it invents nothing:

  - numbers it writes must appear in the claims it was given (check_numbers)
  - "what's next" must be absent unless a source stated it

    input  : validated stories (published claims + contradictions only)
    output : {title, what_happened, impact, whats_next} per story
    next   : Stage 6 renders these into an email
"""

import re
import time

from . import config, llm

SYSTEM_PROMPT = """You write short news summaries for a morning digest.

You will be given a list of VERIFIED CLAIMS about one news story. Each claim has \
already been checked against multiple sources. You are writing from that list.

Absolute constraints:
- Use ONLY the claims provided. Every fact in your summary must trace to one.
- Never add background, context or explanation from your own knowledge, however
  obvious or helpful it seems.
- Never state a number, date, name or quantity that is not in the claims.
- If a figure is not available to you, OMIT THE FACT ENTIRELY. Never write a
  vague placeholder in its place — "a specific amount", "a certain number",
  "an undisclosed figure" tell the reader nothing and waste their time. A
  sentence you cannot make concrete does not belong in the digest.
- Likewise, if sources disagree, either name both figures or drop the point.
  Never write that sources "differ on the details" without saying on what.
- Never average, combine, round or reconcile conflicting figures.
- A claim marked [reported only by X] MUST be written with that attribution in
  the sentence ("according to X, ..." / "X reported that ..."). Never state such
  a claim as though every source confirmed it.
- Where a DISPUTED POINT is listed, either report the disagreement plainly
  ("sources differ on X, reporting A and B") or leave the detail out. Never
  present one version as settled fact.
- Do not omit a major provided claim that a reader needs to understand the story.

Length discipline matters as much as accuracy. A claim list may contain facts \
about several loosely related developments; your job is the ONE core event, not \
a recap of everything in the list. Leaving out a minor claim is correct when it \
does not help a reader understand that core event.

Write these fields:
- title: a plain factual headline, under 90 characters. No hype, no clickbait.
- what_happened: the core event. 2-3 sentences, 60 words MAXIMUM. If the claims
  cover several developments, lead with the most consequential one and drop the
  peripheral ones entirely rather than chaining them together.
- impact: why it matters and who is affected. 1-2 sentences, 40 words MAXIMUM.
  This must follow from the claims, not from your own sense of importance. Do
  not use it as an overflow space for leftover facts.
- whats_next: only if a claim states a concrete next step. One sentence. Return
  an empty string otherwise. Never speculate about what might happen.

Write plainly, for a reader with two minutes. Prefer clear sentences over \
dramatic ones. Accuracy matters far more than style, and brevity is part of \
being useful — the reader chose a digest precisely to avoid reading everything."""

SYNTH_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "what_happened": {"type": "string"},
        "impact": {"type": "string"},
        "whats_next": {"type": "string"},
    },
    "required": ["title", "what_happened", "impact", "whats_next"],
}


def story_to_prompt(validated):
    """Render the claim list. This is the model's ENTIRE view of the story."""
    lines = [f"CORE EVENT: {validated['what_happened']}", "", "VERIFIED CLAIMS:"]

    for c in validated["published_claims"]:
        srcs = ", ".join(config.SOURCE_NAMES.get(s, s)
                         for s in c.get("resolved_sources", []))
        if c.get("needs_attribution"):
            lines.append(f"- {c['text']}  [reported only by {srcs} — "
                         f"attribute it to them in the text]")
        else:
            lines.append(f"- {c['text']}  [{srcs}]")

    if validated["validated_contradictions"]:
        lines += ["", "DISPUTED POINTS — do not resolve these:"]
        for c in validated["validated_contradictions"]:
            versions = "; ".join(f"{v['source']} says {v['value']}"
                                 for v in c["versions"])
            lines.append(f"- {c['topic']}: {versions}")

    if validated.get("whats_next"):
        lines += ["", f"STATED NEXT STEP: {validated['whats_next']}"]
    else:
        lines += ["", "STATED NEXT STEP: none — leave whats_next empty."]

    return "\n".join(lines)


# ------------------------------------------------------ post-generation check --

# A digit run NOT preceded by a letter or digit. The negative lookbehind
# matters: "News18" is one of our own source names, and a naive \d+ pattern
# reported its "18" as an invented figure on almost every story. A verifier
# that cries wolf is worse than none — it trains you to ignore it.
_NUM = re.compile(r"(?<![A-Za-z\d])\d[\d,.]*")


def _numbers_in(text):
    """Bare numeric tokens, normalised so '1,110' and '1110' compare equal."""
    return {m.group(0).replace(",", "").rstrip(".") for m in _NUM.finditer(text or "")}


def check_numbers(summary, validated):
    """PRD section 14 says 'do not create numbers'. This verifies it happened.

    Every number in the generated text must appear somewhere in the claims the
    model was given. A number that does not is either invented or miscopied —
    both are serious, and both are invisible without this check.
    """
    allowed = set()
    for c in validated["published_claims"]:
        allowed |= _numbers_in(c["text"]) | _numbers_in(c.get("value", ""))
    for c in validated["validated_contradictions"]:
        for v in c["versions"]:
            allowed |= _numbers_in(v["value"])
    allowed |= _numbers_in(validated["what_happened"])
    allowed |= _numbers_in(validated.get("whats_next") or "")

    written = set()
    for field in ("title", "what_happened", "impact", "whats_next"):
        written |= _numbers_in(summary.get(field, ""))

    # Small integers are usually prose ("two people", "third day") rather than
    # copied data, and they collide constantly. Only flag numbers big enough to
    # be a real figure.
    unsupported = {n for n in written - allowed
                   if len(n.replace(".", "")) >= 2 or float(n or 0) > 12}
    return sorted(unsupported)


# ------------------------------------------------------------------- driver --

def synthesize_story(validated):
    data, usage = llm.generate_structured(
        SYSTEM_PROMPT,
        story_to_prompt(validated),
        SYNTH_SCHEMA,
        max_tokens=config.SYNTH_MAX_TOKENS,
    )
    nxt = (data.get("whats_next") or "").strip()
    summary = {
        "title": data["title"].strip(),
        "what_happened": data["what_happened"].strip(),
        "impact": data["impact"].strip(),
        "whats_next": nxt if nxt and nxt.lower() not in ("null", "none", "n/a") else None,
    }
    summary["unsupported_numbers"] = check_numbers(summary, validated)
    summary["sources"] = [
        {"name": config.SOURCE_NAMES.get(a["source"], a["source"]), "url": a["url"]}
        for a in validated["story"]["cluster"]["articles"]
    ]
    return summary, usage


def synthesize_all(validated_stories, verbose=True):
    summaries, skipped, failures = [], [], []
    usage = {"input": 0, "output": 0, "api_calls": 0}

    for i, v in enumerate(validated_stories, 1):
        n_claims = len(v["published_claims"])
        head = v["story"]["headline"][:60]

        # A story whose claims were nearly all withheld cannot be written
        # honestly. PRD section 9 would rather send fewer stories than pad.
        if n_claims < config.MIN_PUBLISHABLE_CLAIMS:
            skipped.append({"headline": head, "published_claims": n_claims})
            if verbose:
                print(f"  [{i}] SKIPPED ({n_claims} verified claims, "
                      f"need {config.MIN_PUBLISHABLE_CLAIMS}): {head}", flush=True)
            continue

        if usage["api_calls"]:
            time.sleep(config.LLM_REQUEST_SPACING)
        try:
            summary, u = synthesize_story(v)
            usage["api_calls"] += 1
            usage["input"] += u["input_tokens"]
            usage["output"] += u["output_tokens"]
        except Exception as exc:
            failures.append({"headline": head, "error": f"{type(exc).__name__}: {exc}"[:180]})
            if verbose:
                print(f"  [{i}] FAILED {type(exc).__name__}: {str(exc)[:70]}", flush=True)
            continue

        summaries.append(summary)
        if verbose:
            flag = (f"  ** UNSUPPORTED NUMBERS: {summary['unsupported_numbers']}"
                    if summary["unsupported_numbers"] else "")
            print(f"  [{i}] {summary['title'][:66]}{flag}", flush=True)

    price = config.LLM_PRICING[config.LLM_PROVIDER]
    usage["cost_usd"] = round(usage["input"]/1e6*price["in"]
                              + usage["output"]/1e6*price["out"], 4)
    return summaries, skipped, failures, usage
