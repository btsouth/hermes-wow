"""The WoW side of hermes-wow: publish the roster into the client, take replies out.

Why it works this way (the sandbox decides it, not taste): a WoW addon has no
filesystem and no network. Its only persistence is SavedVariables, which the
*client* reads at UI load and writes at logout or /reload. So the two directions
are asymmetric and both go through the client's own file moments:

* in  -> Hermes writes ``Interface/AddOns/HermesAI/Data.lua`` (a plain Lua
  table). The client reads it when the UI loads, so fresh data arrives on login,
  on ``/reload``, and on the addon's sync key.
* out -> the addon queues replies into SavedVariables; the client writes them to
  ``WTF/Account/<acct>/SavedVariables/HermesAI.lua`` at the same reload. Hermes
  watches that file and dispatches what it has not seen yet.

The outbox is a single delimited string on purpose: parsing Lua from Python
would mean shipping a Lua parser for one table, and the addon can sanitise the
separators out of user text trivially.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import backend, state as state_module
from .roster import board

ADDON_NAME = "HermesAI"
ENTRY_SEP = ";;"
FIELD_SEP = "|"

# What a queued entry can be. A closed set on purpose: the alternative was
# recognising a hand-off by its text being exactly "!focus", which meant a player
# who typed that sentence got a clipboard instead of a reply.
OUTBOX_KINDS = {"reply", "focus"}

# What a session id looks like in the store: date_time_hex, or any uuid-shaped
# string. Used to tell a real entry from the debris of a torn one.
SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.-]{8,}")

# Payload wire format. The addon refuses anything whose tag or schema it does not
# know, so both sides can be updated independently without silent nonsense.
PAYLOAD_TAG = "HE1"
PAYLOAD_SCHEMA = 2
BRIDGE_VERSION = "0.5.0"

_INSTALL_HINTS = (
    "/mnt/data/Games/World of Warcraft",
    "/data/Games/World of Warcraft",
    "~/Games/World of Warcraft",
    "~/Games",
    "/mnt/games/World of Warcraft",
)

_STATE_PATH = state_module.STATE_DIR / "wow-inbox.json"


# --------------------------------------------------------------- discovery --


def _expand(path: str | Path) -> Path:
    return Path(os.path.expanduser(str(path)))


def addon_source() -> Path:
    """The addon as it lives in this project."""
    return Path(__file__).resolve().parent.parent / "addon" / ADDON_NAME


def find_addon_dirs() -> list[Path]:
    """Every ``Interface/AddOns`` belonging to a WoW client on this machine."""
    explicit = os.environ.get("HERMES_WOW_ADDON_DIR")
    if explicit:
        return [_expand(explicit)]

    found: list[Path] = []
    seen: set[tuple[int, int]] = set()
    roots: list[Path] = []

    for hint in _INSTALL_HINTS:
        roots.append(_expand(hint))

    for root in roots:
        if not root.is_dir():
            continue
        # <root>/<version dir>/Interface/AddOns, plus the case where the hint
        # already points at the version directory.
        for candidate in sorted(root.glob("*/Interface/AddOns")) + sorted(root.glob("Interface/AddOns")):
            if not candidate.is_dir():
                continue
            # The same install is often reachable through more than one mount
            # path; same inode means one client, not two.
            try:
                stat = candidate.stat()
            except OSError:
                continue
            key = (stat.st_dev, stat.st_ino)
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)
    return found


def pick_addon_dir(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        path = _expand(explicit)
        return path

    candidates = find_addon_dirs()
    if not candidates:
        return None

    # Prefer the directory the client has touched most recently: that is the
    # install the user is actually playing.
    return max(candidates, key=lambda path: path.stat().st_mtime if path.exists() else 0)


def version_dir(addon_dir: Path) -> Path:
    """The client's version directory: the one that holds ``Interface`` and ``WTF``.

    A client keeps `WTF` BESIDE `Interface`, not inside it, so walking up from the
    addon folder has to reach the version directory itself. This was wrong once in
    a way no gate noticed: every in-game reply silently went nowhere because the
    SavedVariables lookup returned None.

    Found by looking for the directory that actually has a `WTF` in it, rather
    than by counting parents: callers pass either the AddOns folder or the addon
    folder, and a count that is right for one is wrong for the other.
    """
    leaf = addon_path(addon_dir)
    candidates = [leaf, leaf.parent, leaf.parent.parent, leaf.parent.parent.parent]
    for candidate in candidates:
        if (candidate / "WTF").is_dir():
            return candidate
    # No WTF anywhere: a client that has never been launched. Assume the standard
    # layout so the caller gets a missing-file answer rather than a wrong one.
    return leaf.parent.parent.parent


def savedvars_path(addon_dir: Path) -> Path | None:
    """``WTF/Account/<account>/SavedVariables/HermesAI.lua`` for this client."""
    account_root = version_dir(addon_dir) / "WTF" / "Account"
    if not account_root.is_dir():
        return None

    candidates = [path for path in account_root.glob(f"*/SavedVariables/{ADDON_NAME}.lua") if path.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


# ----------------------------------------------------------------- publish --

# Fields are delimited, so the separators are stripped out of user text rather
# than escaped: a title containing "|" must not be able to forge a field.
_SEPARATORS = str.maketrans({"|": "/", ";": "/", '"': "/", "\\": "/", "\r": " ", "\n": " ", "\t": " "})


def _field(value: Any, limit: int = 0) -> str:
    text = str(value if value is not None else "").translate(_SEPARATORS)
    if limit and len(text) > limit:
        return text[: limit - 1] + "\u2026"
    return text


def _lua_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _number(value: Any, default: float) -> float:
    """A numeric field from a row, or a default: a remote roster is not ours to trust.

    Non-finite counts as nonsense here too. `json.loads` hands over `Infinity`,
    `NaN` and an overflowing `1e999` without complaint, `float()` keeps them, and
    the `int()` on the other side of this refuses all three - which raised out of
    `render_payload`, so one bad field on one host cost the whole publish.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def render_payload(
    payload: dict,
    new_ids: Iterable[str] = (),
    *,
    host_status: dict[str, str] | None = None,
    acked: int = 0,
    error: str = "",
) -> str:
    """The published snapshot as one delimited string.

    Header, then one record per session:

    ``id|status|age_seconds|host|profile|project|title|activity|messages|cost|offline|preview``
    """
    generated = int(payload.get("generated_at", time.time()))
    sessions = list(payload.get("sessions", []) or [])
    host_status = host_status or {"local": "ok"}

    header = "|".join(
        (
            PAYLOAD_TAG,
            f"bridge={BRIDGE_VERSION}",
            f"schema={PAYLOAD_SCHEMA}",
            f"generated={generated}",
            f"rows={len(sessions)}",
            "hosts=" + ";".join(f"{_field(name, 32)}:{state}" for name, state in sorted(host_status.items())),
            f"acked={int(acked or 0)}",
            # A bridge that cannot read the session store must say so in the
            # payload: an empty board with no explanation reads as "all clear".
            "error=" + _field(error, 160) if error else "error=",
            "new=" + ";".join(_field(item, 64) for item in new_ids),
        )
    )

    records = [header]
    for session in sessions:
        records.append(
            "|".join(
                (
                    _field(session.get("id"), 64),
                    _field(session.get("status"), 16),
                    str(int(_number(session.get("age_s"), 0))),
                    _field(session.get("host") or "local", 32),
                    _field(session.get("profile"), 32),
                    _field(session.get("project"), 48),
                    _field(session.get("title"), 120),
                    _field(session.get("activity"), 80),
                    str(int(_number(session.get("messages"), 0))),
                    f"{_number(session.get('cost_usd'), 0.0):.4f}",
                    "1" if session.get("host_offline") else "0",
                    _field(session.get("preview"), 160),
                )
            )
        )

    return ENTRY_SEP.join(records)


