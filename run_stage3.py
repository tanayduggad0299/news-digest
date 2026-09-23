"""Run Stage 3 end to end: cluster -> eligibility -> impact -> rank -> top 5."""

import json

from digest import cluster, config, eligibility, impact, rank


def main():
    clusters, articles, _ = cluster.build_clusters(verbose=False)
    elig, rej = eligibility.filter_eligible(clusters)
    s = eligibility.summarise(clusters, elig, rej)

    print("=== Stage 2 recap ===")
    print(f"  {len(articles)} articles -> {s['clusters_in']} clusters")
    print("=== Stage 3a: eligibility gate (no AI, no cost) ===")
    print(f"  eligible (2+ sources): {s['eligible']}")
    print(f"  rejected (1 source)  : {s['rejected_single_source']}")
    print(f"\n=== Stage 3b: impact scoring "
          f"({config.LLM_PROVIDER} / {config.LLM_MODELS[config.LLM_PROVIDER]}) ===")

    scored, refusals, errors, usage = impact.score_all(elig)

    print(f"\n  scored   : {len(scored)}/{len(elig)}")
    print(f"  refusals : {len(refusals)}")
    print(f"  errors   : {len(errors)}")
    print(f"  tokens   : {usage['input']:,} in / {usage['output']:,} out")
    print(f"  COST     : ${usage['cost_usd']:.4f}")

    agree = sum(1 for x in scored
                if x["impact_score_llm"] == x["impact_score_derived"])
    print(f"\n=== PRD section 8: the two approaches compared ===")
    print(f"  agree    : {agree}/{len(scored)} ({agree/max(len(scored),1)*100:.0f}%)")
    for field, label in (("impact_score_llm", "Approach 1 (LLM direct)"),
                         ("impact_score_derived", "Approach 2 (code from indicators)")):
        dist = {k: sum(1 for x in scored if x[field] == k) for k in (3, 2, 1, 0)}
        print(f"  {label:34s} 3:{dist[3]:2d}  2:{dist[2]:2d}  1:{dist[1]:2d}  0:{dist[0]:2d}")

    top, n_qualify = rank.select_top(scored)
    print(f"\n=== Stage 3c: ranking (on {config.RANKING_SCORE_FIELD}, "
          f"floor {config.MIN_IMPACT_SCORE}) ===")
    print(f"  {n_qualify} stories cleared the impact floor; taking top {len(top)}\n")
    for i, s_ in enumerate(top, 1):
        c = s_["cluster"]
        print(f"  {i}. [impact {s_['impact_score_llm']}] {s_['headline'][:74]}")
        print(f"     {c['size']} articles / {c['source_count']} sources: {', '.join(c['sources'])}")
        print(f"     {s_['reasoning'][:110]}")
        print()

    with open(config.PROJECT_ROOT / "data" / "stage3_output.json", "w") as f:
        json.dump({
            "usage": usage, "refusals": refusals, "errors": errors,
            "scored": [{k: v for k, v in x.items() if k != "cluster"} | {
                "sources": x["cluster"]["sources"],
                "titles": [a["title"] for a in x["cluster"]["articles"]],
            } for x in scored],
            "top": [x["headline"] for x in top],
        }, f, indent=2, ensure_ascii=False)
    print(f"wrote data/stage3_output.json")


if __name__ == "__main__":
    main()
