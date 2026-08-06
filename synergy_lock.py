"""
synergy_lock.py — keeps every Synergy-connected machine locked together.

Ported from the synergy-lock Go project. This machine's role is set by
config.py's SYNERGY_ROLE:

  'host'   — this is the Synergy server machine. Watches for the local
             session lock/unlock event (Windows only for real detection;
             falls back to a stdin dev-mode stub elsewhere) and broadcasts
             it to every connected client over a WebSocket.
  'client' — this is a Synergy client machine. Connects to the host and
             locks itself on "lock". Never unlocks itself automatically —
             see the synergy-lock README for why (no credentials are ever
             stored or bypassed). While the host is unlocked, also holds
             this machine's display awake, releasing that hold on lock.
  'none'   — feature disabled.

Both roles are started as daemon threads from server.py and run for the
life of the process — nothing here is user-facing HTTP, it just piggybacks
on the same long-running process so only one script needs to run per
machine.
"""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse


# ── Host role ────────────────────────────────────────────────────────────

class _Registry:
    """Thread-safe set of connected client WebSocket connections."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clients = set()

    def add(self, ws):
        with self._lock:
            self._clients.add(ws)

    def discard(self, ws):
        with self._lock:
            self._clients.discard(ws)

    def __len__(self):
        with self._lock:
            return len(self._clients)

    def broadcast(self, msg):
        data = json.dumps(msg)
        with self._lock:
            dead = []
            for ws in self._clients:
                try:
                    ws.send(data)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self._clients.discard(ws)
            count = len(self._clients)
        print(f'synergy-lock: broadcast {msg["type"]!r} to {count} client(s)')


def start_host(port, token):
    """Starts the WebSocket broadcast server and the session watcher, each on its own daemon thread."""
    from websockets.sync.server import serve as ws_serve

    registry = _Registry()

    def handler(websocket):
        if token:
            query = urllib.parse.urlparse(websocket.request.path).query
            supplied = urllib.parse.parse_qs(query).get('token', [None])[0]
            if supplied != token:
                websocket.close(code=1008, reason='unauthorized')
                return
        registry.add(websocket)
        print(f'synergy-lock: client connected ({len(registry)} total)')
        try:
            for _ in websocket:
                pass  # clients don't send anything meaningful; just detect disconnect
        except Exception:
            pass
        finally:
            registry.discard(websocket)
            print(f'synergy-lock: client disconnected ({len(registry)} total)')

    def serve_forever():
        with ws_serve(handler, '0.0.0.0', port) as server:
            print(f'synergy-lock: host listening on ws://0.0.0.0:{port}')
            server.serve_forever()

    threading.Thread(target=serve_forever, daemon=True, name='synergy-lock-host-ws').start()

    def on_lock():
        print('synergy-lock: SESSION LOCK detected -> broadcasting lock')
        registry.broadcast({'type': 'lock'})

    def on_unlock():
        print('synergy-lock: SESSION UNLOCK detected -> broadcasting unlock (informational only; clients never auto-unlock)')
        registry.broadcast({'type': 'unlock'})

    threading.Thread(target=lambda: _run_watcher(on_lock, on_unlock), daemon=True, name='synergy-lock-watcher').start()


def _run_watcher(on_lock, on_unlock):
    if os.name == 'nt':
        _run_windows_watcher(on_lock, on_unlock)
    else:
        _run_dev_watcher(on_lock, on_unlock)


def _run_dev_watcher(on_lock, on_unlock):
    """Non-Windows dev-mode stub: real lock detection is Windows-only, matching the host's intended platform."""
    print("synergy-lock [dev mode]: real session-lock detection is Windows-only.")
    print("synergy-lock [dev mode]: type 'lock' or 'unlock' + Enter to simulate an event.")
    for line in sys.stdin:
        cmd = line.strip()
        if cmd == 'lock':
            on_lock()
        elif cmd == 'unlock':
            on_unlock()


def _run_windows_watcher(on_lock, on_unlock):
    """
    Blocks forever pumping a hidden window's message queue, watching for
    WM_WTSSESSION_CHANGE. Runs on its own dedicated thread — Python threads
    stay bound to one OS thread for their whole life, so (unlike the Go
    version) no explicit thread-pinning is needed for GetMessage to keep
    receiving messages.
    """
    import win32con
    import win32gui
    import win32ts

    WM_WTSSESSION_CHANGE = 0x02B1
    WTS_SESSION_LOCK = 0x7
    WTS_SESSION_UNLOCK = 0x8
    NOTIFY_FOR_THIS_SESSION = 0

    def wnd_proc(hwnd, msg, wparam, lparam):
        if msg == WM_WTSSESSION_CHANGE:
            if wparam == WTS_SESSION_LOCK:
                on_lock()
            elif wparam == WTS_SESSION_UNLOCK:
                on_unlock()
            return 0
        if msg == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

    wc = win32gui.WNDCLASS()
    wc.lpfnWndProc = wnd_proc
    wc.lpszClassName = 'MyTodaySynergyLockWatcher'
    wc.hInstance = win32gui.GetModuleHandle(None)
    class_atom = win32gui.RegisterClass(wc)

    hwnd = win32gui.CreateWindow(
        class_atom, 'MyTodaySynergyLockWatcher', 0,
        0, 0, 0, 0, 0, 0, wc.hInstance, None,
    )

    win32ts.WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION)
    try:
        win32gui.PumpMessages()
    finally:
        win32ts.WTSUnRegisterSessionNotification(hwnd)