def render_data(
    payload: dict,
    new_ids: Iterable[str] = (),
    *,
    host_status: dict[str, str] | None = None,
    acked: int = 0,
    error: str = "",
) -> str:
    """The Data.lua the client reads at UI load: data, never code."""
    body = _lua_string(render_payload(payload, new_ids, host_status=host_status, acked=acked, error=error))
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return (
        f"-- generated by hermes-wow at {stamp}; data only, do not edit by hand\n"
        f"HermesAIData = {body}\n"
    )


def read_sync_stamp(path: Path | None) -> float | None:
    """When the player last synced, from the addon's own SavedVariables.

    The addon sets this immediately before it reloads the UI, so the value the
    client flushes is the moment of that sync.
    """
    if path is None:
        return None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    match = re.search(r"^\s*HermesAISync\s*=\s*(\d+(?:\.\d+)?)", text, re.MULTILINE)
    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def new_since(payload: dict, sync_stamp: float | None) -> list[str]:
    """Attention items that appeared since the player's last in-game sync.

    A needs-you transition is caused by the agent's message landing, so the
    session's own last-activity timestamp dates the transition closely enough to
    say "2 new since you last looked".
    """
    if not sync_stamp:
        return []

    fresh: list[str] = []
    for session in payload.get("sessions", []) or []:
        if session.get("status") not in ("needs", "error"):
            continue
        appeared_at = float(session.get("activity_at") or 0)
        if appeared_at > sync_stamp:
            fresh.append(str(session.get("id")))
    return fresh


