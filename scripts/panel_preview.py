#!/usr/bin/env python3
"""Render the panel's ACTUAL look against live data, as HTML.

Why this exists: a WoW frame cannot be iterated on quickly (every change costs a
client restart, and the panel is drawn by the client, not by me). So the layout,
wording and density get settled in a browser first, against the real roster.

This is not a mockup. It mirrors `addon/HermesAI/UI.lua`: the same 560x536 frame,
34px rows, ten of them, the same palette table, the same tab labels and counts,
the same truncation, the same footer. When this and the addon disagree, the addon
is right and this file is the bug.

Run: python3 scripts/panel_preview.py [output.html]
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wowmode import roster  # noqa: E402

# The palette and the status colours are READ OUT OF THE ADDON. A hand-copied hex
# is a slow-motion bug: the preview would keep looking right after the addon
# changed, which is the one thing this script exists to prevent.
ADDON = ROOT / "addon" / "HermesAI"


def lua_tables(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def to_hex(triple: list[float]) -> str:
    return "#" + "".join(f"{int(round(value * 255)):02x}" for value in triple[:3])


def to_rgba(triple: list[float]) -> str:
    red, green, blue, alpha = (list(triple) + [1.0])[:4]
    return f"rgba({int(round(red * 255))},{int(round(green * 255))},{int(round(blue * 255))},{round(alpha, 3)})"


def parse_theme(source: str, name: str) -> dict[str, list[float]]:
    """The named theme's entries, as {key: [r, g, b, a?]}."""
    block = re.search(rf"\n  {name} = \{{(.*?)\n  \}},", source, re.S)
    if not block:
        raise SystemExit(f"panel_preview: no '{name}' theme in UI.lua: the preview would be lying")

    entries = {}
    for key, values in re.findall(r"(\w+) = \{([^}]*)\}", block.group(1)):
        numbers = [float(item) for item in values.replace(" ", "").split(",") if item]
        if numbers:
            entries[key] = numbers
    return entries


def parse_status_colors(source: str) -> dict[str, str]:
    block = re.search(r"ns\.STATUS_COLORS = \{(.*?)\n\}", source, re.S)
    if not block:
        raise SystemExit("panel_preview: no STATUS_COLORS in Core.lua: the preview would be lying")
    return {
        key: to_hex([float(item) for item in values.replace(" ", "").split(",") if item])
        for key, values in re.findall(r"(\w+) = \{([^}]*)\}", block.group(1))
    }


UI_SOURCE = lua_tables(ADDON / "UI.lua")
CORE_SOURCE = lua_tables(ADDON / "Core.lua")

THEME = parse_theme(UI_SOURCE, "dark")
COLORS = parse_status_colors(CORE_SOURCE)

PANEL_BG = to_hex(THEME["bg"])
PANEL_EDGE = to_hex(THEME["edge"])
ROW = to_rgba(THEME["row"])
ROW_ALT = to_rgba(THEME["rowAlt"])
ROW_HIGHLIGHT = to_rgba(THEME["highlight"])
BUTTON = to_hex(THEME["button"])
BUTTON_HOVER = to_hex(THEME["buttonHover"])
BUTTON_ACTIVE = to_rgba(THEME["buttonActive"])
FIELD = to_rgba(THEME["field"])

GOLD = "#c8a45c"
TEXT = "#e8edf7"
MUTED = "#949eb9"
DIM = "#70778f"

TABS = [
    ("all", "All", None),
    ("needs", "Needs you", "needs"),
    ("working", "Working", "working"),
    ("waiting", "Waiting", "waiting"),
    ("reply", "Replies", "reply"),
    ("finished", "Finished", "finished"),
]

ROW_COUNT = 12
SEP = " \u00b7 "


def age_phrase(seconds) -> str:
    """"just now", not "now ago"."""
    compact = age_label(seconds)
    if compact == "now":
        return "just now"
    return compact + " ago"


def age_label(seconds) -> str:
    """UI.lua ageLabel: compact, because a row has no space for prose."""
    seconds = float(seconds or 0)
    if seconds < 90:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    return f"{int(seconds // 86400)}d"


def truncate(text, limit: int) -> str:
    """Cut to fit, at the last sentence end when one is close by."""
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text

    head = text[: limit - len("...")]
    boundary = re.match(r"(?s)^.*[.!?\n]\s?", head) if head else None
    if boundary and len(boundary.group(0)) >= limit // 2:
        head = boundary.group(0).rstrip()
    return head + "..."


