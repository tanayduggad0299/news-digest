"""Configuration for the morning news digest."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "digest.db"

# The three predefined sources, each covering India + World.
# "source" is the outlet — the eligibility rule in PRD section 8 counts
# distinct outlets, so the two feeds below for one outlet are ONE source.
FEEDS = [
    ("the_hindu", "india", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("the_hindu", "world", "https://www.thehindu.com/news/international/feeder/default.rss"),
    ("times_of_india", "india", "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"),
    ("times_of_india", "world", "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms"),
    ("hindustan_times", "india", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    ("hindustan_times", "world", "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml"),
]

SOURCE_NAMES = {
    "the_hindu": "The Hindu",
    "times_of_india": "The Times of India",
    "hindustan_times": "Hindustan Times",
}

# Sources measured and rejected, so we don't retry them later:
#   indian_express — feed works but only ~27 articles/24h, too thin to help
#                    the 2-source eligibility rule.
#   ndtv           — feed works, but article pages return HTTP 403 to
#                    automated clients. A deliberate block; we respect it.
#   news18         — worked perfectly from a home connection (5/5 extraction)
#                    and is BLOCKED from datacentre IPs: 0/6 on a GitHub
#                    Actions runner, 130 of 167 real articles refused with 403.
#                    Source viability is a property of WHERE THE CODE RUNS, not
#                    of the source alone. Measured with probe_sources.py.
#   firstpost      — feed is mostly weeks-stale.
#   theprint / scroll / deccanherald — feed URLs 301/404.
#   indian_express — the FEED itself now 403s from a datacentre IP too.
#
# Verified reachable from a GitHub runner (probe_sources.py):
#   the_hindu, hindustan_times, times_of_india, india_today,
#   livemint, economic_times. The last two are business-led and would skew
#   the digest toward markets, so Times of India takes the third slot.

# The digest considers articles published within this many hours of the run.
LOOKBACK_HOURS = 24

# The Hindu's feeds hold only 60 items, which is 2-5 hours of their output,
# so a single daily fetch cannot see a full day. Collection therefore runs
# every few hours and accumulates into SQLite; the 7 AM digest reads the
# accumulated window rather than hitting the feeds once.
COLLECT_INTERVAL_HOURS = 3

USER_AGENT = "Mozilla/5.0 (compatible; MorningDigest/0.1; personal reader)"
FETCH_TIMEOUT_SECONDS = 30
POLITE_DELAY_SECONDS = 1.0

# Articles shorter than this after extraction are treated as failures —
# usually a paywall interstitial, a photo gallery, or a live-blog shell.
MIN_BODY_WORDS = 120

# Feeds mix in video, photo-gallery and web-story pages. These have almost no
# body text, so they cannot be fact-checked or synthesised — drop at collection.
EXCLUDE_URL_PATTERNS = (
    "/videos/", "/video/", "/photogallery/", "/photos/",
    "/web-stories/", "/webstories/", "/live-blog/", "/liveblog/",
)

# Outlets append their own name to RSS titles. Left in, this text is embedded
# in Stage 2 and makes articles from the same outlet look more alike.
TITLE_SUFFIXES = (
    " | Times of India", " - Times of India", " | Hindustan Times", " - Hindustan Times",
    " | The Hindu", " - The Hindu",
)

# Navigation crumbs trafilatura sometimes keeps as the first line.
BODY_JUNK_PREFIXES = ("homevideos", "home videos", "advertisement", "home")

# ---------------------------------------------------------------- Stage 2 --

# "auto" uses Voyage when VOYAGE_API_KEY is set, else the local model.
EMBED_PROVIDER = "auto"
LOCAL_EMBED_MODEL = "BAAI/bge-small-en-v1.5"   # 384 dims, ~130MB, CPU-friendly
VOYAGE_EMBED_MODEL = "voyage-4"                # 1024 dims, hosted

# How much of the article body joins the headline in the embedded text.
LEDE_WORDS = 120

# PRD section 7 proposes 0.80 as an explicit starting hypothesis, to be tuned
# after observing real clustering errors. This value is model-specific: each
# embedding model has its own similarity distribution, so changing
# EMBED_PROVIDER requires re-running evals/eval_clustering.py.
#
# MEASURED against 115 hand-labelled articles (evals/clustering_gold.jsonl),
# clustering the full 410-article day exactly as production does:
#
#     linkage   thr    precision  recall    F1
#     single    0.80     0.830     0.967   0.893
#     single    0.82     0.942     0.942   0.942   <- selected
#     single    0.84     0.931     0.785   0.852
#     average   0.80     0.980     0.793   0.877
#     average   0.84     0.986     0.595   0.742   <- what we shipped on a guess
#
# The earlier choice of average-linkage was made by eyeballing ONE symptom —
# a 31-article cluster at threshold 0.80 — and treating it as proof that
# transitive grouping was the problem. The eval says otherwise: that blob was a
# THRESHOLD problem, and raising 0.80 to 0.82 fixes it while keeping
# single-linkage's much better recall. Average-linkage at 0.84 was missing 40%
# of the pairs it should have merged, silently splitting real stories such as
# the Pakistan airstrikes and the Bihar assault case across two clusters.
SIMILARITY_THRESHOLD = 0.82

# "single"  = PRD section 7 as written: one qualifying pair merges two groups.
# "average" = cluster-level validation; merges only when the average cross-pair
#             similarity clears the threshold. Kept available, and it wins on
#             precision, but it costs far too much recall at any threshold that
#             keeps its precision advantage.
LINKAGE = "single"

# ---------------------------------------------------------------- Stage 3 --

MIN_DISTINCT_SOURCES = 2        # PRD section 8: 2 of the 3 outlets must cover it

# Which model provider runs the LLM stages. One-line switch.
#   "gemini"    — free tier, rate limited, Google may train on the data
#   "anthropic" — paid per token, strongest at following negative constraints
LLM_PROVIDER = "gemini"

LLM_MODELS = {
    "gemini": "gemini-3.8-flash",      # quota resets daily; 3.6/3.7 are fallbacks
    "anthropic": "claude-opus-5",
}

# USD per 1M tokens, per provider. Gemini's free tier bills nothing; the zeros
# keep the cost accounting code uniform across providers.
LLM_PRICING = {
    "gemini":    {"in": 0.0, "out": 0.0},
    "anthropic": {"in": 5.0, "out": 25.0},
}

# The free tier allows ~10 requests/minute and returns HTTP 503 under load
# often enough that retries are mandatory, not defensive.
LLM_MAX_RETRIES = 2            # transient only; quota errors are not retried
LLM_BACKOFF_SECONDS = 6         # 503s are load-related; waiting longer beats
                                # retrying fast and spending the daily quota
# Hard ceiling on HTTP attempts per pipeline run — a runaway guard, NOT a rate
# limiter. It counts every ATTEMPT, including retries and failover hops, so one
# logical call can consume up to (models in chain) x (retries + 1) attempts when
# the free tier is returning 503s.
#
# It was set to 40 and became a binding constraint instead of a safety net: a
# flaky run spent the whole allowance before synthesis and emailed "no major
# stories" on a day when 17 stories qualified. A false negative is worse than no
# guard at all, because it looks like a quiet news day rather than a bug.
MAX_CALLS_PER_RUN = 150

# Attempts held back so the last stage cannot be starved by earlier ones.
# Synthesis is what the reader actually receives; losing it wastes every call
# already spent.
RESERVED_FOR_SYNTHESIS = 40
LLM_REQUEST_SPACING = 4.0       # seconds between calls; retry logic absorbs 429s

# Impact scoring sees title + lede only. Full article text costs ~5x more and
# is not needed to judge how many people an event affects; Stage 4 sends the
# full text, but only for the five stories that survive ranking.
IMPACT_LEDE_WORDS = 110
IMPACT_MAX_ARTICLES = 4         # cap per cluster; extra articles add cost, not signal
# Stories per LLM call. Raised from 8 after a run turned 34 clusters into 27
# API calls: every 503 triggers retries and a batch split, so the call count
# scales with batch COUNT, not story count. Bigger batches shrink the surface
# that flakiness can amplify. 34 stories now cost 3 calls, not 5-and-splits.
IMPACT_BATCH_SIZE = 16
IMPACT_MAX_TOKENS = 8000        # must cover 8 assessments, not 1
IMPACT_EFFORT = "medium"        # scoring against a 4-point rubric is judgment,
                                # not hard reasoning; "high" costs more for
                                # little gain here. Tune against the eval set.

TOP_N_STORIES = 5               # PRD section 9

# Which of the two PRD section 8 approaches drives ranking. Both scores are
# always computed and logged; this only picks the one that orders the digest.
#   "impact_score_llm"     — Approach 1, the model scores directly
#   "impact_score_derived" — Approach 2, code scores from extracted indicators
RANKING_SCORE_FIELD = "impact_score_llm"

# A story must clear this to enter the ranking at all (PRD section 8:
# "meaningful real-world impact"). Prevents a slow news day promoting filler.
MIN_IMPACT_SCORE = 2

# ---------------------------------------------------------------- Stage 4 --

CLAIMS_MAX_ARTICLES = 4          # one per source first, then fill
CLAIMS_MAX_WORDS_PER_ARTICLE = 700
CLAIMS_MAX_TOKENS = 8000

# How strictly PRD section 12 ("unsupported information") is read.
#
# This resolves the tension I flagged between section 12 (exclude facts only one
# source reports) and section 14 (do not omit major facts needed to understand
# the story). At exactly 2 sources — the common case — requiring corroboration
# for EVERYTHING strips almost every detail and leaves three-sentence stories.
#
#   "quantitative" — corroborate numbers, amounts, dates, counts. Allow
#                    single-source descriptive context, flagged as such.
#                    This is the default and prioritises usefulness.
#   "all"          — corroborate every claim. Maximum accuracy, thinner digest.
#
# PM decision, not a technical one. Change this line to switch.
CORROBORATION_SCOPE = "quantitative"

MIN_CLAIM_SOURCES = 2            # sources needed for a corroboration-required claim
SURFACE_CONTRADICTION_MIN_SOURCES = 2   # below this, omit rather than surface

# Failover order when a model's DAILY free quota runs out mid-run. Each Gemini
# model carries its own separate 20/day allowance, so moving down this chain
# genuinely buys more capacity rather than just retrying into the same wall.
LLM_MODEL_CHAIN = {
    # Ordered strongest-first. The lite models are weaker but carry their own
    # separate daily quotas, so they are real reserve capacity rather than a
    # retry into the same wall.
    "gemini": ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash",
               "gemini-3-flash-preview", "gemini-3.1-flash-lite",
               "gemini-3.5-flash-lite", "gemini-flash-lite-latest"],
    "anthropic": ["claude-opus-5"],
}

# ---------------------------------------------------------------- Stage 5 --

SYNTH_MAX_TOKENS = 2000

# How a single-source fact is handled when it survives validation.
#   False — PRD section 12 as written: it never reaches the synthesiser.
#           Accuracy over completeness; the reader can follow the source link.
#   True  — it reaches the synthesiser labelled, and must be written with
#           inline attribution ("according to The Hindu, ..."). Keeps thin
#           two-source stories readable at the cost of some unverified detail.
# Flip this after reading a few real digests. It is a PM call, not a technical one.
ATTRIBUTE_SINGLE_SOURCE = True

# A story with fewer verified claims than this cannot be written honestly, so
# it is dropped rather than padded (PRD section 9: send fewer, never fill).
MIN_PUBLISHABLE_CLAIMS = 3

# ---------------------------------------------------------------- Stage 6 --

DIGEST_TIMEZONE = "Asia/Kolkata"     # the 7 AM that matters is IST

# "resend" (API, free 3k/month, better deliverability) or "smtp" (Gmail app
# password, no signup). Credentials live in .env, never here.
EMAIL_PROVIDER = "resend"
DIGEST_TO = ""                       # override with DIGEST_TO in .env
DIGEST_FROM = "Morning Digest <onboarding@resend.dev>"

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
