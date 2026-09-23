"""Stage 6a — Rendering.

No AI. String formatting.

Email HTML is not web HTML. Mail clients strip <style> blocks, ignore flexbox
and grid, and Outlook renders through Word's engine. So everything here is
inline styles on tables and a system font stack — deliberately old-fashioned,
because it is what actually arrives looking right.

We produce both an HTML and a plain-text version. Every email carries both; the
client picks. Plain text is what shows in notification previews and what a
screen reader falls back to, so it is not a throwaway.

    input  : synthesised stories from Stage 5
    output : (subject, html, text)
    next   : deliver.py sends it
"""

from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from . import config

_FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
         "Helvetica, Arial, sans-serif")


def _today():
    return datetime.now(ZoneInfo(config.DIGEST_TIMEZONE))


def subject_line(stories, when=None):
    when = when or _today()
    date = when.strftime("%a %d %b")
    if not stories:
        return f"Morning Digest — {date} — no major stories"
    lead = stories[0]["title"]
    if len(lead) > 58:
        lead = lead[:55].rstrip() + "…"
    extra = f" +{len(stories) - 1} more" if len(stories) > 1 else ""
    return f"{date}: {lead}{extra}"


# ----------------------------------------------------------------- plain text --

def render_text(stories, when=None):
    when = when or _today()
    lines = [f"MORNING DIGEST — {when.strftime('%A %d %B %Y')}", ""]

    if not stories:
        lines += ["No stories met the bar this morning.", "",
                  "A story needs coverage from at least two of the three sources",
                  "and meaningful real-world impact to be included. Nothing",
                  "qualified today, so there is nothing to read.", ""]
        return "\n".join(lines)

    lines.append(f"{len(stories)} "
                 f"{'story' if len(stories) == 1 else 'stories'} · "
                 f"about {max(1, len(stories))} min read")
    lines.append("")

    for i, s in enumerate(stories, 1):
        lines += [f"{i}. {s['title']}", "",
                  f"   WHAT HAPPENED", f"   {s['what_happened']}", "",
                  f"   IMPACT", f"   {s['impact']}", ""]
        if s.get("whats_next"):
            lines += ["   WHAT'S NEXT", f"   {s['whats_next']}", ""]
        seen, srcs = set(), []
        for src in s.get("sources", []):
            if src["name"] not in seen:
                seen.add(src["name"])
                srcs.append(f"   - {src['name']}: {src['url']}")
        lines += ["   SOURCES"] + srcs + ["", "-" * 58, ""]

    lines += ["Assembled from The Hindu, News18 and Hindustan Times.",
              "Only stories carried by two or more of them are included."]
    return "\n".join(lines)


# ----------------------------------------------------------------------- html --

def _story_html(i, s):
    parts = [f'''
      <tr><td style="padding:0 0 30px 0;">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
          <tr><td style="padding:0 0 10px 0;">
            <span style="display:inline-block;min-width:22px;height:22px;
                         background:#1a1a1a;color:#ffffff;border-radius:11px;
                         font:600 12px/22px {_FONT};text-align:center;
                         padding:0 7px;">{i}</span>
            <span style="font:600 19px/27px {_FONT};color:#111111;
                         padding-left:9px;">{escape(s["title"])}</span>
          </td></tr>''']

    for label, body in (("What happened", s.get("what_happened")),
                        ("Impact", s.get("impact")),
                        ("What's next", s.get("whats_next"))):
        if not body:
            continue
        parts.append(f'''
          <tr><td style="padding:0 0 4px 0;">
            <span style="font:600 11px/16px {_FONT};color:#8a8a8a;
                         letter-spacing:0.7px;text-transform:uppercase;">{label}</span>
          </td></tr>
          <tr><td style="padding:0 0 13px 0;font:400 15px/24px {_FONT};
                         color:#2e2e2e;">{escape(body)}</td></tr>''')

    seen, links = set(), []
    for src in s.get("sources", []):
        if src["name"] in seen:
            continue
        seen.add(src["name"])
        links.append(f'<a href="{escape(src["url"], quote=True)}" '
                     f'style="color:#555555;text-decoration:underline;">'
                     f'{escape(src["name"])}</a>')
    parts.append(f'''
          <tr><td style="padding:3px 0 0 0;font:400 13px/20px {_FONT};
                         color:#8a8a8a;">Sources: {" · ".join(links)}</td></tr>
        </table>
        <div style="border-bottom:1px solid #e8e8e8;margin-top:26px;"></div>
      </td></tr>''')
    return "".join(parts)


def render_html(stories, when=None):
    when = when or _today()
    header_date = when.strftime("%A, %d %B %Y")

    if stories:
        count = (f"{len(stories)} {'story' if len(stories) == 1 else 'stories'}"
                 f" · about {max(1, len(stories))} min read")
        body = "".join(_story_html(i, s) for i, s in enumerate(stories, 1))
    else:
        count = "Nothing qualified today"
        body = f'''
      <tr><td style="padding:14px 0 30px 0;font:400 15px/24px {_FONT};color:#2e2e2e;">
        No stories met the bar this morning. A story needs coverage from at least
        two of the three sources and meaningful real-world impact to be included.
        Rather than pad the digest with less important news, we have sent nothing.
      </td></tr>'''

    return f'''<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light">
<title>Morning Digest</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f2;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">
  {escape(subject_line(stories, when))}
</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:#f4f4f2;">
 <tr><td align="center" style="padding:26px 16px;">
  <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
         style="width:100%;max-width:600px;background:#ffffff;border-radius:6px;">
   <tr><td style="padding:34px 34px 0 34px;">
     <div style="font:700 25px/32px {_FONT};color:#111111;">Morning Digest</div>
     <div style="font:400 14px/21px {_FONT};color:#8a8a8a;padding-top:5px;">
       {header_date}</div>
     <div style="font:400 13px/20px {_FONT};color:#8a8a8a;padding-top:2px;">
       {count}</div>
     <div style="border-bottom:2px solid #111111;margin:20px 0 26px 0;"></div>
   </td></tr>
   <tr><td style="padding:0 34px;">
     <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
       {body}
     </table>
   </td></tr>
   <tr><td style="padding:6px 34px 30px 34px;font:400 12px/19px {_FONT};color:#9a9a9a;">
     Assembled from The Hindu, News18 and Hindustan Times. Only stories carried by
     two or more of them are included, and facts reported by a single source are
     attributed to that source in the text.
   </td></tr>
  </table>
 </td></tr>
</table>
</body></html>'''


def render(stories, when=None):
    when = when or _today()
    return subject_line(stories, when), render_html(stories, when), render_text(stories, when)