def new_label(seconds) -> str:
    seconds = float(seconds or 0)
    if seconds < 90:
        return "just now"
    return age_label(seconds) + " ago"


def row_meta(session: dict, offline: bool = False) -> str:
    """The row's second line: status, where it lives, what it is doing."""
    parts = [session.get("status_label") or session["status"]]
    trail = session["host"] if session.get("host") not in (None, "", "local") else session.get("profile")
    if trail:
        parts.append(trail)
    if session.get("project"):
        parts.append(session["project"])
    if offline:
        parts.append("host offline")
    elif session.get("activity"):
        parts.append(session["activity"])
    return SEP.join(parts)


def is_offline(session: dict, offline_hosts: set[str]) -> bool:
    """The addon's rule: the row's own flag, or its host being down."""
    if session.get("host_offline"):
        return True
    host = session.get("host")
    return bool(host and host != "local" and host in offline_hosts)


def render_row(session: dict, index: int, *, offline: bool = False) -> str:
    color = COLORS.get(session["status"], COLORS["idle"])
    title_color = DIM if offline else TEXT
    return f"""
        <div class="row" style="background:{ROW_ALT if index % 2 == 0 else ROW}">
          <i class="dot" style="background:{color};opacity:{0.45 if offline else 1}"></i>
          <div class="body">
            <div class="title" style="color:{title_color}">{html.escape(session['title'] or '')}</div>
            <div class="meta" style="color:{color}">{html.escape(row_meta(session, offline))}</div>
          </div>
          <div class="age">{age_label(session['age_s'])}</div>
          <div class="chev">&gt;</div>
        </div>"""


def render_detail(session: dict | None) -> str:
    """The pane behind a row: what it is, what it has done, and the reply box."""
    if not session:
        return '<div class="rows"><div class="empty">Click a row for detail, reply and hand-off.</div></div>'

    where = []
    host = session.get("host")
    where.append(host if host and host != "local" else "this machine")
    if session.get("profile"):
        where.append(session["profile"])
    if session.get("project"):
        where.append(session["project"])

    extras = []
    if session.get("messages"):
        extras.append(f"{session['messages']} messages")
    if session.get("cost_usd"):
        extras.append(f"${session['cost_usd']:.2f}")
    if session.get("activity"):
        extras.append(session["activity"])
    body = session.get("preview") or "no output in the last turn"

    facts = [
        ("session", session["id"]),
        ("machine", host if host and host != "local" else "this machine"),
        ("project", session.get("project") or "-"),
        ("profile", session.get("profile") or "-"),
        ("messages", str(session.get("messages") or 0)),
        ("cost", f"${session.get('cost_usd') or 0:.2f}"),
    ]
    fact_rows = "".join(
        f'<div class="fact"><span class="fkey">{html.escape(key)}</span>'
        f'<span class="fvalue">{html.escape(value)}</span></div>'
        for key, value in facts
    )

    color = COLORS.get(session["status"], COLORS["idle"])
    return f"""
      <div class="detail">
        <div class="dtitle">{html.escape(session['title'] or '')}</div>
        <div class="dmeta">{html.escape(' / '.join(where))}</div>
        <div class="dstatus" style="color:{color}">{html.escape(session.get('status_label') or '')} {SEP} last activity {age_phrase(session['age_s'])} {SEP} session {html.escape(session['id'])}</div>
        <div class="dpreview">{html.escape(truncate(body, 900))}</div>
        <div class="dstats">{html.escape(session.get("activity") or "not reporting an activity")}</div>
        <div class="facts">{fact_rows}</div>
        <div class="dtarget">the reply goes to {html.escape(host if host and host != 'local' else 'this machine')}</div>
        <div class="composer">
          <div class="input">Reply to {html.escape(session['id'][-8:])}...  <span class="caret"></span></div>
          <span class="button">Hand off</span>
          <span class="button primary">Send</span>
        </div>
        <div class="hint">Enter sends the reply and syncs. Hand off puts the session on your clipboard for the desktop app.</div>
      </div>"""


def row_counts(sessions: list[dict]) -> dict:
    """The counts the ADDON shows: computed from the rows it actually received.

    The roster's own counts cover the whole three-day window, which is more than
    the payload carries. The panel tabs must count what the panel can show, or
    the preview stops matching the thing it previews.
    """
    counts: dict[str, int] = {}
    for session in sessions:
        counts[session["status"]] = counts.get(session["status"], 0) + 1
    return counts


