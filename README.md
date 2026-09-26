# Morning News Digest

Reads three Indian news sites, works out which stories matter, checks the facts
across sources, and emails five short summaries every morning. Runs unattended
on GitHub Actions. Costs nothing.

```
494 articles → 365 clusters → 41 eligible → 5 stories, in your inbox by 7am IST
```

---

## The idea

Most news reaches you one outlet at a time, and you have no easy way to tell a
consequential story from a heavily-covered one. This reads three papers at once,
groups their coverage of the same events, and only sends you a story when **at
least two of the three independently thought it was worth reporting**.

That two-source rule does a lot of work. It filters ~90% of clusters, cuts the
cost of the AI stages by roughly 8×, and — because three newsrooms making the
same call is evidence of significance — it turns out to be a decent relevance
signal as well as a cheap one.

## How it works

Nine stages. **Six of them use no AI at all.**

| # | Stage | AI? | What it does |
|---|-------|-----|--------------|
| 1 | Collect | no | Reads six RSS feeds, records anything new |
| 2 | Extract | no | Fetches each article, strips nav and ads |
| 3 | Embed | **yes** | Turns each headline + lede into 384 numbers representing meaning |
| 4 | Cluster | no | Groups articles whose meanings are close enough |
| 5 | Gate | no | Drops any story not carried by 2+ distinct outlets |
| 6 | Score | **yes** | Rates each surviving story 0–3 for real-world impact |
| 7 | Rank | no | Sorts by impact, source coverage, recency. Takes five |
| 8 | Claims | **yes** | Reads the full articles, reports which outlet said what |
| 9 | Validate → Write → Send | mixed | Code decides what may be published; a model writes it; code sends it |

Two design decisions carry most of the weight:

**The free filter runs before the paid one.** Stage 5 is plain Python and costs
nothing. Running it before Stage 6 takes ~365 clusters down to ~41, so we pay to
analyse 41 stories instead of 365 — for an identical digest, since every story
it skips was single-source and could never have been selected.

**The writer never sees the articles.** Stage 9 hands the model the *validated
claim list* and nothing else. It cannot repeat a fact it was never shown, which
makes invention structurally hard rather than merely discouraged. The
corroboration rule, the contradiction rule and the never-average guardrail all
live in Python, not in a prompt — a rule in a prompt is a request, a rule in
code is a rule.

## Running it

```bash
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt

cp .env.example .env        # then fill in the keys

./.venv/bin/python run_pipeline.py                  # build, write a preview file
./.venv/bin/python run_pipeline.py --skip-collect   # reuse stored articles
./.venv/bin/python run_pipeline.py --send           # actually email it
```

Without `--send` it writes `data/digest_preview.html`, so you can iterate on
wording and layout without filling your inbox.

### Keys

| Variable | What for | Free? |
|---|---|---|
| `GEMINI_API_KEY` | The three AI stages | Yes — aistudio.google.com |
| `RESEND_API_KEY` | Sending the email | Yes — resend.com |
| `DIGEST_TO` | Who receives it | — |

Nothing is stored in code. `.env` is gitignored; in production the values live
in GitHub Secrets.

## Scheduling

Two workflows, and the split matters.

```
.github/workflows/collect.yml   every 3 hours    fetch → extract → cache
.github/workflows/digest.yml    23:47 UTC daily  build → grade → email
```

**Collection is separate and frequent because RSS feeds forget.** A feed holds
a fixed number of items and older ones fall off permanently — The Hindu's holds
about three hours of output at peak. A single morning fetch would miss most of
the previous day, and stories would fail the two-source rule on a technicality
rather than on merit.

**The digest fires ~100 minutes early on purpose.** GitHub runs scheduled jobs
on spare shared capacity with no timing guarantee; a 01:30 schedule was measured
firing at 06:19. Firing early absorbs a typical delay. If an exact arrival time
ever matters, GitHub Actions is the wrong scheduler.

Full deployment steps are in [DEPLOY.md](DEPLOY.md).

## Knowing whether it's any good

Three checks, each blind to what the others catch.

**Regression test** — `evals/run_regression.py`. Clustering against 120
hand-labelled articles. The data is frozen inside the eval file, so the score
only moves when the code does. Runs in CI on every change, free, deterministic.

```
found 39 of 40 pairs · missed 1 · wrong 0 · F1 0.987
```

**Health metrics** — `digest/metrics.py`. Fifteen numbers from each morning's
real run, checked against bars *and* against their own 7-day average. The bar
catches a cliff; the trailing comparison catches a slide, which is what a source
going stale actually looks like.

**Summary grader** — `digest/judge.py`. A model scores each story against the
claims it was built from, on the same rubric a human would use. It runs on a
different model from the writer, because a model marking its own work is
consistently generous. Validated against human scores before being trusted:
84% within one point, and it independently caught an invented organisation that
a human reviewer had flagged.

When any check fails, a warning appears **at the top of the digest email**,
naming the story and quoting the phrase that caused it.

`evals/FAILURES.md` is the running log of every bad output seen. Real failures
make a better test set than invented ones.

## The knobs worth knowing

All in `digest/config.py`, each carrying the measurement that set it.

| Setting | Now | What it does |
|---|---|---|
| `SIMILARITY_THRESHOLD` | `0.82` | How close two articles must be to count as one story. Measured, not guessed — and meaningless if you change the embedding model |
| `LINKAGE` | `single` | How groups merge |
| `MIN_DISTINCT_SOURCES` | `2` | The gate |
| `MIN_IMPACT_SCORE` | `2` | Floor to enter the digest at all |
| `ATTRIBUTE_SINGLE_SOURCE` | `True` | Whether a fact only one paper reported is dropped, or kept with attribution. A product decision, not a technical one |
| `HEALTH_BARS` | — | When you want to be told something is wrong |

## Known limitations

- **Delivery time isn't guaranteed.** Scheduled cloud jobs run on spare
  capacity. One morning's digest arrived nearly five hours late.
- **Mail lands in Promotions**, and only reaches the Resend account owner, until
  a sending domain is verified. That also blocks sharing it with other readers.
- **Source viability depends on where the code runs.** News18 extracted 5/5
  articles from a laptop and 0/6 from a cloud runner. `probe_sources.py` tests
  candidates from the machine that will actually run them.
- **The free AI tier allows 20 requests per day per model.** The pipeline fails
  over across seven models; one run a day fits with headroom, two or three
  starts exhausting them and later stages fall back to weaker models.
- **The grader is slightly generous** (+0.36 against human scores). When it says
  something is fine, that's weaker evidence than when it says something is
  broken.
- **Sample sizes are small.** Clustering is measured on one labelled day; the
  grader is validated against five human-scored summaries. Both are passes, not
  comfortable ones.

## Layout

```
digest/          the pipeline, one module per stage
evals/           regression test, gold set, judge validation, failure log
.github/         scheduled workflows
run_pipeline.py  the whole thing, end to end
probe_sources.py which news sources are reachable from here
DEPLOY.md        deployment steps
```

Built stage by stage against a written product spec. Every threshold in the
config carries the measurement that produced it, so changing one means beating a
number rather than having a hunch.
