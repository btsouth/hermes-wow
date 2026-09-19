"""Guided, reversible per-user Linux installation. Never edits Hermes data."""
from __future__ import annotations

import json
import fcntl
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from . import roster, wowclient

ROOT = Path(__file__).resolve().parent.parent
BASE = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'hermes-wow'
MANIFEST = BASE / 'install.json'
UNIT = Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'systemd/user/hermes-wow.service'
LAUNCHER = Path.home() / '.local/bin/hermes-wow'
MARKER = '# Managed by hermes-wow setup'


@contextmanager
def lifecycle_lock():
    BASE.mkdir(parents=True, exist_ok=True)
    path = BASE / 'lifecycle.lock'
    with path.open('a+') as handle:
        inherited = os.environ.get('HERMES_WOW_LOCK_FD')
        if inherited:
            try:
                descriptor = int(inherited)
                expected, actual = os.fstat(handle.fileno()), os.fstat(descriptor)
                if (expected.st_dev, expected.st_ino) != (actual.st_dev, actual.st_ino):
                    raise ValueError('wrong lock inode')
                # A fresh descriptor must conflict with the inherited holder.
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise ValueError('inherited lock is not held')
            except (ValueError, OSError) as exc:
                raise RuntimeError('Invalid inherited lifecycle lock.') from exc
            yield descriptor
            return
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another setup, update, or uninstall is running. Try again when it finishes.') from exc
        yield handle.fileno()


def load_manifest():
    if not MANIFEST.exists():
        return None
    data = json.loads(MANIFEST.read_text())
    if data.get('owner') != 'hermes-wow' or data.get('version') != 1:
        raise RuntimeError(f'Unrecognized installation record: {MANIFEST}')
    return data


def systemctl(*args, check=True):
    result = subprocess.run(['systemctl', '--user', *args], capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"systemctl --user {' '.join(args)} failed. Check your user login session and journalctl --user -u hermes-wow.service. {result.stderr.strip()}")
    return result


def quote(value):
    # systemd expands specifiers and ExecStart dollars even inside quotes.
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$').replace('\n', '\\n').replace('\r', '\\r') + '"'


def choose_addon(explicit=None, yes=False):
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
    else:
        choices = wowclient.find_addon_dirs()
        if not choices:
            if not yes and sys.stdin.isatty():
                entered = input('WoW Interface/AddOns directory: ').strip()
                if entered:
                    return choose_addon(entered, yes=yes)
            raise RuntimeError('No WoW installation found. Run setup --addon-dir "/path/to/World of Warcraft/_version_/Interface/AddOns".')
        if len(choices) > 1:
            if yes or not sys.stdin.isatty():
                raise RuntimeError('Multiple WoW installations found. Choose with --addon-dir:\n' + '\n'.join(map(str, choices)))
            for i, path in enumerate(choices, 1):
                print(f'{i}. {path}')
            try:
                index = int(input('WoW installation number: ')) - 1
                if index < 0:
                    raise ValueError()
                candidate = choices[index]
            except (ValueError, IndexError, EOFError):
                raise RuntimeError('No installation selected. Run setup with --addon-dir.') from None
        else:
            candidate = choices[0]
    if not candidate.is_dir() or candidate.name != 'AddOns' or candidate.parent.name != 'Interface':
        raise RuntimeError(f'Expected an existing Interface/AddOns directory: {candidate}')
    return candidate.resolve()


def configure(*, addon_dir=None, hermes_home=None, yes=False, force=False, no_start=False):
    with lifecycle_lock():
        return _configure(addon_dir=addon_dir, hermes_home=hermes_home, yes=yes, force=force, no_start=no_start)