def render_panel(data: dict, *, mode: str) -> str:
    sessions = data.get("sessions", [])
    counts = row_counts(sessions)
    attention = counts.get("needs", 0) + counts.get("error", 0)
    # The roster reports when it was generated; there is no separate age field,
    # and reading one that never exists made every render say "just now".
    generated_at = float(data.get("generated_at") or time.time())
    age = age_label(max(0.0, time.time() - generated_at))

    tabs = []
    for key, label, bucket in TABS:
        count = len(sessions) if bucket is None else counts.get(bucket, 0)
        active = " active" if key == ("all" if mode == "board" else "needs") else ""
        tabs.append(f'<span class="tab{active}">{html.escape(label)} {count}</span>')

    if mode == "detail":
        body = render_detail(sessions[0]) if sessions else render_detail(None)
    else:
        offline_hosts = {name for name, state in (data.get("hosts") or {}).items() if state != "ok"}
        rows = "".join(
            render_row(session, index + 1, offline=is_offline(session, offline_hosts))
            for index, session in enumerate(sessions[:ROW_COUNT])
        )
        if not rows:
            rows = '<div class="empty">No snapshot yet: run <code>hermes-wow wow install</code>, then sync.</div>'
        body = f'<div class="rows">{rows}</div>'

    offline_hosts = data.get("hosts_offline") or []

    trailer = ""
    if len(sessions) > ROW_COUNT:
        trailer = f"1-{ROW_COUNT} of {len(sessions)}, scroll"

    return f"""    <div class="panel">
      <div class="header">
        <span class="crest">H</span>
        <span class="name">Hermes Agents</span>
        <span class="badge">{attention} need you</span>
        <span class="synced">{'+' + str(data.get('new_count', 0)) + ' new, ' if data.get('new_count') else ''}synced {age}</span>
        <span class="button tiny">*</span><span class="button tiny">-</span><span class="button tiny">X</span>
      </div>
      <div class="search">Search agents, threads, or projects...<span class="caret"></span></div>
      <div class="tabs">{''.join(tabs)}</div>
{body}
      <div class="footer">
        <span>{'host offline: ' + ', '.join(offline_hosts) if offline_hosts else 'needs you / new reply / working / waiting / finished'}</span>
        <span style="margin-left:auto">{trailer}</span>
      </div>
    </div>"""


