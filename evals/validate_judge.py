"""Does the automated grader agree with the human?

Run this BEFORE believing anything digest/judge.py reports. A grader whose
scores have never been checked against a person's produces official-looking
numbers that mean nothing.

WHAT AGREEMENT LOOKS LIKE

Exact agreement is the wrong target — two careful humans scoring the same
summary out of 5 often differ by a point. What matters is:

  within 1    the grader lands next to the human. This is the headline.
  bias        does it score consistently HIGHER than the human? A generous
              grader is the dangerous failure: it reports everything fine
              while problems ship.
  unacceptable  agreement on the binary flag. This matters more than any
              average — it is the number that decides whether the digest can
              be trusted at all, and a grader that misses what a human calls
              unacceptable is worse than no grader.

    python evals/validate_judge.py human_scores.json digest.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from digest import config, judge  # noqa: E402

# The human rubric used the name "hallucination"; the grader uses "invention".
# Same criterion, different word.
ALIASES = {"hallucination": "invention", "contradiction": "contradiction_handling"}


def load_human(path):
    """[{story, title, criteria:{...}, unacceptable}] keyed by story index."""
    rows = json.load(open(path))
    out = {}
    for r in rows:
        crit = {ALIASES.get(k, k): v for k, v in r["criteria"].items()}
        out[r["story"]] = {"criteria": crit, "unacceptable": bool(r.get("unacceptable")),
                           "title": r.get("title", "")}
    return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    human = load_human(sys.argv[1])
    digest = json.load(open(sys.argv[2]))
    summaries, evidence = digest["stories"], digest["evidence"]

    print(f"grading {len(summaries)} summaries with {config.JUDGE_MODEL}")
    print(f"(the writer used {config.LLM_MODELS[config.LLM_PROVIDER]} — "
          f"deliberately not the same model)\n")
    graded, failures, usage = judge.grade_all(summaries, evidence)
    if failures:
        print(f"\n{len(failures)} could not be graded; comparing the rest")

    print(f"\n{'':32}{'human':>7}{'judge':>7}{'gap':>6}")
    diffs, within1, exact, higher, lower = [], 0, 0, 0, 0
    for i, g in enumerate(graded):
        h = human.get(i)
        if not h:
            continue
        print(f"  {g['title'][:30]:32}")
        for c in judge.CRITERIA:
            hv, jv = h["criteria"].get(c), g["criteria"][c]
            if hv is None:
                continue
            d = jv - hv
            diffs.append(d)
            if abs(d) <= 1:
                within1 += 1
            if d == 0:
                exact += 1
            elif d > 0:
                higher += 1
            else:
                lower += 1
            mark = "" if abs(d) <= 1 else "   <-- disagrees"
            print(f"    {c:28}{hv:>7}{jv:>7}{d:>+6}{mark}")

    n = len(diffs)
    if not n:
        print("\nno overlapping scores to compare")
        return 1

    print(f"\n{'='*52}\nAGREEMENT over {n} scored criteria\n")
    print(f"  exact match      {exact:>3}/{n}  ({exact/n*100:.0f}%)")
    print(f"  within 1 point   {within1:>3}/{n}  ({within1/n*100:.0f}%)   <- the headline")
    print(f"  mean gap         {sum(diffs)/n:>+6.2f}   "
          f"({'grader is GENEROUS' if sum(diffs)/n > 0.3 else 'grader is HARSH' if sum(diffs)/n < -0.3 else 'no strong bias'})")
    print(f"  judge higher     {higher:>3}   judge lower {lower}")

    hu = {i for i, h in human.items() if h["unacceptable"]}
    ju = {i for i, g in enumerate(graded) if g["unacceptable"]}
    print(f"\n  UNACCEPTABLE flag — the one that decides trust")
    print(f"    human flagged  {sorted(hu) if hu else 'none'}")
    print(f"    judge flagged  {sorted(ju) if ju else 'none'}")
    missed = hu - ju
    print(f"    agreed on      {len(hu & ju)} of {len(hu)} the human flagged")
    if missed:
        print(f"    MISSED         {sorted(missed)} — the grader called these acceptable")

    ok = (within1 / n >= config.JUDGE_AGREEMENT_BAR) and not missed
    print(f"\n{'TRUSTWORTHY' if ok else 'NOT YET TRUSTWORTHY'}: "
          f"within-1 agreement {within1/n*100:.0f}% "
          f"(bar {config.JUDGE_AGREEMENT_BAR*100:.0f}%)"
          f"{', and it missed a human-flagged failure' if missed else ''}")
    if not ok:
        print("Fix the grader's prompt and re-run before using its scores.")

    out = Path(__file__).parent / "judge_validation.json"
    json.dump({"graded": graded, "within1": within1, "n": n,
               "mean_gap": sum(diffs)/n, "human_flagged": sorted(hu),
               "judge_flagged": sorted(ju), "trustworthy": ok},
              open(out, "w"), indent=2, ensure_ascii=False)
    print(f"\nwrote {out.name}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
