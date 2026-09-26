"""Automated summary grading — an LLM scoring the digest against its evidence.

Clustering has a right answer, so a frozen answer key works. Summaries don't:
two good writers produce different summaries of the same story and both can be
correct. So quality is scored against a RUBRIC instead, and the grader is a
model.

WHAT THE GRADER SEES

Exactly what the writer saw — the validated claim list — plus the summary the
writer produced. Not the original articles. That is deliberate: "did this
invent anything?" means "does this contain something the claim list does not
support", and the claim list is the thing to check against. Handing it the
articles would change the question.

THE TWO RULES PEOPLE SKIP

1. A DIFFERENT MODEL FROM THE WRITER. A model grading its own output is
   consistently generous — the same blind spots sit on both sides of the desk.
   config.JUDGE_MODEL is pinned away from the writing chain.

2. VALIDATE THE JUDGE BEFORE TRUSTING IT. Its scores mean nothing until they
   have been checked against a human's on the same summaries. That check is
   evals/validate_judge.py; run it before reading any number this file
   produces as if it were true.

An unvalidated judge is a random number generator with good manners.
"""

import time

from . import config, llm

SYSTEM_PROMPT = """You grade news summaries against the verified facts they \
were built from. You are a strict, fair reviewer, not an editor and not a fan.

You will be given a SUMMARY and the VERIFIED CLAIMS the writer was given. The \
writer was allowed to use those claims and nothing else — no outside knowledge, \
no background.

Score each criterion 1 to 5:
  5  no issue at all
  4  a trivial issue a reader would not notice
  3  a noticeable issue, still tolerable
  2  a significant problem
  1  unacceptable — would mislead the reader

The criteria:

- accuracy: does every factual statement in the summary match a claim? A number,
  name, date or place stated differently from the claim is an accuracy failure,
  however small the difference looks.

- completeness: is any claim important to understanding the event missing from
  the summary? Judge what a reader needs, not how many claims were used. Leaving
  out minor detail is correct behaviour and costs nothing.

- invention: does the summary state anything the claims do not support? This is
  the most serious criterion. An organisation, person, figure or event that
  appears nowhere in the claims is a 1, even if it sounds plausible and even if
  it is only one clause of one sentence.

- contradiction_handling: where DISPUTED POINTS are listed, does the summary
  either report the disagreement or omit the detail? Presenting one side of a
  dispute as settled fact is a 1. If nothing is disputed, score 5.

- clarity: could a reader understand the event in one read? Judge only the
  writing. Vague filler that replaces a fact ("a certain amount", "several
  sources differ on details" without saying on what) belongs here AND in
  accuracy.

Also set `unacceptable` to true if the summary contains a factual error serious \
enough that sending it to a reader would be embarrassing. Be sparing: this is \
for real errors, not for dullness or omission.

For every criterion you score 3 or below, quote the exact phrase from the \
summary that caused it. A grader that cannot point at the problem has not \
found one."""

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "accuracy": {"type": "integer"},
        "completeness": {"type": "integer"},
        "invention": {"type": "integer"},
        "contradiction_handling": {"type": "integer"},
        "clarity": {"type": "integer"},
        "unacceptable": {"type": "boolean"},
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "quote": {"type": "string",
                              "description": "The exact phrase from the summary."},
                    "why": {"type": "string"},
                },
                "required": ["criterion", "quote", "why"],
            },
        },
    },
    "required": ["accuracy", "completeness", "invention",
                 "contradiction_handling", "clarity", "unacceptable", "problems"],
}

CRITERIA = ["accuracy", "completeness", "invention",
            "contradiction_handling", "clarity"]


def build_prompt(summary, evidence):
    lines = ["SUMMARY UNDER REVIEW", "",
             f"Title: {summary['title']}",
             f"What happened: {summary['what_happened']}",
             f"Impact: {summary['impact']}"]
    if summary.get("whats_next"):
        lines.append(f"What's next: {summary['whats_next']}")

    lines += ["", "=" * 50, "", "VERIFIED CLAIMS THE WRITER WAS GIVEN", ""]
    for c in evidence["published_claims"]:
        srcs = ", ".join(config.SOURCE_NAMES.get(s, s)
                         for s in c.get("resolved_sources", []))
        tag = f"only {srcs}" if c.get("needs_attribution") else srcs
        lines.append(f"- {c['text']}  [{tag}]")

    if evidence.get("contradictions"):
        lines += ["", "DISPUTED POINTS — the writer was told not to resolve these"]
        for c in evidence["contradictions"]:
            versions = "; ".join(f"{v['source']} says {v['value']}"
                                 for v in c["versions"])
            lines.append(f"- {c['topic']}: {versions}")
    else:
        lines += ["", "DISPUTED POINTS: none — score contradiction_handling 5."]

    if evidence.get("withheld_claims"):
        lines += ["", "WITHHELD — deliberately kept from the writer. Their absence "
                      "from the summary is correct, not an omission."]
        for c in evidence["withheld_claims"]:
            lines.append(f"- {c['text']}")
    return "\n".join(lines)


def grade_story(summary, evidence):
    data, usage = llm.generate_structured(
        SYSTEM_PROMPT,
        build_prompt(summary, evidence),
        JUDGE_SCHEMA,
        model=config.JUDGE_MODEL,          # never the writing model
        max_tokens=config.JUDGE_MAX_TOKENS,
    )
    scores = {k: max(1, min(5, int(data.get(k, 3)))) for k in CRITERIA}
    return {
        "title": summary["title"],
        "criteria": scores,
        "unacceptable": bool(data.get("unacceptable")),
        "problems": data.get("problems", []),
        "mean": round(sum(scores.values()) / len(scores), 2),
    }, usage


# Severity order for reporting: when a story fails, name the WORST thing first.
# A warning that quotes the completeness nitpick instead of the invented
# organisation is technically accurate and useless.
_SEVERITY = {"invention": 0, "accuracy": 1, "contradiction_handling": 2,
             "completeness": 3, "clarity": 4}


def worst_problem(result):
    """The problem most worth putting in a warning, or None."""
    usable = [p for p in result.get("problems", [])
              if (p.get("quote") or "").strip().lower() not in ("", "none", "n/a")]
    if not usable:
        return None
    return min(usable, key=lambda p: (
        result["criteria"].get(p.get("criterion"), 5),      # lowest score first
        _SEVERITY.get(p.get("criterion"), 9)))              # then by severity


def grade_all(summaries, evidence, verbose=True):
    results, failures = [], []
    usage = {"input": 0, "output": 0, "calls": 0}
    for i, (s, e) in enumerate(zip(summaries, evidence)):
        if i:
            time.sleep(config.LLM_REQUEST_SPACING)
        try:
            r, u = grade_story(s, e)
        except Exception as exc:
            failures.append({"title": s["title"], "error": f"{type(exc).__name__}: {exc}"[:160]})
            if verbose:
                print(f"  [{i+1}] GRADE FAILED {type(exc).__name__}", flush=True)
            continue
        usage["calls"] += 1
        usage["input"] += u["input_tokens"]
        usage["output"] += u["output_tokens"]
        results.append(r)
        if verbose:
            flag = "  UNACCEPTABLE" if r["unacceptable"] else ""
            low = [k for k, v in r["criteria"].items() if v <= 3]
            print(f"  [{i+1}] mean {r['mean']}  "
                  f"{'weak: ' + ','.join(low) if low else 'clean'}{flag}", flush=True)
            for p in r["problems"][:2]:
                print(f"        {p['criterion']}: \"{p['quote'][:58]}\"", flush=True)
    return results, failures, usage
