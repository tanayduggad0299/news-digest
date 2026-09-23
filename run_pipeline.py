"""Full pipeline, Stages 1-6.

    python run_pipeline.py                 collect, build, write a preview file
    python run_pipeline.py --skip-collect  reuse articles already in the database
    python run_pipeline.py --send          actually email the digest
"""

import json
import sys

from digest import (claims, cluster, config, deliver, eligibility, impact,
                    llm, rank, render, synthesize, validate)


def main(skip_collect=False):
    if not skip_collect:
        from digest import collect, extract
        print("=== Stage 1: collect + extract ===", flush=True)
        collect.collect(verbose=False)
        extract.extract_pending(verbose=False)

    clusters, articles, _ = cluster.build_clusters(verbose=False)
    elig, _ = eligibility.filter_eligible(clusters)
    print(f"\n=== Stage 2: clustering ===")
    print(f"  {len(articles)} articles -> {len(clusters)} clusters -> {len(elig)} eligible")

    print(f"\n=== Stage 3: impact scoring ===", flush=True)
    scored, refusals, errors, u3 = impact.score_all(elig, verbose=False)
    print(f"  scored {len(scored)}/{len(elig)} in {u3['api_calls']} calls "
          f"(refused {len(refusals)}, lost {len(errors)})")
    top, n_qualify = rank.select_top(scored)
    print(f"  {n_qualify} cleared impact floor -> top {len(top)}")
    for i, s in enumerate(top, 1):
        print(f"    {i}. [{s['impact_score_llm']}] {s['headline'][:66]}")

    print(f"\n=== Stage 4: claim extraction + validation ===", flush=True)
    extracted, cfails, u4 = claims.extract_all(top, verbose=False)
    validated = validate.validate_all(extracted)
    tot = validate.summarise(validated)
    print(f"  {tot['claims_in']} claims -> {tot['published']} published, "
          f"{tot['withheld']} withheld, {tot['contradictions']} contradictions")

    print(f"\n=== Stage 5: synthesis ===", flush=True)
    summaries, skipped, sfails, u5 = synthesize.synthesize_all(validated)

    print(f"\n=== DIGEST ({len(summaries)} stories) ===\n")
    for i, s in enumerate(summaries, 1):
        print(f"{i}. {s['title']}")
        print(f"   WHAT HAPPENED: {s['what_happened']}")
        print(f"   IMPACT: {s['impact']}")
        if s["whats_next"]:
            print(f"   WHAT'S NEXT: {s['whats_next']}")
        print(f"   SOURCES: {', '.join(sorted({x['name'] for x in s['sources']}))}")
        if s["unsupported_numbers"]:
            print(f"   !! UNSUPPORTED NUMBERS: {s['unsupported_numbers']}")
        print()

    calls = u3["api_calls"] + u4["api_calls"] + u5["api_calls"]
    print(f"=== run summary ===")
    print(f"  API calls    : {llm.call_count()} (budget {config.MAX_CALLS_PER_RUN})")
    print(f"  tokens       : {u3['input']+u4['input']+u5['input']:,} in / "
          f"{u3['output']+u4['output']+u5['output']:,} out")
    print(f"  cost         : ${u3['cost_usd']+u4['cost_usd']+u5['cost_usd']:.4f}")
    print(f"  skipped thin : {len(skipped)}  {[s['headline'][:40] for s in skipped]}")
    print(f"  exhausted    : {llm.exhausted_models()}")

    # Persist the claim lists next to the digest so the number check and any
    # later eval can be re-run offline, without spending API quota.
    with open(config.PROJECT_ROOT / "data" / "digest_latest.json", "w") as f:
        json.dump({"stories": summaries, "skipped": skipped,
                   "calls": llm.call_count(),
                   "cost_usd": u3["cost_usd"]+u4["cost_usd"]+u5["cost_usd"],
                   "evidence": [{
                       "headline": v["story"]["headline"],
                       "published_claims": v["published_claims"],
                       "withheld_claims": v["withheld_claims"],
                       "contradictions": v["validated_contradictions"],
                       "what_happened": v["what_happened"],
                       "whats_next": v["whats_next"],
                   } for v in validated]},
                  f, indent=2, ensure_ascii=False)
    print("\nwrote data/digest_latest.json")

    # ---- Stage 6: render + deliver -------------------------------------
    subject, html, text = render.render(summaries)
    print("\n=== Stage 6: delivery ===")
    print(f"  subject: {subject}")
    try:
        # Dry run writes the email to disk instead of sending, so layout can be
        # checked without credentials. Pass --send to actually deliver.
        result = deliver.send(subject, html, text, len(summaries),
                              dry_run="--send" not in sys.argv)
        print(f"  {result}")
    except deliver.DeliveryError as exc:
        print(f"  DELIVERY FAILED: {exc}")


if __name__ == "__main__":
    main(skip_collect="--skip-collect" in sys.argv)