def addon_path(base: Path, name: str = ADDON_NAME) -> Path:
    """Accept either the AddOns directory or the addon directory itself."""
    return base if base.name == name else base / name


def publish(
    addon_dir: Path,
    payload: dict | None = None,
    *,
    limit: int = 15,
    days: float = 3.0,
    hosts_enabled: bool = True,
    host_ttl: float = 120.0,
) -> dict:
    """Write the roster the addon will read, atomically.

    `host_ttl` is how stale a cached remote roster may be before a publish pays
    for a fresh fetch. The watcher passes a huge value and refreshes hosts on its
    own thread, so a slow or dead host can never hold up the board.
    """
    from . import hosts as host_module

    data = payload if payload is not None else board(limit=limit, days=days)
    # A caller-supplied host map wins: the fixture and any future caller that
    # merges its own hosts should not be overwritten by our default.
    host_status: dict[str, str] = dict(data.get("hosts") or {"local": "ok"})
    remote_error = ""

    if hosts_enabled:
        configured = host_module.load_hosts()
        if configured:
            try:
                # The ssh calls happen OUTSIDE the cache lock: holding it across a
                # dead host's timeout would stall the refresher thread, and the
                # whole point of the cache is that neither waits for the other.
                snapshot = host_module.load_cache()
                results = host_module.fetch_all(
                    configured, limit=limit, days=days, cache=snapshot, ttl=host_ttl
                )

                def apply_hosts(cache: dict) -> None:
                    nonlocal host_status, data
                    merged, host_status, cache_out = host_module.merge(
                        data.get("sessions", []) or [], results, cache=cache
                    )
                    cache.clear()
                    cache.update(cache_out)
                    # The addon recounts attention from the rows it receives, so
                    # there is nothing to fix up here beyond the merged rows.
                    data = dict(data, sessions=merged)

                host_module.mutate_cache(apply_hosts)
            except Exception as exc:  # noqa: BLE001 - a host problem must not stop the local publish
                remote_error = str(exc)

    directory = addon_path(addon_dir)
    target = directory / "Data.lua"
    target.parent.mkdir(parents=True, exist_ok=True)

    # A roster that could not be read is published AS an error, never as an empty
    # board: the panel has to say "Hermes is not answering" instead of rendering
    # "all clear" over a bridge that is broken.
    roster_error = str(data.get("error") or "")

    sync_stamp = read_sync_stamp(savedvars_path(directory))
    new_ids = new_since(data, sync_stamp)

    # 0644: the game reads this file, and it is not a secret.
    state_module.atomic_write(
        target,
        render_data(data, new_ids, host_status=host_status, acked=acked_seq(), error=roster_error),
        mode=0o644,
    )

    return {
        "path": str(target),
        "sessions": len(data.get("sessions", []) or []),
        "attention": int(data.get("attention", 0)),
        "new": new_ids,
        "sync_stamp": sync_stamp,
        "rows": list(data.get("sessions", []) or []),
        "host_status": host_status,
        "acked": acked_seq(),
        "error": roster_error,
        "remote_error": remote_error,
    }


