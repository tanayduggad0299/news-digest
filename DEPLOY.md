# Running the digest daily

Nothing is scheduled until you do the steps below. They take about ten minutes.

## Why it runs on GitHub rather than your Mac

The obvious option is a cron job on your laptop. It does not work in practice:
your Mac is asleep at 7 AM, and a sleeping machine runs nothing. You would wake
up to a digest only on the mornings you happened to leave the lid open.

GitHub Actions runs in the cloud on a schedule, free for this workload, with
secret storage and a log of every run. Your laptop can be anywhere.

## Two schedules, not one

    collect.yml   every 3 hours    fetch feeds, extract text, save to the database
    digest.yml    07:00 IST daily  cluster, score, verify, write, email

Collection is separate and frequent for a specific reason. The Hindu's RSS feed
holds only 60 items, which is roughly three hours of their output. A single
fetch at 7 AM would see the night and miss the whole previous day, and most
stories would then fail the two-source rule because one outlet's coverage was
simply absent. So we accumulate through the day, and the 7 AM job reads the last
24 hours from the accumulated database.

## Setup

**1. Create a private GitHub repository.** Private matters: the database
contains the full text of every article collected.

**2. Push this project.**

    cd "news-digest"
    git init
    git add .
    git commit -m "Morning news digest"
    git branch -M main
    git remote add origin git@github.com:<you>/news-digest.git
    git push -u origin main

Check that `.env` did NOT get committed — `git ls-files | grep env` must return
nothing. Secrets belong in GitHub Secrets, never in the repository.

**3. Add the secrets.** Repository → Settings → Secrets and variables →
Actions → New repository secret:

    GEMINI_API_KEY    your Google AI Studio key
    RESEND_API_KEY    your Resend key
    DIGEST_TO         the address to send to

**4. Allow the workflows to write.** Settings → Actions → General → Workflow
permissions → **Read and write permissions**. Without this the "persist the
database" step fails and collection never accumulates.

**5. Test before trusting it.** Actions tab → `collect` → Run workflow. When it
goes green, run `digest` the same way. Only then wait for a real morning.

## What to expect

Scheduled runs on GitHub are queued, not guaranteed to the second, and can be
delayed by several minutes under load. Treat 7 AM as "shortly after 7".

GitHub disables scheduled workflows in repositories with no activity for 60
days. Since the collector commits the database several times a day, this repo
stays active on its own.

## When a morning is quiet

Check the Actions tab first — a red run tells you where it stopped.

Every run uploads `digest_latest.json` and `digest_preview.html` as artifacts,
kept 14 days, even when the job fails. So you can always see what the pipeline
produced, which separates "the digest was never built" from "it was built but
the email never arrived".

Failed sends are also recorded in the `sends` table in the database, with the
error text.

## The quota ceiling

A full run costs roughly 23 Gemini requests. The free tier allows 20 per day
*per model*, and the pipeline fails over down a chain of seven models, so one
run a day fits with room to spare. Two or three runs in a day will start
exhausting models, which is why the code enforces `MAX_CALLS_PER_RUN`.

If the digest ever arrives noticeably worse, check the run log for `exhausted`.
It means the later stages were written by weaker fallback models.
