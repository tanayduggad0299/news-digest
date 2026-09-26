# Failure log

Every bad output, with one line on what was wrong. This is the raw material
for the eval set — real failures test what actually breaks, invented test
cases only test what we already thought of.

Format: date · story · what was wrong · status

---

**2026-09-26 · "Protests and Legal Actions Target CEC Gyanesh Kumar"** ·
Human review scored it 2/5 on invention and marked it UNACCEPTABLE. The
"what's next" named a "Cockroach Janata Party" that appears in none of the
13 verified claims. Root cause: `whats_next` travelled from extraction
straight into the digest without passing the corroboration gate — it carried
no digits so the number-checker ignored it, and it was not in `claims` so the
validation layer never saw it. · FIXED: whats_next now carries source
attribution and goes through the same gate; `what_happened` is marked to the
writer as context, not a source of facts.

**2026-09-24 · "US Sanctions to Ground Iranian Airlines"** · The "what's next"
described a Trump–Xi summit — a different story that happened to be mentioned
in the same articles. The field picks up any forward-looking claim in the
cluster rather than one about this event. · OPEN

**2026-09-23 · "IRGC warns of advanced weapons"** · Wrote "the US spent a
specific amount on munitions" — a contentless placeholder instead of the
figure. Worse than omitting the fact. · FIXED: prompt now says omit the fact
entirely rather than write a vague placeholder.

**2026-09-22 · story 2 (Xi Jinping visit)** · "What happened" was a
five-sentence run-on chaining several loosely related developments. · FIXED:
word ceilings and a core-event instruction added to the synthesis prompt.