def _configure(*, addon_dir=None, hermes_home=None, yes=False, force=False, no_start=False):
    if sys.platform != 'linux':
        raise RuntimeError('Automatic setup currently supports Linux with a systemd user session.')
    old = load_manifest()
    target = choose_addon(addon_dir or (old or {}).get('addon_dir'), yes)
    home = Path(hermes_home or (old or {}).get('hermes_home') or os.environ.get('HERMES_HOME', Path.home() / '.hermes')).expanduser().resolve()
    if not (home / 'state.db').is_file() and not hermes_home and not yes and sys.stdin.isatty():
        entered = input(f'No Hermes session store at {home}. Hermes home directory (blank to cancel): ').strip()
        if entered:
            home = Path(entered).expanduser().resolve()
    python = home / 'hermes-agent/venv/bin/python'
    if not python.is_file():
        python = Path(sys.executable)
    dependency = subprocess.run([str(python), '-c', 'from websockets.sync.client import connect'], capture_output=True)
    if dependency.returncode:
        raise RuntimeError(f'Hermes websocket dependency is missing in {python}. Repair your Hermes environment before setup.')
    try:
        data = roster.board(db_path=home / 'state.db', limit=15, days=3)
    except Exception as exc:
        raise RuntimeError(f'Hermes session store is not readable: {exc}') from exc
    if data.get('error'):
        raise RuntimeError(f"Hermes session store is not usable: {data['error']}. Run Hermes first or pass --hermes-home.")
    if not shutil.which('systemctl'):
        raise RuntimeError('systemctl is missing. Automatic setup requires a systemd user session.')
    systemctl('show-environment')
    fragment = systemctl('show', 'hermes-wow.service', '--property=FragmentPath', '--value').stdout.strip()
    if fragment and Path(fragment).resolve() != UNIT.resolve():
        raise RuntimeError(f'Another hermes-wow.service already exists at {fragment}; nothing changed.')
    dest = target / 'HermesAI'
    for path in (UNIT, LAUNCHER):
        if path.is_symlink() or path.exists() and MARKER not in path.read_text():
            raise RuntimeError(f'Refusing to replace an unmanaged file: {path}')
    if any('\n' in str(path) or '\r' in str(path) for path in (target, home, ROOT, python)):
        raise RuntimeError('Installation paths cannot contain line breaks.')
    if dest.is_symlink() or dest.exists() and any(path.is_symlink() for path in dest.rglob('*')):
        raise RuntimeError(f'Refusing to overwrite a linked addon: {dest}')
    owned = old and old.get('addon_dir') == str(target)
    if dest.exists() and not owned and not force:
        if yes or not sys.stdin.isatty() or input(f'Back up and replace existing addon at {dest}? [y/N] ').lower() != 'y':
            raise RuntimeError('Existing addon is unmanaged. Use --force to back it up and replace it.')
    BASE.mkdir(parents=True, exist_ok=True)
    backup = None
    if dest.exists():
        backup = Path(tempfile.mkdtemp(prefix='addon-backup-', dir=BASE)) / 'HermesAI'
        shutil.copytree(dest, backup, symlinks=True)
    originals = {path: path.read_bytes() if path.exists() else None for path in (UNIT, LAUNCHER, MANIFEST)}
    was_active = systemctl('is-active', 'hermes-wow.service', check=False).returncode == 0
    was_enabled = systemctl('is-enabled', 'hermes-wow.service', check=False).returncode == 0
    try:
        systemctl('stop', 'hermes-wow.service', check=False)
        wowclient.install(target, force=True)
        wowclient.publish(target, data, hosts_enabled=False)
        UNIT.parent.mkdir(parents=True, exist_ok=True)
        health = BASE / 'service-health.json'
        health.unlink(missing_ok=True)
        command = [str(python), '-c', 'import sys; sys.path.insert(0, sys.argv.pop(1)); from wowmode.cli import main; raise SystemExit(main())', str(ROOT), 'wow', 'watch', '--addon-dir', str(target), '--quiet', '--health-file', str(health)]
        environment = {'HERMES_HOME': str(home)}
        environment.update({name: str(Path(os.environ.get(name) or Path.home() / default).resolve()) for name, default in (('XDG_STATE_HOME', '.local/state'), ('XDG_CONFIG_HOME', '.config'), ('XDG_DATA_HOME', '.local/share'))})
        env_lines = ''.join('Environment=' + quote(key + '=' + value).replace('$$', '$') + '\n' for key, value in environment.items())
        UNIT.write_text(f'{MARKER}\n[Unit]\nDescription=Hermes WoW bridge\n\n[Service]\nType=simple\n{env_lines}ExecStart={" ".join(quote(arg) for arg in command)}\nRestart=on-failure\nRestartSec=10\nUMask=0077\n\n[Install]\nWantedBy=default.target\n')
        import shlex
        LAUNCHER.parent.mkdir(parents=True, exist_ok=True)
        exports = ''.join('export ' + key + '=' + shlex.quote(value) + '\n' for key, value in environment.items())
        LAUNCHER.write_text('#!/bin/sh\n' + MARKER + '\n' + exports + 'exec ' + shlex.quote(str(ROOT / 'bin/hermes-wow')) + ' "$@"\n')
        LAUNCHER.chmod(0o755)
        systemctl('daemon-reload')
        if not no_start:
            systemctl('enable', 'hermes-wow.service')
            systemctl('restart', 'hermes-wow.service')
            deadline = time.monotonic() + 20
            while not health.is_file():
                systemctl('is-active', 'hermes-wow.service')
                if time.monotonic() >= deadline:
                    raise RuntimeError('Bridge started but did not publish its first snapshot. Check journalctl --user -u hermes-wow.service.')
                time.sleep(0.2)
            status = json.loads(health.read_text())
            if not status.get('ok'):
                raise RuntimeError('Bridge could not publish its first snapshot. Check hermes-wow status.')
            systemctl('is-active', 'hermes-wow.service')
            systemctl('is-enabled', 'hermes-wow.service')
        manifest = dict(owner='hermes-wow', version=1, app_root=str(ROOT), addon_dir=str(target), hermes_home=str(home), python=str(python), backup=str(backup) if backup else (old or {}).get('backup'))
        temporary = MANIFEST.with_suffix('.tmp')
        temporary.write_text(json.dumps(manifest, indent=2) + '\n')
        temporary.replace(MANIFEST)
        return manifest
    except Exception:
        systemctl('stop', 'hermes-wow.service', check=False)
        if dest.exists():
            shutil.rmtree(dest)
        if backup:
            shutil.copytree(backup, dest, symlinks=True)
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        systemctl('daemon-reload', check=False)
        systemctl('enable' if was_enabled else 'disable', 'hermes-wow.service', check=False)
        if was_active:
            systemctl('start', 'hermes-wow.service', check=False)
        raise


