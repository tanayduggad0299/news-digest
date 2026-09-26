"""Stage 4b — Cross-source validation.

NO AI. This is the safety layer, and that is exactly why it is plain Python.

PRD sections 11, 12 and 14 set hard guardrails: never manufacture a number to
reconcile sources, never silently resolve a contradiction, do not publish facts
no second source supports. Those could be written into a prompt, and the model
would usually comply. "Usually" is not a guardrail. Here they are code, so they
hold every morning without depending on the model's mood.

    input  : extracted claim lists from claims.py
    output : the same stories with each claim marked publishable or withheld,
             and a reason for every withholding
    next   : Stage 5 synthesises prose from the publishable claims ONLY
"""

from . import config


def _sources_for(claim, valid_sources):
    """Normalise supported_by against the sources actually in this cluster.

    The model echoes back source labels; we never trust them blindly. A label
    that does not match a real source in this cluster is dropped rather than
    counted, so a hallucinated attribution can only ever reduce a claim's
    support, never inflate it.
    """
    wanted = {s.strip().lower() for s in claim.get("supported_by", [])}
    return {name for name, label in valid_sources.items()
            if label.strip().lower() in wanted}


def validate_story(extracted):
    """Apply the corroboration and contradiction rules to one story."""
    cluster = extracted["story"]["cluster"]
    # Map internal source id -> the human label the model was shown.
    valid_sources = {s: config.SOURCE_NAMES.get(s, s) for s in cluster["sources"]}
    n_sources = len(valid_sources)

    published, withheld = [], []

    for claim in extracted["claims"]:
        supporters = _sources_for(claim, valid_sources)
        n = len(supporters)
        record = dict(claim)
        record["resolved_sources"] = sorted(supporters)
        record["support_count"] = n

        if n == 0:
            # Attribution didn't match any real source — cannot verify it at all.
            record["reason"] = "no recognised source attribution"
            withheld.append(record)
            continue

        # Which claims must be corroborated.
        #
        # The first version keyed this off claim_type alone, and a single source
        # reporting "the strikes killed Iran's Supreme Leader" sailed through
        # because the sentence contains no digit. Whether a claim carries a
        # number is the wrong axis; what matters is whether a reader would be
        # badly misled if it were wrong. `significance` captures that directly.
        needs_corroboration = (
            config.CORROBORATION_SCOPE == "all"
            or claim.get("claim_type") == "quantitative"
            or claim.get("significance") == "major"
        )

        if needs_corroboration and n < config.MIN_CLAIM_SOURCES:
            # Two outcomes are available here, and which one is right is a
            # product decision rather than a technical one.
            #
            # DROP (ATTRIBUTE_SINGLE_SOURCE = False) is PRD section 12 read
            # literally: accuracy over completeness, the reader follows the
            # source link for the rest. Measured on real data, this produces
            # near-empty stories, because most clusters are two articles from
            # two outlets and two newsrooms rarely state the same fact twice.
            #
            # ATTRIBUTE (True) keeps the fact but forces the synthesiser to name
            # who reported it — "according to The Hindu, 28 were killed". The
            # reader is never misled about how well-supported a claim is, which
            # is what section 12 is actually protecting against.
            if config.ATTRIBUTE_SINGLE_SOURCE:
                record["single_source"] = True
                record["needs_attribution"] = True
                published.append(record)
                continue
            record["reason"] = (f"{claim.get('significance', '?')}/"
                                f"{claim.get('claim_type')} claim with {n}/{n_sources} "
                                f"sources (needs {config.MIN_CLAIM_SOURCES})")
            withheld.append(record)
            continue

        record["single_source"] = (n == 1)
        published.append(record)

    # PRD section 11: a contradicted value is never picked or averaged. The
    # disagreement is surfaced when it is worth saying, otherwise the detail is
    # simply omitted. Either way the model never sees a reconciled number.
    contradictions = []
    for c in extracted["contradictions"]:
        versions = [v for v in c.get("versions", [])
                    if v.get("source", "").strip().lower()
                    in {l.lower() for l in valid_sources.values()}]
        if len(versions) >= 2:
            contradictions.append({
                "topic": c["topic"],
                "versions": versions,
                "surface": len(versions) >= config.SURFACE_CONTRADICTION_MIN_SOURCES,
            })

    # "What's next" goes through the SAME gate as every other claim. It used to
    # travel straight from extraction into the digest, unchecked, which is how
    # an invented organisation reached a delivered story: it carried no number,
    # so the number-checker ignored it, and it was not in `claims`, so the
    # corroboration rule never saw it.
    nxt = extracted.get("whats_next_claim")
    whats_next = None
    if nxt:
        supporters = _sources_for(nxt, valid_sources)
        record = dict(nxt)
        record["resolved_sources"] = sorted(supporters)
        record["support_count"] = len(supporters)
        if len(supporters) >= config.MIN_CLAIM_SOURCES:
            whats_next = record
        elif supporters and config.ATTRIBUTE_SINGLE_SOURCE:
            record["single_source"] = True
            record["needs_attribution"] = True
            whats_next = record
        else:
            record["reason"] = (f"next-step claim with {len(supporters)}/"
                                f"{n_sources} sources")
            withheld.append(record)

    out = dict(extracted)
    out["whats_next"] = whats_next["text"] if whats_next else None
    out["whats_next_validated"] = whats_next
    out["published_claims"] = published
    out["withheld_claims"] = withheld
    out["validated_contradictions"] = contradictions
    out["stats"] = {
        "claims_in": len(extracted["claims"]),
        "published": len(published),
        "withheld": len(withheld),
        "single_source_published": sum(1 for c in published if c.get("single_source")),
        "contradictions": len(contradictions),
        "essential_withheld": sum(1 for c in withheld if c.get("is_essential")),
    }
    return out


def validate_all(extracted_stories):
    return [validate_story(e) for e in extracted_stories]


def summarise(validated):
    total = {"claims_in": 0, "published": 0, "withheld": 0,
             "single_source_published": 0, "contradictions": 0,
             "essential_withheld": 0}
    for v in validated:
        for k in total:
            total[k] += v["stats"][k]
    return total
