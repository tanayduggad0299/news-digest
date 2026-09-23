"""Stage 6b — Delivery.

No AI. An HTTP POST or an SMTP conversation.

Two providers, because they trade off differently and you may not want to sign
up for anything:

  resend  A transactional email API. Free for 3,000 emails/month. Better
          deliverability, because mail from a reputable sending service is less
          likely to be filed as spam than mail from a home IP.
  smtp    Gmail (or any SMTP server) using an app password. No signup beyond
          what you already have, but Gmail may rate-limit or flag automated
          sending, and a Google account password will not work — 2FA accounts
          need a purpose-generated app password.

Delivery is the step most likely to fail silently in a scheduled job: the run
succeeds, nothing errors, and you simply never get an email. So every send is
verified and recorded, and a failure is loud.
"""

import json
import os
import smtplib
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import EmailMessage

from . import config, store

_RESEND_URL = "https://api.resend.com/emails"


class DeliveryError(RuntimeError):
    pass


def _load_env():
    from dotenv import load_dotenv
    load_dotenv(config.PROJECT_ROOT / ".env")


# ----------------------------------------------------------------- providers --

def _send_resend(subject, html, text, to_addr, from_addr):
    key = os.environ.get("RESEND_API_KEY")
    if not key:
        raise DeliveryError("RESEND_API_KEY not set — add it to news-digest/.env")

    # requests, not urllib. urllib sends "User-Agent: Python-urllib/3.x", which
    # Cloudflare in front of the Resend API rejects outright with a 403 and the
    # body "error code: 1010" — a Cloudflare code, not a Resend one, so it looks
    # like an auth problem when the credentials are perfectly fine.
    import requests

    # List-Unsubscribe is the single cheapest deliverability win for recurring
    # mail. Gmail and Outlook treat its absence on a daily automated send as a
    # spam signal, and its presence as evidence of a legitimate subscription.
    # RFC 8058 wants the One-Click variant alongside it.
    mail_headers = {
        "List-Unsubscribe": f"<mailto:{to_addr}?subject=unsubscribe>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }

    try:
        resp = requests.post(
            _RESEND_URL,
            json={"from": from_addr, "to": [to_addr],
                  "subject": subject, "html": html, "text": text,
                  "headers": mail_headers},
            headers={"Authorization": f"Bearer {key}",
                     "User-Agent": config.USER_AGENT},
            timeout=45,
        )
    except requests.RequestException as exc:
        raise DeliveryError(f"resend request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise DeliveryError(f"resend HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json().get("id", "sent")


def _send_smtp(subject, html, text, to_addr, from_addr):
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    if not (user and password):
        raise DeliveryError("SMTP_USER / SMTP_PASSWORD not set — "
                            "add them to news-digest/.env")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr or user
    msg["To"] = to_addr
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")   # HTML must be added LAST

    try:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=45) as s:
            s.starttls()
            s.login(user, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise DeliveryError(
            f"SMTP auth rejected: {exc.smtp_code} {exc.smtp_error!r}. "
            "A Google account password will not work — generate an app "
            "password at myaccount.google.com/apppasswords.") from exc
    except Exception as exc:
        raise DeliveryError(f"SMTP failed: {type(exc).__name__}: {exc}") from exc
    return "sent"


_PROVIDERS = {"resend": _send_resend, "smtp": _send_smtp}


# ---------------------------------------------------------------- send + log --

SEND_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS sends (
    sent_at   TEXT NOT NULL,
    provider  TEXT NOT NULL,
    recipient TEXT NOT NULL,
    subject   TEXT NOT NULL,
    stories   INTEGER NOT NULL,
    status    TEXT NOT NULL,          -- ok | failed
    detail    TEXT
);
"""


def _log_send(provider, to_addr, subject, n_stories, status, detail):
    conn = store.connect()
    conn.executescript(SEND_LOG_SCHEMA)
    conn.execute(
        "INSERT INTO sends (sent_at, provider, recipient, subject, stories, "
        "status, detail) VALUES (?,?,?,?,?,?,?)",
        (datetime.now(timezone.utc).isoformat(), provider, to_addr, subject,
         n_stories, status, str(detail)[:300]),
    )
    conn.commit()
    conn.close()


def send(subject, html, text, n_stories=0, provider=None, to_addr=None,
         dry_run=False):
    """Send the digest. Returns a provider message id.

    dry_run writes the email to disk instead of sending, so the layout can be
    checked in a browser without needing credentials or spamming yourself.
    """
    _load_env()
    provider = provider or config.EMAIL_PROVIDER
    to_addr = to_addr or os.environ.get("DIGEST_TO") or config.DIGEST_TO
    from_addr = os.environ.get("DIGEST_FROM") or config.DIGEST_FROM

    if dry_run:
        out = config.PROJECT_ROOT / "data" / "digest_preview.html"
        out.write_text(html, encoding="utf-8")
        (config.PROJECT_ROOT / "data" / "digest_preview.txt").write_text(
            text, encoding="utf-8")
        return f"dry-run -> {out}"

    if not to_addr:
        raise DeliveryError("no recipient — set DIGEST_TO in news-digest/.env")

    try:
        msg_id = _PROVIDERS[provider](subject, html, text, to_addr, from_addr)
    except Exception as exc:
        _log_send(provider, to_addr, subject, n_stories, "failed", exc)
        raise
    _log_send(provider, to_addr, subject, n_stories, "ok", msg_id)
    return msg_id


def recent_sends(limit=10):
    conn = store.connect()
    conn.executescript(SEND_LOG_SCHEMA)
    rows = conn.execute("SELECT * FROM sends ORDER BY sent_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]