# ── Client role ──────────────────────────────────────────────────────────

def start_client(host_addr, token):
    """Starts the reconnecting WebSocket client on a daemon thread. Assumes unlocked at launch, so the keep-awake hold is in place from the start rather than only after the first lock/unlock round trip."""
    threading.Thread(target=lambda: _client_loop(host_addr, token), daemon=True, name='synergy-lock-client').start()
    _start_keepawake()


def _client_loop(host_addr, token):
    from websockets.sync.client import connect

    url = f'ws://{host_addr}'
    if token:
        url += f'?token={urllib.parse.quote(token)}'

    while True:
        try:
            with connect(url) as ws:
                print('synergy-lock: connected to host')
                for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        print(f'synergy-lock: bad message: {raw!r}')
                        continue
                    mtype = msg.get('type')
                    if mtype == 'lock':
                        print('synergy-lock: received lock command -- locking now')
                        _lock_local_machine()
                        # Let the display return to normal power management while
                        # locked -- nobody's there to need it on.
                        _stop_keepawake()
                    elif mtype == 'unlock':
                        # Intentionally ignored for unlocking: this client never
                        # auto-unlocks. Still used as the signal to hold the
                        # display awake again.
                        print('synergy-lock: received unlock notice (ignored for unlocking; holding display awake)')
                        _start_keepawake()
            print('synergy-lock: disconnected from host, reconnecting in 2s')
            time.sleep(2)
        except Exception as e:
            print(f'synergy-lock: connect failed ({e}), retrying in 5s')
            time.sleep(5)


def _lock_local_machine():
    if os.name == 'nt':
        if not ctypes.windll.user32.LockWorkStation():
            print(f'synergy-lock: LockWorkStation failed: {ctypes.GetLastError()}')
    elif sys.platform == 'darwin':
        cg_session = '/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession'
        try:
            if subprocess.run([cg_session, '-suspend']).returncode == 0:
                return
        except FileNotFoundError:
            pass
        subprocess.run(['/usr/bin/pmset', 'displaysleepnow'])
    else:
        candidates = [
            ['loginctl', 'lock-session'],
            ['xdg-screensaver', 'lock'],
            ['gnome-screensaver-command', '--lock'],
            ['dm-tool', 'lock'],
            ['xscreensaver-command', '-lock'],
        ]
        for cmd in candidates:
            try:
                if subprocess.run(cmd).returncode == 0:
                    return
            except FileNotFoundError:
                continue
        print('synergy-lock: no supported lock command succeeded')


# ── Client keep-awake ────────────────────────────────────────────────────
# While the host is unlocked, hold this machine's display awake (so it
# doesn't go dark just because Synergy input isn't currently pointed at
# it); release the hold once the host locks (nobody's there to need it on).

_ES_CONTINUOUS = 0x80000000
_ES_DISPLAY_REQUIRED = 0x00000002

_keepawake_lock = threading.Lock()
_keepawake_stop_event = None   # threading.Event, Windows
_keepawake_proc = None         # subprocess.Popen, macOS/Linux


def _start_keepawake():
    global _keepawake_stop_event, _keepawake_proc
    with _keepawake_lock:
        if os.name == 'nt':
            if _keepawake_stop_event is not None:
                return
            stop_event = threading.Event()
            _keepawake_stop_event = stop_event
            threading.Thread(target=_windows_keepawake_loop, args=(stop_event,), daemon=True, name='synergy-lock-keepawake').start()
        elif sys.platform == 'darwin':
            if _keepawake_proc is not None:
                return
            try:
                _keepawake_proc = subprocess.Popen(['/usr/bin/caffeinate', '-d'])
            except FileNotFoundError:
                pass
        else:
            if _keepawake_proc is not None:
                return
            try:
                _keepawake_proc = subprocess.Popen([
                    'systemd-inhibit', '--what=idle:sleep',
                    '--who=myToday-synergy-lock', '--why=host unlocked, keeping display on',
                    'sleep', 'infinity',
                ])
            except FileNotFoundError:
                pass


def _stop_keepawake():
    global _keepawake_stop_event, _keepawake_proc
    with _keepawake_lock:
        if _keepawake_stop_event is not None:
            _keepawake_stop_event.set()
            _keepawake_stop_event = None
        if _keepawake_proc is not None:
            _keepawake_proc.terminate()
            try:
                _keepawake_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                _keepawake_proc.kill()
            _keepawake_proc = None


def _windows_keepawake_loop(stop_event):
    """Re-asserts ES_DISPLAY_REQUIRED every 15s, since the state only applies while asserted."""
    set_state = ctypes.windll.kernel32.SetThreadExecutionState
    set_state(_ES_CONTINUOUS | _ES_DISPLAY_REQUIRED)
    try:
        while not stop_event.wait(15):
            set_state(_ES_CONTINUOUS | _ES_DISPLAY_REQUIRED)
    finally:
        set_state(_ES_CONTINUOUS)
