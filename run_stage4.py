"""Run the pipeline through Stage 4: clusters -> top 5 -> claims -> validation."""

import json

from digest import (claims, cluster, config, eligibility, impact, rank,
                    validate)


def main():
    clusters, articles, _ = cluster.build_clusters(verbose=False)
    elig, _ = eligibility.filter_eligible(clusters)
    print(f"=== Stages 1-2 ===\n  {len(articles)} articles -> {len(clusters)} clusters "
          f"-> {len(elig)} eligible\n")

    print(f"=== Stage 3: impact scoring "
          f"({config.LLM_MODELS[config.LLM_PROVIDER]}) ===", flush=True)
    scored, refusals, errors, u3 = impact.score_all(elig)
    print(f"\n  scored {len(scored)}/{len(elig)} in {u3['api_calls']} calls "
          f"| refusals {len(refusals)} | lost {len(errors)}")

    top, n_qualify = rank.select_top(scored)
    print(f"  {n_qualify} cleared impact floor; taking top {len(top)}\n")
    for i, s in enumerate(top, 1):
        print(f"  {i}. [{s['impact_score_llm']}] {s['headline'][:70]}")

    print(f"\n=== Stage 4a: claim extraction (full article text) ===", flush=True)
    extracted, fails, u4 = claims.extract_all(top)

    print(f"\n=== Stage 4b: validation (no AI — PRD sections 11, 12) ===")
    validated = validate.validate_all(extracted)
    tot = validate.summarise(validated)
    for k, v in tot.items():
        print(f"  {k:26s}: {v}")

    print(f"\n=== cost ===")
    print(f"  Stage 3: {u3['input']:,} in / {u3['output']:,} out -> ${u3['cost_usd']:.4f}")
    print(f"  Stage 4: {u4['input']:,} in / {u4['output']:,} out -> ${u4['cost_usd']:.4f}")
    print(f"  TOTAL API calls today: {u3['api_calls'] + u4['api_calls']}")

    with open(config.PROJECT_ROOT / "data" / "stage4_output.json", "w") as f:
        json.dump([{
            "headline": v["story"]["headline"],
            "impact_score": v["story"]["impact_score_llm"],
            "sources": v["story"]["cluster"]["sources"],
            "what_happened": v["what_happened"],
            "whats_next": v["whats_next"],
            "published_claims": v["published_claims"],
            "withheld_claims": v["withheld_claims"],
            "contradictions": v["validated_contradictions"],
            "stats": v["stats"],
            "urls": [a["url"] for a in v["story"]["cluster"]["articles"]],
        } for v in validated], f, indent=2, ensure_ascii=False)
    print("\nwrote data/stage4_output.json")


if __name__ == "__main__":
    main()
