"""Talk to the running Hermes backend the desktop app spawned, without owning it.

Why an RPC path at all: a reply typed over Azeroth should land in the session
that is already open, with its live UI updating, instead of bolting a second
agent process onto the same conversation. The desktop's own backend is a
``hermes serve --host 127.0.0.1 --port 0`` process; loopback WS auth is its
``HERMES_DASHBOARD_SESSION_TOKEN``, which the process carries in its own
environment.

The CLI path (``hermes chat --resume -Q``) stays as the fallback for when no
backend is running or the RPC refuses, so a reply is never lost to a dead
socket.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

from .roster import hermes_home

_SERVE_RE = re.compile(r"serve\b")


def _proc_cmdline(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [part for part in raw.decode("utf-8", "replace").split("\0") if part]


def _proc_env(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    out: dict[str, str] = {}
    for entry in raw.decode("utf-8", "replace").split("\0"):
        if "=" in entry:
            key, value = entry.split("=", 1)
            out[key] = value
    return out


def _listening_port(pid: int) -> int | None:
    try:
        out = subprocess.run(
            ["ss", "-ltnpH"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    for line in out.splitlines():
        if f"pid={pid}," not in line and not line.rstrip().endswith(f"pid={pid}"):
            continue
        match = re.search(r"127\.0\.0\.1:(\d+)", line)
        if match:
            return int(match.group(1))
    return None


def find_backend(home: Path | None = None) -> dict[str, object] | None:
    """Locate a loopback ``hermes serve`` for this HERMES_HOME, or None."""
    home = Path(home or hermes_home()).resolve()

    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        cmdline = _proc_cmdline(pid)
        if not cmdline or not any(_SERVE_RE.fullmatch(part) for part in cmdline):
            continue
        if not _asks_for_a_random_port(cmdline):
            continue

        env = _proc_env(pid)
        # A backend started without HERMES_HOME in its environ is the default
        # profile, which is this home: requiring the variable hid the one backend
        # most players actually have running.
        declared = env.get("HERMES_HOME", "")
        if declared and Path(declared).resolve() != home:
            continue

        port = _listening_port(pid)
        if not port:
            continue

        return {
            "pid": pid,
            "port": port,
            "url": f"ws://127.0.0.1:{port}/api/ws",
            "token": env.get("HERMES_DASHBOARD_SESSION_TOKEN", ""),
            "profile": _profile_from_cmdline(cmdline),
        }
    return None


def _asks_for_a_random_port(cmdline: list[str]) -> bool:
    """Does this process ask the server to pick a port? Both spellings count."""
    for index, argument in enumerate(cmdline):
        if argument == "--port":
            return index + 1 < len(cmdline) and cmdline[index + 1] == "0"
        if argument.startswith("--port="):
            return argument.split("=", 1)[1] == "0"
    return False


def _profile_from_cmdline(cmdline: list[str]) -> str:
    if "--profile" in cmdline:
        index = cmdline.index("--profile")
        if index + 1 < len(cmdline):
            return cmdline[index + 1]
    return ""


def call(method: str, params: dict, *, backend: dict[str, object], timeout: float = 25.0):
    """One JSON-RPC round trip against the backend, or raise RuntimeError."""
    try:
        from websockets.sync.client import connect
    except ImportError as exc:  # pragma: no cover - venv always has websockets
        raise RuntimeError(f"websockets unavailable: {exc}") from exc

    url = str(backend["url"])
    token = str(backend.get("token") or "")
    if token:
        url = f"{url}?token={token}"

    with connect(url, max_size=None, open_timeout=timeout, close_timeout=2) as ws:
        ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}))
        # Bounded: this backend also pushes events, and a flood of them must not
        # spin here until the socket times out.
        for _ in range(200):
            try:
                frame = ws.recv(timeout=timeout)
            except Exception as exc:  # noqa: BLE001 - a closed socket is a failed call
                raise RuntimeError(f"{method}: connection closed ({exc})") from exc

            try:
                message = json.loads(frame)
            except (TypeError, ValueError):
                continue
            if message.get("id") == 1:
                if "error" in message:
                    error = message["error"]
                    detail = error.get("message") if isinstance(error, dict) else error
                    raise RuntimeError(f"{method} refused: {detail}")
                return message.get("result")

        raise RuntimeError(f"{method}: no reply in 200 frames")


def _runtime_session_id(session_id: str, *, backend: dict[str, object]) -> str:
    """Stored session id -> the id this backend currently knows it by.

    ``prompt.submit`` addresses a LIVE session; a stored id is refused outright
    ("session not found"). The desktop app resolves the same pair the same way:
    ``session.active_list`` first, ``session.resume`` to attach what is not live.
    """
    snapshot = call("session.active_list", {}, backend=backend) or {}
    for session in snapshot.get("sessions", []) or []:
        if str(session.get("session_key") or "") == session_id:
            return str(session.get("id") or "")

    resumed = call("session.resume", {"session_id": session_id, "omit_messages": True}, backend=backend) or {}
    runtime_id = str(resumed.get("session_id") or "")
    if not runtime_id:
        raise RuntimeError(f"backend could not resume {session_id}")
    return runtime_id


def submit_reply(session_id: str, text: str, *, backend: dict[str, object] | None = None):
    backend = backend or find_backend()
    if backend is None:
        raise RuntimeError("no running Hermes backend (desktop app not open?)")
    runtime_id = _runtime_session_id(session_id, backend=backend)
    return call("prompt.submit", {"session_id": runtime_id, "text": text}, backend=backend)


def _hermes_bin() -> str:
    candidate = Path.home() / ".local" / "bin" / "hermes"
    return str(candidate) if candidate.exists() else "hermes"


# How long to watch the detached fallback before calling it accepted. A refused
# turn says so in well under a second; a real one runs for minutes.
CLI_GRACE_SECONDS = 1.5


def _tail(path: Path, limit: int = 4096) -> str:
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit))
            return handle.read().decode("utf-8", "replace")
    except OSError:
        return ""


def refusal_reason(log_path: Path) -> str:
    """The reason a resumed turn refused the reply, if it wrote one.

    The CLI can exit 0 having refused (the session is owned by another window),
    so the log is the only place that says a reply never arrived.
    """
    for line in _tail(log_path).splitlines():
        if "refusal-reason:" in line:
            return line.split("refusal-reason:", 1)[1].strip()[:120]
    return ""


def submit_reply_cli(
    session_id: str, text: str, *, log_dir: Path | None = None, grace: float = CLI_GRACE_SECONDS
) -> dict:
    """Fallback: a detached one-shot turn resumed on the stored session.

    Returns a verdict rather than a path: reporting a refused turn as sent is how
    a reply gets acknowledged on the wire and trimmed out of the player's outbox
    without ever reaching the session.
    """
    log_dir = log_dir or (hermes_home() / "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    # The id is a path segment here and it comes from a file a player can edit:
    # keep it to characters that cannot walk out of the log directory.
    safe_id = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:80] or "session"
    log_path = log_dir / f"hermes-wow-reply-{safe_id}.log"

    with open(log_path, "ab", buffering=0) as handle:
        handle.write(f"\n--- {session_id} @ {os.path.getmtime(__file__):.0f}\n".encode())
        child = subprocess.Popen(
            [_hermes_bin(), "chat", "-Q", "--resume", session_id, "--oneshot", "-q", text],
            stdout=handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=os.path.expanduser("~"),
        )

    # The child holds its own descriptor, so closing ours here is what stops a
    # long-running watcher from leaking one per reply. Then watch it briefly: a
    # refused turn fails fast, and calling that a success loses the reply.
    deadline = time.monotonic() + max(0.0, grace)
    while time.monotonic() < deadline and child.poll() is None:
        time.sleep(0.1)

    reason = refusal_reason(log_path)
    exit_code = child.poll()
    if exit_code is not None and (exit_code != 0 or reason):
        return {
            "ok": False,
            "log_path": log_path,
            "pid": child.pid,
            "exit": exit_code,
            "reason": reason or f"exited {exit_code}",
        }

    return {
        "ok": True if exit_code is not None else None,
        "log_path": log_path,
        "pid": child.pid,
        "exit": exit_code,
        "reason": reason,
    }