def install(addon_dir: Path, *, force: bool = False) -> dict:
    """Copy the addon into the client's AddOns folder."""
    source = addon_source()
    if not source.is_dir():
        raise RuntimeError(f"addon sources missing at {source}")

    destination = addon_path(addon_dir)
    if destination.exists() and not force:
        # Two things can own this folder. If the copy came from a release zip or an
        # addon manager, overwriting it takes it out of that manager's hands and
        # silently pins the user to whatever version this checkout happens to be;
        # the only thing the bridge actually needs to write is the snapshot.
        raise RuntimeError(
            f"{destination} already exists.\n"
            "  If an addon manager or a release zip installed it, it is not ours to replace:\n"
            "  `hermes-wow wow publish` refreshes the snapshot without touching the code.\n"
            "  Pass --force only to replace a copy that this tool installed."
        )

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    for item in sorted(source.iterdir()):
        if item.is_file():
            (destination / item.name).write_bytes(item.read_bytes())
            copied.append(item.name)

    return {"installed": str(destination), "files": copied}


# ------------------------------------------------------------------- inbox --


def _unescape_lua_literal(raw: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\" and index + 1 < len(raw):
            nxt = raw[index + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "'": "'"}
            if nxt in mapping:
                out.append(mapping[nxt])
                index += 2
                continue
            if nxt.isdigit():
                # Bounded on purpose: `\2a` or `\12z` used to raise out of here,
                # and this parser's contract is that a torn or hand-edited file
                # reads as "nothing new", not as an exception in the watch loop.
                #
                # `str.isdigit()` is true for more than `\d` matches: superscripts
                # and fractions (`\²`, `\½`) pass the test above and fail the regex,
                # and taking `nxt` as the digits then reached int() and raised -
                # taking the watch loop down from a file that the client writes.
                # No match means the backslash was not a decimal escape, so it is
                # left alone like any other unknown one.
                match = re.match(r"\d{1,3}", raw[index + 1 :])
                if match:
                    code = int(match.group(0))
                    out.append(chr(code) if 0 < code < 0x110000 else nxt)
                    index += 1 + len(match.group(0))
                    continue
        out.append(char)
        index += 1
    return "".join(out)


def read_outbox(path: Path) -> list[dict[str, str]]:
    """Entries the addon queued, newest last.

    Wire format is ``seq|host|session|text``; a three-field entry (an addon older
    than the host routing) still reads, as a local reply.

    Tolerant by design: a half-written file (the client was killed mid-flush)
    must read as "nothing new", never as an exception in the watcher loop.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    match = re.search(r"^\s*HermesAIOutbox\s*=\s*\"((?:[^\"\\]|\\.)*)\"", text, re.MULTILINE)
    if not match:
        return []

    payload = _unescape_lua_literal(match.group(1))
    entries: list[dict[str, str]] = []

    for chunk in payload.split(ENTRY_SEP):
        chunk = chunk.strip()
        if not chunk:
            continue

        parts = [part.strip() for part in chunk.split(FIELD_SEP)]

        # Current wire format: seq|kind|host|session|text. Two older shapes are
        # still read, because a player can be mid-session on an older addon while
        # the bridge updates: four fields had no kind, three had no host either.
        if len(parts) >= 5:
            seq, kind, host, session_id = parts[0], parts[1], parts[2], parts[3]
            message = FIELD_SEP.join(parts[4:])
        elif len(parts) == 4:
            seq, kind, host, session_id, message = parts[0], "reply", parts[1], parts[2], parts[3]
        elif len(parts) == 3:
            seq, kind, host, session_id, message = parts[0], "reply", "local", parts[1], parts[2]
        else:
            continue

        legacy = len(parts) < 5
        if kind not in OUTBOX_KINDS:
            kind = "reply"
        # A hand-off carries no words, so only a reply needs text.
        if not session_id or (kind == "reply" and not message):
            continue
        # A three-field line from a truncated file reads as seq|session|text and
        # turns the first field into a "session id" like "reply". Only an id that
        # looks like one is worth sending anything to.
        if not SESSION_ID_RE.fullmatch(session_id):
            continue
        # A seq that is not a number cannot be acked or deduped, and int() on it
        # used to raise out of the watcher loop.
        if not seq.isdigit():
            continue

        entries.append(
            {
                "seq": seq,
                "kind": kind,
                "host": host or "local",
                "session_id": session_id,
                "text": message,
                "legacy": legacy,
            }
        )

    return entries


def acked_seq() -> int:
    """The highest entry seq the bridge has settled, for the next payload."""
    return int(_load_state().get("acked_seq") or 0)


def _load_state() -> dict[str, Any]:
    """Dispatch bookkeeping, and what to do when it cannot be trusted.

    The addon only ever appends to its outbox (it has no idea what the bridge did
    with an entry), so this file is the ONLY thing standing between a lost state
    and every queued reply being sent again. A state file that exists but does not
    parse is therefore treated as "assume everything was already sent": a missing
    reply is visible and fixable, a duplicate reply into a live session is not.
    """
    try:
        raw = _STATE_PATH.read_text(encoding="utf-8")
    except OSError:
        return {"dispatched": [], "acked_seq": 0}

    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        backup = _STATE_PATH.with_suffix(".corrupt.json")
        try:
            backup.write_text(raw, encoding="utf-8")
        except OSError:
            pass

        # Put a clean file back immediately. Leaving the damaged one in place made
        # every later round take the "unreadable" path, which meant replies stopped
        # being sent forever until a human deleted a file they did not know about.
        fresh = {"dispatched": [], "acked_seq": 0, "recovered_from": str(backup)}
        try:
            _save_state(fresh)
        except OSError:
            pass

        return {**fresh, "unreadable": True}

    if not isinstance(state, dict):
        return {"dispatched": [], "acked_seq": 0, "unreadable": True}
    state.setdefault("dispatched", [])
    state.setdefault("acked_seq", 0)
    return state


def _save_state(state: dict[str, Any]) -> None:
    """Write the state atomically: a crash mid-write must not lose the dedupe."""
    state_module.atomic_write(_STATE_PATH, json.dumps(state, indent=2))


def _entry_key(entry: dict[str, str]) -> str:
    # The seq counter lives in the addon's per-character SavedVariables, so two
    # characters can both start at 1. Content plus seq is the honest identity.
    return "|".join(
        (entry["seq"], entry.get("kind", "reply"), entry.get("host", ""), entry["session_id"], entry["text"])
    )


# Ways to put text where the player can paste it. First one installed wins.
CLIPBOARD_COMMANDS = (
    ["wl-copy"],
    ["xclip", "-selection", "clipboard"],
    ["xsel", "--clipboard", "--input"],
    ["pbcopy"],
)


def session_env() -> dict[str, str]:
    """The display env a clipboard or notifier needs.

    A bridge started by a service, a cron job, or an ssh command has no
    WAYLAND_DISPLAY, and wl-copy exits 1 without one. The socket is right there
    on disk, so find it instead of refusing to copy.
    """
    env = dict(os.environ)
    if not env.get("XDG_RUNTIME_DIR"):
        runtime = Path(f"/run/user/{os.getuid()}")
        if runtime.exists():
            env["XDG_RUNTIME_DIR"] = str(runtime)
    if not env.get("WAYLAND_DISPLAY") and not env.get("DISPLAY"):
        candidates = sorted(Path("/run/user") .glob(f"{os.getuid()}/wayland-*"))
        if candidates:
            env["WAYLAND_DISPLAY"] = candidates[0].name
    if not env.get("DBUS_SESSION_BUS_ADDRESS"):
        bus = Path(f"/run/user/{os.getuid()}/bus")
        if bus.exists():
            env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus}"
    return env


def _clipboard(text: str) -> str:
    """Put text on the clipboard, if this machine has a clipboard tool.

    The text goes through a temp FILE rather than a pipe: wl-copy forks a child
    that keeps the selection alive, and that child would hold a pipe open until
    the timeout, which reads as a failure even though the copy worked.
    """
    env = session_env()
    for command in CLIPBOARD_COMMANDS:
        if not shutil.which(command[0]):
            continue

        handle, path = tempfile.mkstemp(prefix="hermes-handoff-", suffix=".txt")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as source:
                source.write(text)
            with open(path, "r", encoding="utf-8") as source:
                done = subprocess.run(
                    command,
                    stdin=source,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                    env=env,
                )
        except (OSError, subprocess.SubprocessError):
            continue
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

        if done.returncode == 0:
            return command[0]
    return ""


def hand_off(entry: dict[str, str], *, keybind: str = "SUPER+SHIFT+H") -> dict[str, Any]:
    """Answer a hand-off request from the board.

    Hermes has no outside API to focus a session in the desktop app, and faking
    one by pretending to read the player's screen would be worse than useless.
    So the honest version: the session id lands on the clipboard and a desktop
    notification says which one, and the player pastes it into the app they
    already have open.
    """
    from . import notify as notify_module

    session_id = entry.get("session_id", "")
    tool = _clipboard(session_id)

    try:
        notify_module.notify_desktop(
            {
                "kind": "handoff",
                "id": session_id,
                "title": f"hand-off: {session_id}",
                "project": entry.get("project", ""),
                "detail": "session id is on your clipboard" if tool else "copy the id from the board",
            }
        )
        notified = True
    except Exception:  # noqa: BLE001 - a notification is a courtesy, never a failure
        notified = False

    return {"ok": True, "session_id": session_id, "clipboard": tool, "notified": notified, "keybind": keybind}


def _pid_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reconcile_pending(state: dict[str, Any]) -> tuple[list[str], list[dict[str, str]]]:
    """Settle the CLI fallbacks that were still running when we last looked.

    A detached `hermes chat --resume` takes minutes and can refuse the reply in
    its first second. Until it is seen to finish, its entry is neither delivered
    nor failed: it stays unacknowledged, so it is retried rather than lost.
    """
    from . import backend as backend_module

    pending = state.get("pending") or {}
    delivered: list[str] = []
    refused: list[dict[str, str]] = []

    for key, item in list(pending.items()):
        if _pid_running(int(item.get("pid") or 0)):
            continue

        log = str(item.get("log") or "")
        reason = backend_module.refusal_reason(Path(log)) if log else ""
        entry = {field: item.get(field, "") for field in ("seq", "kind", "host", "session_id", "text")}
        entry.setdefault("kind", "reply")

        exit_code = item.get("exit")
        if reason or (exit_code is not None and int(exit_code or 0) != 0):
            refused.append({**entry, "outcome": f"failed: the CLI turn was refused ({reason or 'see the log'})"})
        else:
            delivered.append(key)

        pending.pop(key, None)

    state["pending"] = pending
    return delivered, refused


def dispatch(
    entries: Iterable[dict[str, str]],
    *,
    channel: str = "auto",
    state: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Send queued replies through the channels that match where the session lives."""
    from . import hosts as host_module

    state = state if state is not None else _load_state()
    seen = set(state.get("dispatched", []))
    results: list[dict[str, str]] = []

    if state.get("unreadable"):
        # This round cannot know what was already handled, so nothing is sent: a
        # duplicate reply into a live session is worse than a delayed one. The
        # clean state written on load means the next round works normally.
        return [
            {**entry, "outcome": f"skipped: dispatch state was unreadable ({state.get('recovered_from', 'no copy')})"}
            for entry in entries
        ]

    # First settle whatever the previous round left running.
    delivered, refused_earlier = _reconcile_pending(state)
    seen.update(delivered)
    results.extend(refused_earlier)

    # ...and treat what is still running as spoken for. A pending entry is not in
    # `dispatched` by design - it has not been delivered - so without this the
    # watcher launches another CLI turn for the same reply on every round and
    # overwrites the pid of the turn already running. The reply then lands in the
    # same live session once per round, which is the one thing this module's own
    # notes call unfixable, and no round can ever settle the first child.
    inflight = set(state.get("pending") or {})

    for entry in entries:
        key = _entry_key(entry)
        if key in seen or key in inflight:
            continue

        host = (entry.get("host") or "").strip()
        outcome = "sent"

        # Control messages are their own kind, never inferred from the text. The
        # text form is honoured only for an entry from an addon that predates the
        # kind field: otherwise a player who replies "!focus" gets a clipboard
        # instead of an answer, which a gate caught only on the addon side.
        if entry.get("kind") == "focus" or (entry.get("legacy") and entry.get("text", "").strip() == "!focus"):
            handed = hand_off(entry)
            outcome = "handed_off" if handed.get("clipboard") else "handed_off_no_clipboard"
        elif host and host != "local":
            sent = host_module.reply(host, entry["session_id"], entry["text"])
            outcome = "sent_remote" if sent.get("ok") else f"failed: {sent.get('error')}"
        else:
            try:
                if channel == "cli":
                    raise RuntimeError("cli channel requested")
                backend.submit_reply(entry["session_id"], entry["text"])
            except Exception:  # noqa: BLE001 - any RPC failure means the CLI path
                try:
                    verdict = backend.submit_reply_cli(entry["session_id"], entry["text"])
                except Exception as exc:  # noqa: BLE001
                    outcome = f"failed: {exc}"
                else:
                    if verdict.get("ok") is False:
                        outcome = f"failed: the CLI turn was refused ({verdict.get('reason')})"
                    elif verdict.get("ok") is None:
                        # Still running: journal it and do NOT ack it. A reply is not
                        # delivered until the child has been seen to finish.
                        state.setdefault("pending", {})[key] = {
                            "pid": verdict.get("pid"),
                            "log": str(verdict.get("log_path") or ""),
                            "exit": verdict.get("exit"),
                            "seq": entry["seq"],
                            "kind": entry["kind"],
                            "host": entry["host"],
                            "session_id": entry["session_id"],
                            "text": entry["text"],
                            "at": time.time(),
                        }
                        outcome = "sent_via_cli_pending"
                    else:
                        outcome = "sent_via_cli"

        # Failed and pending entries both stay unsettled: a failure deserves a
        # retry, and a pending one has not finished being a question.
        if outcome.startswith("failed") or outcome == "sent_via_cli_pending":
            results.append({**entry, "outcome": outcome})
            continue

        seen.add(key)
        results.append({**entry, "outcome": outcome})

    state["dispatched"] = sorted(seen)[-500:]

    # Only the contiguous settled prefix may be acknowledged. A failed entry
    # below a later success would otherwise be acked and dropped by the addon
    # with nothing left to retry it: the reply is simply lost.
    settled = [int(e["seq"] or 0) for e in entries if _entry_key(e) in seen]
    pending = [int(e["seq"] or 0) for e in entries if _entry_key(e) not in seen]
    ceiling = (min(pending) - 1) if pending else None
    if settled:
        mark = max(settled)
        if ceiling is not None:
            mark = min(mark, ceiling)
        state["acked_seq"] = max(int(state.get("acked_seq") or 0), mark)

    _save_state(state)
    return results


def inbox(*, addon_dir: Path | None = None, dispatch_replies: bool = False, channel: str = "auto") -> dict:
    directory = addon_dir or pick_addon_dir()
    if directory is None:
        return {"error": "no WoW client found (set HERMES_WOW_ADDON_DIR)", "entries": []}

    path = savedvars_path(directory)
    if path is None:
        return {"error": f"no SavedVariables for {ADDON_NAME} yet under {directory}", "entries": []}

    entries = read_outbox(path)
    result: dict[str, Any] = {"path": str(path), "entries": entries}
    if dispatch_replies:
        result["results"] = dispatch(entries, channel=channel)
    return result


def watch(
    *,
    addon_dir: Path | None = None,
    interval: float = 10.0,
    channel: str = "auto",
    once: bool = False,
    iterations: int | None = None,
    notify_desktop: bool = True,
    notify_platforms: Iterable[str] = (),
    notify_enabled: bool = True,
    limit: int = 15,
    days: float = 3.0,
    hosts_enabled: bool = True,
    host_refresh_interval: float = 60.0,
) -> list[dict]:
    """Publish status out, dispatch replies in, ring the bell, forever (or N rounds)."""
    directory = addon_dir or pick_addon_dir()
    if directory is None:
        return [{"error": "no WoW client found (set HERMES_WOW_ADDON_DIR)"}]

    from . import notify as notifier
    from . import hosts as host_module

    state = _load_state()
    reports: list[dict] = []
    count = 0
    stop_refresher = threading.Event()

    def _refresh_hosts() -> None:
        """Keep the host cache warm off the publish path.

        A dead host can take its full timeout, so this never runs inline: the
        board is published from cache and the remote rows catch up when the
        fetch returns.
        """
        while not stop_refresher.is_set():
            configured = [host for host in host_module.load_hosts() if host.enabled]
            if configured:
                try:
                    # Fetch first, lock only for the write: the lock exists to
                    # serialise the read-modify-write, not to serialise ssh.
                    results = host_module.fetch_all(
                        configured, limit=limit, days=days, ttl=0, cache=host_module.load_cache()
                    )

                    def refresh(cache: dict) -> None:
                        _rows, _status, cache_out = host_module.merge([], results, cache=cache)
                        cache.clear()
                        cache.update(cache_out)

                    host_module.mutate_cache(refresh)
                except Exception:  # noqa: BLE001 - never let a host kill the watcher
                    pass
            stop_refresher.wait(max(15.0, host_refresh_interval))

    refresher = None
    if hosts_enabled and host_module.load_hosts():
        refresher = threading.Thread(target=_refresh_hosts, daemon=True, name="hermes-wow-hosts")
        refresher.start()

    while True:
        report: dict[str, Any] = {"at": time.time()}
        data = board(limit=limit, days=days)

        try:
            report["published"] = publish(
                directory, data, hosts_enabled=hosts_enabled, host_ttl=1e9 if refresher else 120.0
            )
        except Exception as exc:  # noqa: BLE001
            report["publish_error"] = str(exc)

        if notify_enabled:
            # Notify from what was PUBLISHED, which includes the merged remote
            # rows: a needs-you on another machine used to be silent.
            published_rows = (report.get("published") or {}).get("rows")
            report["notify"] = notifier.transitions(
                published_rows if published_rows is not None else (data.get("sessions", []) or []),
                desktop=notify_desktop,
                platforms=notify_platforms,
            )

        path = savedvars_path(directory)
        if path is not None:
            entries = read_outbox(path)
            seen = set(state.get("dispatched", []))
            fresh = [entry for entry in entries if _entry_key(entry) not in seen]
            if fresh:
                # Guarded like the publish above: a state that cannot be written
                # (full disk, read-only home) must not stop the watcher.
                try:
                    report["dispatched"] = dispatch(fresh, channel=channel, state=state)
                except Exception as exc:  # noqa: BLE001
                    report["dispatch_error"] = str(exc)
            report["outbox"] = len(entries)

        if once or iterations is not None:
            reports.append(report)
        else:
            # A watcher that runs for days would otherwise hold every round it
            # has ever done, sessions and previews included.
            reports.append(report)
            del reports[:-10]
        count += 1

        if once or (iterations is not None and count >= iterations):
            break
        time.sleep(interval)

    stop_refresher.set()
    return reports