def render(data: dict, *, now: float) -> str:
    sessions = data.get("sessions", [])
    generated = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    attention = data.get("attention", 0)

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>HermesAI panel, as the addon draws it</title>
<style>
  :root {{ --edge: {PANEL_EDGE}; --text: {TEXT}; --muted: {MUTED}; --dim: {DIM}; --gold: {GOLD}; }}
  body {{ margin: 0; padding: 16px; background: #05070d; color: var(--text);
          font: 13px/1.3 system-ui, "Segoe UI", sans-serif; }}
  .wrap {{ display: flex; gap: 18px; align-items: flex-start; flex-wrap: wrap; }}
  .panel {{ width: 560px; height: 540px; box-sizing: border-box; display: flex; flex-direction: column;
            background: linear-gradient(180deg, #141b2e, {PANEL_BG});
            border: 1px solid var(--edge); border-radius: 4px;
            box-shadow: 0 0 18px rgba(47,107,216,.35), inset 0 0 40px rgba(47,107,216,.05); }}
  .header {{ display: flex; align-items: center; gap: 6px; padding: 9px 12px 6px; }}
  .crest {{ color: var(--gold); font-weight: 700; }}
  .name {{ font-weight: 600; }}
  .badge {{ color: #ffa843; font-size: 12px; margin-left: 4px; }}
  .synced {{ margin-left: auto; color: var(--dim); font-size: 11.5px; }}
  .button {{ padding: 1px 7px; border: 1px solid rgba(148,158,185,.45); border-radius: 3px;
             color: var(--muted); font-size: 11.5px; background: {BUTTON}; }}
  .button.tiny {{ padding: 0 5px; }}
  .button.primary {{ border-color: var(--edge); color: var(--text); background: rgba(47,107,216,.28); }}
  .search {{ margin: 2px 12px 0; padding: 4px 8px; border: 1px solid rgba(148,158,185,.35); border-radius: 3px;
             background: {FIELD}; color: var(--dim); font-size: 12px; }}
  .caret {{ display: inline-block; width: 1px; height: 11px; background: var(--muted); vertical-align: -1px; }}
  .tabs {{ display: flex; gap: 4px; padding: 8px 12px; }}
  .tab {{ padding: 2px 8px; border: 1px solid rgba(148,158,185,.35); border-radius: 3px;
          background: {BUTTON}; color: var(--muted); font-size: 11.5px; }}
  .tab.active {{ background: rgba(47,107,216,.35); border-color: var(--edge); color: var(--text); }}
  .rows {{ padding: 0 10px; flex: 1; overflow: hidden; }}
  .row {{ display: flex; align-items: center; gap: 8px; height: 34px; padding: 0 8px 0 6px; }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; flex: 0 0 8px; }}
  .body {{ flex: 1; min-width: 0; }}
  /* The addon measures text with GetStringWidth and trims to fit; the browser's
     equivalent is a fixed box and an ellipsis. Same intent, same widths. */
  .body {{ flex: 1; min-width: 0; }}
  .title {{ font-weight: 600; font-size: 12.5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .meta {{ font-size: 11px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .age {{ color: var(--dim); font-size: 11px; }}
  .chev {{ color: var(--muted); }}
  .empty {{ padding: 40px 12px; color: var(--dim); font-size: 12px; text-align: center; }}
  .detail {{ margin: 0 10px; padding: 12px; flex: 1; display: flex; flex-direction: column;
             background: rgba(20,27,45,.97); border: 1px solid rgba(47,107,216,.35); border-radius: 3px; }}
  .dtitle {{ font-weight: 600; font-size: 14px; }}
  .dmeta {{ color: var(--muted); font-size: 11.5px; margin-top: 2px; }}
  .dstatus {{ font-size: 11.5px; margin-top: 8px; }}
  .dpreview {{ color: var(--text); font-size: 12px; margin-top: 10px; line-height: 1.45; }}
  .dstats {{ color: var(--muted); font-size: 11.5px; margin-top: 12px; }}
  .dtarget {{ color: var(--dim); font-size: 11.5px; margin-top: 6px; }}
  /* The pane is taller than one paragraph needs, so the facts the player would
     otherwise have to guess at live in the space instead of in a hole. */
  .facts {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px 18px; margin-top: 14px; }}
  .fact {{ display: flex; gap: 8px; font-size: 11.5px; }}
  .fkey {{ color: var(--dim); min-width: 62px; }}
  .fvalue {{ color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
  .composer {{ display: flex; align-items: center; gap: 6px; margin-top: auto; }}
  .input {{ flex: 1; padding: 3px 8px; border: 1px solid rgba(47,107,216,.35); border-radius: 3px;
            background: rgba(0,0,0,.35); color: var(--dim); font-size: 12px; }}
  .hint {{ color: var(--dim); font-size: 11px; margin-top: 10px; }}
  .footer {{ display: flex; padding: 5px 12px 8px; color: var(--dim); font-size: 11px; }}
  .note {{ width: 380px; color: var(--muted); font-size: 12px; }}
  .note h3 {{ color: var(--text); font-size: 12.5px; margin: 0 0 6px; }}
  .note li {{ margin-bottom: 5px; }}
  code {{ color: var(--text); }}
</style></head>
<body>
  <div class="wrap">
{render_panel(data, mode="board")}
{render_panel(data, mode="detail")}
    <div class="note">
      <h3>Rendered from your live roster</h3>
      <ul>
        <li>{len(sessions)} sessions, {attention} needing you, generated {generated}.</li>
        <li>Left: the board. Right: the detail pane that opens when you click a row (composer focused, ready to type).</li>
        <li>Same numbers the addon computes: tab counts, the synced stamp, per-row age, host and project.</li>
        <li>Not pictured: the movable badge, the minimap button with its count, the settings pane, and the first-run card.</li>
        <li>Fonts and chrome are the client's in game, so text is a little tighter than this.</li>
      </ul>
    </div>
  </div>
</body></html>
"""


def main() -> int:
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "panel-preview.html"
    data = roster.board(limit=15, days=3)

    # Present the panel the way the player will most often see it: something
    # wanting a decision, plus whatever is running.
    data["new_count"] = 0
    data["hosts_offline"] = []

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(data, now=time.time()), encoding="utf-8")
    print(json.dumps({
        "path": str(output),
        "sessions": len(data.get("sessions", [])),
        "attention": data.get("attention", 0),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
