"""Daily health metrics — catching the failures an eval cannot.

The regression test in evals/ measures clustering against a frozen answer key.
It is deterministic and it never changes, which is its whole value — and also
its blind spot: it says nothing about what happened THIS MORNING.

Production fails in ways a fixed test cannot see. A news source starts refusing
our requests. A feed goes empty. A model's daily quota runs out halfway through.
Extraction quietly drops from 98% to 60%. None of that changes the answer key;
all of it wrecks the digest.

So each run records what it actually did, and the numbers are checked against
bars. The bars are a product decision, in config.HEALTH_BARS.

WHY THE TRAILING COMPARISON MATTERS

An absolute bar catches a cliff. It does not catch a slide — eligible clusters
drifting 38, 34, 29, 25 over a fortnight is invisible to a "must exceed 10"
rule, and is exactly what a source going stale looks like. So every metric is
also compared with its own recent history.
"""

import json
from datetime import datetime, timezone

from . import config, store

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_at   TEXT PRIMARY KEY,
    ok       INTEGER NOT NULL,
    breaches TEXT,
    payload  TEXT NOT NULL
);
"""

# Which direction is bad, per metric. Everything here is "higher is better"
# unless listed in LOWER_IS_BETTER.
LOWER_IS_BETTER = {"unsupported_numbers", "api_failures", "refusals",
                   "models_exhausted", "empty_feeds", "judge_unacceptable"}


def _conn():
    conn = store.connect()
    conn.executescript(SCHEMA)
    return conn


def record(payload):
    """Store one run's numbers and return (ok, breaches, trends)."""
    breaches = check_bars(payload)
    trends = check_trends(payload)
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO runs (run_at, ok, breaches, payload) VALUES (?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), 0 if breaches else 1,
         json.dumps(breaches), json.dumps(payload)),
    )
    conn.commit()
    conn.close()
    return (not breaches), breaches, trends


def check_bars(payload):
    """Absolute limits. Returns a list of human-readable breach descriptions."""
    out = []
    for name, bar in config.HEALTH_BARS.items():
        if name not in payload:
            continue
        got = payload[name]
        if name in LOWER_IS_BETTER:
            if got > bar:
                out.append(f"{name} is {got}, above the limit of {bar}")
        elif got < bar:
            out.append(f"{name} is {got}, below the floor of {bar}")
    return out


def history(limit=14):
    conn = _conn()
    rows = conn.execute("SELECT * FROM runs ORDER BY run_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [{"run_at": r["run_at"], "ok": r["ok"], **json.loads(r["payload"])}
            for r in rows]


def check_trends(payload, window=7, slide=0.4):
    """Flag any metric that has slid well below its own recent average.

    Catches the slow failures a fixed floor misses: a source going stale, a
    feed thinning out, extraction degrading as a site changes its markup.
    """
    past = history(limit=window + 1)[1:]      # exclude the run being recorded
    if len(past) < 3:
        return []                              # not enough history to judge
    out = []
    for name in config.HEALTH_BARS:
        if name in LOWER_IS_BETTER or name not in payload:
            continue
        values = [p[name] for p in past if isinstance(p.get(name), (int, float))]
        if len(values) < 3:
            continue
        avg = sum(values) / len(values)
        if avg > 0 and payload[name] < avg * (1 - slide):
            out.append(f"{name} is {payload[name]}, "
                       f"{(1 - payload[name] / avg) * 100:.0f}% below its "
                       f"{len(values)}-run average of {avg:.0f}")
    return out


def summarise(payload, breaches, trends):
    """One compact block for the email footer and the run log."""
    lines = []
    status = "OK" if not (breaches or trends) else "NEEDS A LOOK"
    lines.append(f"pipeline health: {status}")
    for b in breaches:
        lines.append(f"  BREACH  {b}")
    for t in trends:
        lines.append(f"  DRIFT   {t}")
    return "\n".join(lines)