def uninstall():
    with lifecycle_lock():
        return _uninstall()


def _uninstall():
    manifest = load_manifest()
    if not manifest:
        return False
    for path in (UNIT, LAUNCHER):
        if path.is_symlink() or path.exists() and MARKER not in path.read_text():
            raise RuntimeError(f'Refusing to remove a modified or unmanaged file: {path}')
    fragment = systemctl('show', 'hermes-wow.service', '--property=FragmentPath', '--value').stdout.strip()
    if fragment and Path(fragment).resolve() != UNIT.resolve():
        raise RuntimeError(f'Refusing to stop a foreign service at {fragment}.')
    systemctl('disable', '--now', 'hermes-wow.service')
    UNIT.unlink(missing_ok=True)
    LAUNCHER.unlink(missing_ok=True)
    systemctl('daemon-reload')
    MANIFEST.unlink()
    # Leave code and addon in place: they may contain user edits. No user data is deleted.
    return True


def command(args):
    try:
        if args.command == 'uninstall':
            removed = uninstall()
            print('Automatic startup removed. Addon, runtime, backups, Hermes data and WoW settings were preserved.' if removed else 'No managed installation found.')
        else:
            result = configure(addon_dir=args.addon_dir, hermes_home=args.hermes_home, yes=args.yes, force=args.force, no_start=args.no_start)
            if args.no_start:
                print('Installed for testing; automatic startup was not enabled.')
            else:
                print('Bridge is running and will start when you log in. Restart WoW, enable HermesAI, then type /hermesai. Press Sync to refresh and send queued actions.')
            print(f"Command: {LAUNCHER}\nAddon: {result['addon_dir']}/HermesAI")
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print(f'hermes-wow: {exc}', file=sys.stderr)
        return 1


def status():
    """Report actionable health without printing any session content."""
    try:
        manifest = load_manifest()
        if not manifest:
            print('Not configured. Run hermes-wow setup.')
            return 1
        active = systemctl('is-active', 'hermes-wow.service', check=False).returncode == 0
        enabled = systemctl('is-enabled', 'hermes-wow.service', check=False).returncode == 0
        print(f"Addon: {manifest['addon_dir']}/HermesAI")
        print(f"Bridge: {'running' if active else 'stopped'}; login startup: {'enabled' if enabled else 'disabled'}")
        health_path = BASE / 'service-health.json'
        health = json.loads(health_path.read_text()) if health_path.is_file() else {}
        age = time.time() - float(health.get('at', 0))
        healthy = active and enabled and health.get('ok') and 0 <= age < 60
        if healthy:
            print(f'Last successful sync: {int(age)} seconds ago. In WoW, open /hermesai and press Sync.')
            return 0
        print('Bridge needs attention. Run hermes-wow setup to repair it.')
        print('Logs: journalctl --user -u hermes-wow.service -n 30')
        return 1
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f'Cannot read bridge health: {exc}. Run hermes-wow setup.', file=sys.stderr)
        return 1
