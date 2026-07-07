#!/usr/bin/env python3
"""
server.py — serves the calendar HTML and proxies Slack API calls to bypass CORS.

Usage:
    python3 server.py

Then open http://localhost:8080 in your browser.
"""

import base64
import http.server
import re
import subprocess
import sys
import urllib.request
import urllib.parse
import json
import os
import ssl
import subprocess
import tempfile
import shutil
import threading
import time
import datetime
import uuid
import xml.etree.ElementTree as ET
import gzip

import socketserver

# When frozen by PyInstaller, bundled read-only files live in sys._MEIPASS;
# user-editable files (config.py, feeds.json) live beside the executable.
import sys as _sys
if getattr(_sys, 'frozen', False):
    BUNDLE_DIR = _sys._MEIPASS
    BASE_DIR   = os.path.dirname(_sys.executable)
    # config.py lives beside the exe, not inside _internal — add it to the path.
    _sys.path.insert(0, BASE_DIR)
    # Playwright's bundled driver looks for browsers in .local-browsers relative
    # to itself. Set this early so both --install and sync_playwright() agree.
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = os.path.join(
        BUNDLE_DIR, 'playwright', 'driver', 'package', '.local-browsers'
    )
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    BASE_DIR   = BUNDLE_DIR

HTML_FILE = os.path.join(BUNDLE_DIR, 'calendar.html')

# --install must run before importing config so setup.bat works even when the
# user hasn't created config.py yet (which is the normal first-run order).
if '--install' in _sys.argv:
    print('Installing Playwright browser (Chromium)...')
    _ca_tmp = None
    try:
        if getattr(_sys, 'frozen', False):
            _node_name = 'node.exe' if os.name == 'nt' else 'node'
            _node_exe  = os.path.join(BUNDLE_DIR, 'playwright', 'driver', _node_name)
            _cli_js    = os.path.join(BUNDLE_DIR, 'playwright', 'driver', 'package', 'cli.js')
            if not os.path.exists(_node_exe):
                raise FileNotFoundError(f'Playwright driver not found at: {_node_exe}')
            _cmd = [_node_exe, _cli_js, 'install', 'chromium']
        else:
            from playwright._impl._driver import compute_driver_executable
            _node_exe, _cli_js = compute_driver_executable()
            _cmd = [str(_node_exe), str(_cli_js), 'install', 'chromium']
        _env = dict(os.environ)
        if os.name == 'nt':
            _ps = (
                '$seen = @{}; '
                '@("Cert:\\\\LocalMachine\\\\Root","Cert:\\\\LocalMachine\\\\CA","Cert:\\\\CurrentUser\\\\Root") | '
                'ForEach-Object { '
                '  Get-ChildItem $_ -ErrorAction SilentlyContinue | ForEach-Object { '
                '    if (-not $seen[$_.Thumbprint]) { '
                '      $seen[$_.Thumbprint] = $true; '
                '      $bytes = $_.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert); '
                '      [System.Convert]::ToBase64String($bytes) } } }'
            )
            _res = subprocess.run(['powershell', '-Command', _ps], capture_output=True, text=True, timeout=30)
            if _res.returncode == 0 and _res.stdout.strip():
                _pem = []
                for _b64 in _res.stdout.strip().split('\n'):
                    _b64 = _b64.strip()
                    if _b64:
                        _pem.append('-----BEGIN CERTIFICATE-----')
                        for _i in range(0, len(_b64), 64):
                            _pem.append(_b64[_i:_i+64])
                        _pem.append('-----END CERTIFICATE-----')
                _ca_tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
                _ca_tmp.write('\n'.join(_pem))
                _ca_tmp.close()
                _env['NODE_EXTRA_CA_CERTS'] = _ca_tmp.name
                print(f'SSL: passing {_pem.count("-----BEGIN CERTIFICATE-----")} Windows CA certs to Node')
        subprocess.run(_cmd, check=True, env=_env)
        _bin = 'myToday.exe' if os.name == 'nt' else 'myToday'
        print(f'Done. You can now run {_bin}')
    except Exception as _e:
        print(f'Install failed: {_e}')
        _sys.exit(1)
    finally:
        if _ca_tmp:
            try:
                os.unlink(_ca_tmp.name)
            except Exception:
                pass
    _sys.exit(0)

try:
    from config import (
        PROXY_SECRET,
        SLACK_WORKSPACE_ID, SLACK_WORKSPACE_DOMAIN, GITHUB_PAT, PAGERDUTY_TOKEN, FINNHUB_KEY,
        MS_CLIENT_ID, MS_TENANT_ID, AUTO_PULL, PULL_INTERVAL,
        GITHUB_API_URL, GITHUB_FEEDS_URL, PAGERDUTY_TEAMS,
    )
    import config as _config
    PORT            = getattr(_config, 'PORT', 8080)
    print(f'Config: {os.path.abspath(_config.__file__)}')
except ModuleNotFoundError:
    print('ERROR: config.py not found.')
    print(f'  Copy config.example.py to config.py in {BASE_DIR} and fill in your credentials.')
    _sys.exit(1)

SLACK_CLIENT_TOKEN = None
SLACK_CLIENT_COOKIE = None
SLACK_VERSION_TS = None
SLACK_CLIENT_CSID = None


RSS_CACHE_TTL = 30 * 60  # 30 minutes
_rss_cache      = {}  # url -> (fetched_at, data)
_rss_cache_lock = threading.Lock()

_ssl_ctx      = None
_ssl_ctx_lock = threading.Lock()

def make_ssl_context():
    global _ssl_ctx
    with _ssl_ctx_lock:
        if _ssl_ctx is not None:
            return _ssl_ctx
        if os.name != 'nt':
            ctx = ssl.create_default_context()
            # When running in a venv, Python may use certifi's CA bundle instead of
            # the system store, so Zscaler's corporate root CA won't be trusted.
            # Explicitly load from system CA paths so the Zscaler cert is included.
            _SYSTEM_CA_PATHS = [
                os.environ.get('SSL_CERT_FILE'),
                '/etc/ssl/certs/ca-certificates.crt',       # Debian/Ubuntu
                '/etc/pki/tls/certs/ca-bundle.crt',         # RHEL/CentOS/Fedora
                '/etc/ssl/ca-bundle.pem',                    # OpenSUSE
                '/usr/local/share/ca-certificates/zscaler.crt',
                '/opt/zscaler/var/cacert.pem',               # Zscaler Client Connector
            ]
            loaded = 0
            for path in _SYSTEM_CA_PATHS:
                if path and os.path.isfile(path):
                    try:
                        ctx.load_verify_locations(path)
                        loaded += 1
                    except Exception:
                        pass
            if loaded:
                print(f'SSL: supplemented default context with {loaded} system CA bundle(s)')
            _ssl_ctx = ctx
            return ctx
        # Try loading from all three Windows stores so Zscaler's root CA is included
        # regardless of which store the admin pushed it to.
        ps_cmd = (
            '$seen = @{}; '
            '@("Cert:\\\\LocalMachine\\\\Root","Cert:\\\\LocalMachine\\\\CA","Cert:\\\\CurrentUser\\\\Root") | '
            'ForEach-Object { '
            '  Get-ChildItem $_ -ErrorAction SilentlyContinue | ForEach-Object { '
            '    if (-not $seen[$_.Thumbprint]) { '
            '      $seen[$_.Thumbprint] = $true; '
            '      $bytes = $_.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert); '
            '      [System.Convert]::ToBase64String($bytes) } } }'
        )
        try:
            result = subprocess.run(
                ['powershell', '-Command', ps_cmd],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout.strip():
                ctx = ssl.create_default_context()
                pem_lines = []
                for b64 in result.stdout.strip().split('\n'):
                    b64 = b64.strip()
                    if b64:
                        pem_lines.append('-----BEGIN CERTIFICATE-----')
                        for i in range(0, len(b64), 64):
                            pem_lines.append(b64[i:i+64])
                        pem_lines.append('-----END CERTIFICATE-----')
                pem = '\n'.join(pem_lines)
                tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.pem', delete=False)
                tmp.write(pem)
                tmp.close()
                try:
                    ctx.load_verify_locations(tmp.name)
                finally:
                    os.unlink(tmp.name)
                cert_count = pem_lines.count('-----BEGIN CERTIFICATE-----')
                print(f'SSL: loaded {cert_count} certs from Windows store (Zscaler included)')
                _ssl_ctx = ctx
                return ctx
        except Exception as e:
            print(f'Windows cert store load failed: {e}, using default SSL')
        ctx = ssl.create_default_context()
        _ssl_ctx = ctx
        return ctx

def finnhub_quotes(symbols):
    ssl_ctx       = make_ssl_context()
    https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
    proxy_url     = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if proxy_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy_url, 'http': proxy_url}), https_handler)
    else:
        opener = urllib.request.build_opener(https_handler)
    results = []
    for sym in symbols:
        url = f'https://finnhub.io/api/v1/quote?symbol={sym}&token={FINNHUB_KEY}'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with opener.open(req, timeout=20) as r:
                q = json.loads(r.read())
            if q.get('c'):
                results.append({'symbol': sym, 'price': q['c'], 'change': round(q.get('d', 0), 4), 'pct': round(q.get('dp', 0), 4)})
        except Exception as e:
            print(f'Finnhub error {sym}: {e}')
    return results

def run_slack_playwright_login(email, password):
    """
    Launches a visible Chromium browser, navigates to the Slack workspace,
    optionally fills email/password, waits for the user to complete MFA,
    then extracts the xoxc- token and session cookies.
    Returns (token, cookie_str, version_ts, csid) — any may be None on failure.
    Raises RuntimeError if playwright is not installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            'playwright not installed. Run:\n'
            '  pip install playwright\n'
            '  playwright install chromium'
        )

    extracted = {'token': None, 'version_ts': None, 'csid': None}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False, args=['--no-sandbox'])
        context = browser.new_context()
        page    = context.new_page()

        def on_request(request):
            url  = request.url
            body = request.post_data or ''
            # capture xoxc token from POST body
            if 'xoxc-' in body and not extracted['token']:
                m = re.search(r'(xoxc-[A-Za-z0-9\-]+)', body)
                if m:
                    extracted['token'] = m.group(1)
            # capture _x_version_ts and _x_csid from query string
            if '/api/client.' in url or '/api/conversations.' in url:
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                if '_x_version_ts' in qs and not extracted['version_ts']:
                    extracted['version_ts'] = qs['_x_version_ts'][0]
                if '_x_csid' in qs and not extracted['csid']:
                    extracted['csid'] = qs['_x_csid'][0]

        page.on('request', on_request)

        page.goto(f'https://{SLACK_WORKSPACE_DOMAIN}/')

        # best-effort auto-fill; user can complete manually if selectors differ
        if email:
            try:
                page.wait_for_selector('input[data-qa="login_email"]', timeout=6000)
                page.fill('input[data-qa="login_email"]', email)
                page.keyboard.press('Enter')
            except Exception:
                pass
        if password:
            try:
                page.wait_for_selector('input[data-qa="login_password"]', timeout=6000)
                page.fill('input[data-qa="login_password"]', password)
                page.keyboard.press('Enter')
            except Exception:
                pass

        # wait up to 5 minutes for login + MFA to complete
        page.wait_for_url('**/client/**', timeout=300_000)

        # let the app fire its initial API calls so we capture the token
        page.wait_for_timeout(4000)

        # fallback: pull token from localStorage if request interception missed it
        if not extracted['token']:
            try:
                extracted['token'] = page.evaluate("""() => {
                    const raw = localStorage.getItem('localConfig_v2');
                    if (!raw) return null;
                    const cfg = JSON.parse(raw);
                    for (const team of Object.values(cfg.teams || {})) {
                        if (team.token && team.token.startsWith('xoxc-'))
                            return team.token;
                    }
                    return null;
                }""")
            except Exception:
                pass

        cookies    = context.cookies()
        cookie_str = '; '.join(f'{c["name"]}={c["value"]}' for c in cookies)
        browser.close()

    return (
        extracted['token'],
        cookie_str,
        extracted['version_ts'],
        extracted['csid'],
    )



class Handler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # suppress all request logging

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # ── Client config ─────────────────────────────────────────────────────
        if parsed.path == '/config':
            body = json.dumps({
                'proxy_secret': PROXY_SECRET,
                'ms_client_id': MS_CLIENT_ID,
                'ms_tenant_id': MS_TENANT_ID,
            }).encode()
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
            return

        # ── Slack proxy ───────────────────────────────────────────────────────
        if parsed.path.startswith('/slack/'):
            # check secret key header
            params = urllib.parse.parse_qs(parsed.query)
            if params.get('_k', [None])[0] != PROXY_SECRET:
                self.send_response(403)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"forbidden"}')
                return
            method    = parsed.path[len('/slack/'):]
            if SLACK_CLIENT_TOKEN is None:
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"not_authed"}')
                return
            # strip _k secret and server-side filter params before forwarding to Slack
            all_params = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            filter_deleted_raw   = all_params.pop('is_user_deleted', None)
            filter_minutes_raw   = all_params.pop('updated_within_minutes', None)
            convert_times_raw    = all_params.pop('convert_timestamps', None)
            debug_raw            = all_params.pop('debug', None)
            all_params.pop('_k', None)
            fwd_params = all_params
            # build proxy-aware opener with Windows cert store support
            proxy_url = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
            ssl_ctx   = make_ssl_context()
            https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
            if proxy_url:
                proxy  = urllib.request.ProxyHandler({'https': proxy_url, 'http': proxy_url})
                opener = urllib.request.build_opener(proxy, https_handler)
            else:
                opener = urllib.request.build_opener(https_handler)
            try:
                CLIENT_METHODS      = {'client.counts', 'client.userBoot', 'client.rtm.start'}
                COOKIE_POST_METHODS = {'conversations.history', 'conversations.info', 'conversations.replies', 'users.list'}
                def slack_request(api_method, params):
                    import http.client as _http
                    is_client      = api_method in CLIENT_METHODS
                    is_cookie_post = api_method in COOKIE_POST_METHODS
                    token     = SLACK_CLIENT_TOKEN
                    if is_cookie_post:
                        post_data = urllib.parse.urlencode({'token': SLACK_CLIENT_TOKEN, **params}).encode('utf-8')
                        headers = {
                            'Content-Type':   'application/x-www-form-urlencoded',
                            'Content-Length': str(len(post_data)),
                            'Cookie':         SLACK_CLIENT_COOKIE,
                            'Origin':         'https://app.slack.com',
                            'Referer':        'https://app.slack.com/',
                            'User-Agent':     'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                        }
                        conn = _http.HTTPSConnection(SLACK_WORKSPACE_DOMAIN, context=make_ssl_context(), timeout=20)
                        conn.request('POST', f'/api/{api_method}', body=post_data, headers=headers)
                        resp = conn.getresponse()
                        return json.loads(resp.read())
                    elif is_client:
                        client_defaults = {
                            'thread_counts_by_channel': 'true',
                            'org_wide_aware':            'true',
                            'include_file_channels':     'true',
                            'include_all_unreads':       'true',
                            'dry_run_last_fetched':      str(int(time.time())),
                            '_x_reason':                 'fetchClientCountsOnConnect',
                            '_x_mode':                   'online',
                            '_x_sonic':                  'true',
                            '_x_app_name':               'client',
                        } if api_method == 'client.counts' else {}
                        fields   = {'token': token, **client_defaults, **params}
                        boundary = '----WebKitFormBoundary' + uuid.uuid4().hex[:16]
                        lines    = []
                        for k, v in fields.items():
                            lines += [f'--{boundary}', f'Content-Disposition: form-data; name="{k}"', '', str(v)]
                        lines += [f'--{boundary}--', '']
                        post_data = '\r\n'.join(lines).encode('utf-8')

                        url_qs = urllib.parse.urlencode({
                            '_x_id':                  f'{uuid.uuid4().hex[:8]}-{int(time.time()*1000)}',
                            '_x_csid':                SLACK_CLIENT_CSID,
                            'slack_route':            SLACK_WORKSPACE_ID,
                            '_x_version_ts':          SLACK_VERSION_TS,
                            '_x_frontend_build_type': 'current',
                            '_x_desktop_ia':          '4',
                            '_x_gantry':              'true',
                            'fp':                     '98',
                            '_x_num_retries':         '0',
                        })
                        headers = {
                            'Content-Type':     f'multipart/form-data; boundary={boundary}',
                            'Content-Length':   str(len(post_data)),
                            'Cookie':           SLACK_CLIENT_COOKIE,
                            'Origin':           'https://app.slack.com',
                            'Referer':          'https://app.slack.com/',
                            'User-Agent':       'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0',
                            'sec-fetch-site':   'same-site',
                            'sec-fetch-mode':   'cors',
                            'sec-fetch-dest':   'empty',
                            'accept':           '*/*',
                            'accept-language':  'en-US,en;q=0.9',
                        }
                        conn = _http.HTTPSConnection(SLACK_WORKSPACE_DOMAIN, context=make_ssl_context(), timeout=20)
                        conn.request('POST', f'/api/{api_method}?{url_qs}', body=post_data, headers=headers)
                        resp = conn.getresponse()
                        return json.loads(resp.read())
                    else:
                        qs  = urllib.parse.urlencode(params)
                        url = f'https://slack.com/api/{api_method}?{qs}'
                        req = urllib.request.Request(url, headers={
                            'Authorization': f'Bearer {token}',
                            'Cache-Control': 'no-cache',
                        })
                        with opener.open(req, timeout=20) as r:
                            return json.loads(r.read())

                needs_filter = any(p is not None for p in [filter_deleted_raw, filter_minutes_raw, convert_times_raw, debug_raw])
                if method == 'conversations.list' and needs_filter:
                    all_channels = []
                    page_params  = dict(fwd_params)
                    first_data   = None
                    page_ok      = True
                    while True:
                        page = slack_request(method, page_params)
                        if not page.get('ok'):
                            body   = json.dumps(page).encode()
                            page_ok = False
                            break
                        if first_data is None:
                            first_data = page
                        all_channels.extend(page.get('channels', []))
                        cursor = (page.get('response_metadata') or {}).get('next_cursor', '')
                        if not cursor:
                            break
                        page_params = dict(fwd_params)
                        page_params['cursor'] = cursor
                    if page_ok and debug_raw is not None and debug_raw.lower() == 'true':
                        first_data['channels'] = all_channels
                        first_data.pop('response_metadata', None)
                        body = json.dumps(first_data).encode()
                    elif page_ok:
                        def norm_ts(v): return float(v) / 1000 if float(v) > 1e10 else float(v)
                        channels = all_channels
                        if filter_deleted_raw is not None:
                            want_deleted = filter_deleted_raw.lower() == 'true'
                            channels = [ch for ch in channels if ch.get('is_user_deleted', False) == want_deleted]
                        if filter_minutes_raw is not None:
                            cutoff   = time.time() - int(filter_minutes_raw) * 60
                            channels = [ch for ch in channels if norm_ts(ch.get('updated', 0)) >= cutoff]
                        if convert_times_raw is not None and convert_times_raw.lower() == 'true':
                            for ch in channels:
                                for field in ('updated', 'last_read', 'created'):
                                    val = ch.get(field)
                                    if val:
                                        try:
                                            ts = float(val)
                                            if ts > 1e10:
                                                ts /= 1000
                                            ch[field + '_human'] = datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')
                                        except Exception:
                                            pass
                        # build user_id -> display name lookup
                        if any(ch.get('user') for ch in channels):
                            user_map  = {}
                            u_params  = {'limit': '200'}
                            while True:
                                upage = slack_request('users.list', u_params)
                                if not upage.get('ok'):
                                    break
                                for u in upage.get('members', []):
                                    profile = u.get('profile', {})
                                    name = profile.get('display_name') or profile.get('real_name') or u.get('name', '')
                                    user_map[u['id']] = name
                                cursor = (upage.get('response_metadata') or {}).get('next_cursor', '')
                                if not cursor:
                                    break
                                u_params = {'limit': '200', 'cursor': cursor}
                            for ch in channels:
                                uid = ch.get('user')
                                if uid and uid in user_map:
                                    ch['user_human'] = user_map[uid]
                        first_data['channels'] = channels
                        first_data.pop('response_metadata', None)
                        body = json.dumps(first_data).encode()
                else:
                    data = slack_request(method, fwd_params)
                    body = json.dumps(data).encode()
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            except BrokenPipeError:
                pass
            except Exception as e:
                print(f'Slack proxy error [{method}]: {e}')
                try:
                    self.send_response(502)
                    self._cors()
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode())
                except BrokenPipeError:
                    pass
            return

        # ── Feeds list ────────────────────────────────────────────────────────
        if parsed.path == '/feeds':
            feeds_file = os.path.join(BASE_DIR, 'feeds.json')
            try:
                with open(feeds_file, 'r') as f:
                    data = json.load(f)
                feeds = data.get('feeds', data) if isinstance(data, dict) else data
                body = json.dumps(feeds).encode()
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'[]')
            return

        # ── Locations list ───────────────────────────────────────────────────
        if parsed.path == '/locations':
            feeds_file = os.path.join(BASE_DIR, 'feeds.json')
            try:
                with open(feeds_file, 'r') as f:
                    data = json.load(f)
                body = json.dumps(data.get('locations', [])).encode()
            except FileNotFoundError:
                body = b'[]'
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(body)
            return

        # ── RSS proxy ─────────────────────────────────────────────────────────
        if parsed.path == '/rss':
            params  = urllib.parse.parse_qs(parsed.query)
            feed_url = params.get('url', [None])[0]
            if not feed_url:
                self.send_response(400); self.end_headers(); return
            try:
                now = time.time()
                with _rss_cache_lock:
                    cached = _rss_cache.get(feed_url)
                if cached and now - cached[0] < RSS_CACHE_TTL:
                    body = cached[1]
                else:
                    ssl_ctx       = make_ssl_context()
                    https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
                    proxy_url     = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
                    if proxy_url:
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy_url, 'http': proxy_url}), https_handler)
                    else:
                        opener = urllib.request.build_opener(https_handler)
                    req      = urllib.request.Request(feed_url, headers={
                        'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                        'Accept':          'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                        'Accept-Language': 'en-US,en;q=0.9',
                        'Accept-Encoding': 'gzip, deflate',
                        'Cache-Control':   'no-cache',
                    })
                    with opener.open(req, timeout=15) as r:
                        raw_bytes = r.read()
                        encoding = r.headers.get('Content-Encoding', '')
                    if encoding == 'gzip':
                        xml_bytes = gzip.decompress(raw_bytes)
                    elif encoding == 'deflate':
                        import zlib
                        xml_bytes = zlib.decompress(raw_bytes)
                    else:
                        xml_bytes = raw_bytes
                    root  = ET.fromstring(xml_bytes)
                    ns    = {'atom': 'http://www.w3.org/2005/Atom'}
                    items = []
                    def _strip_html(s):
                        return re.sub(r'<[^>]+>', '', s or '').strip()

                    # RSS 2.0
                    for item in root.findall('./channel/item'):
                        title = item.findtext('title') or ''
                        link  = item.findtext('link') or ''
                        date  = item.findtext('pubDate') or ''
                        desc  = _strip_html(item.findtext('description') or '')
                        items.append({'title': title.strip(), 'link': link.strip(), 'date': date.strip(), 'description': desc})
                    # Atom
                    if not items:
                        for entry in root.findall('atom:entry', ns) or root.findall('{http://www.w3.org/2005/Atom}entry'):
                            title = entry.findtext('atom:title', namespaces=ns) or entry.findtext('{http://www.w3.org/2005/Atom}title') or ''
                            link_el = entry.find('atom:link', ns) or entry.find('{http://www.w3.org/2005/Atom}link')
                            link = (link_el.get('href') if link_el is not None else '') or ''
                            date = (entry.findtext('atom:published', namespaces=ns)
                                    or entry.findtext('{http://www.w3.org/2005/Atom}published')
                                    or entry.findtext('atom:updated', namespaces=ns)
                                    or entry.findtext('{http://www.w3.org/2005/Atom}updated') or '')
                            desc = _strip_html(
                                entry.findtext('atom:summary', namespaces=ns)
                                or entry.findtext('{http://www.w3.org/2005/Atom}summary')
                                or entry.findtext('atom:content', namespaces=ns)
                                or entry.findtext('{http://www.w3.org/2005/Atom}content') or '')
                            items.append({'title': title.strip(), 'link': link.strip(), 'date': date.strip(), 'description': desc})
                    body = json.dumps({'status': 'ok', 'items': items[:15]}).encode()
                    with _rss_cache_lock:
                        _rss_cache[feed_url] = (now, body)
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                print(f'RSS proxy error ({feed_url}): {e}')
                self.send_response(502)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'status': 'error', 'message': str(e)}).encode())
            return

        # ── Stocks proxy ──────────────────────────────────────────────────────
        if parsed.path == '/stocks':
            try:
                feeds_file = os.path.join(BASE_DIR, 'feeds.json')
                try:
                    with open(feeds_file, 'r') as f:
                        feeds_data = json.load(f)
                    symbols = feeds_data.get('stocks', []) if isinstance(feeds_data, dict) else []
                except Exception:
                    symbols = []
                out = finnhub_quotes(symbols)
                body = json.dumps(out).encode()
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                print(f'Stocks proxy error: {e}')
                self.send_response(502)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        # ── PagerDuty proxy ───────────────────────────────────────────────────
        if parsed.path == '/pagerduty':
            try:
                ssl_ctx       = make_ssl_context()
                https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
                proxy_url     = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
                if proxy_url:
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy_url, 'http': proxy_url}), https_handler)
                else:
                    opener = urllib.request.build_opener(https_handler)

                teams = PAGERDUTY_TEAMS
                headers = {'Authorization': f'Token token={PAGERDUTY_TOKEN}', 'Accept': 'application/vnd.pagerduty+json;version=2'}
                results = []

                for team in teams:
                    try:
                        # Fetch on-call using schedule_ids
                        oncall_url = f'https://api.pagerduty.com/oncalls?schedule_ids[]={team["schedule"]}&include[]=users'
                        req = urllib.request.Request(oncall_url, headers=headers)
                        try:
                            with opener.open(req, timeout=15) as r:
                                oncall_data = json.loads(r.read())
                        except urllib.error.HTTPError as he:
                            body = he.read().decode('utf-8')
                            print(f'PagerDuty oncalls {team["name"]} 400: {body}')
                            raise Exception(f'oncalls: {body}')

                        oncall_person = 'No one'
                        if oncall_data.get('oncalls') and len(oncall_data['oncalls']) > 0:
                            user = oncall_data['oncalls'][0].get('user', {})
                            oncall_person = user.get('summary', 'Unknown')

                        # Fetch incident counts by service
                        incident_url = f'https://api.pagerduty.com/incidents?service_ids[]={team["service"]}&statuses[]=triggered&statuses[]=acknowledged&statuses[]=resolved&limit=100'
                        req = urllib.request.Request(incident_url, headers=headers)
                        try:
                            with opener.open(req, timeout=15) as r:
                                incident_data = json.loads(r.read())
                        except urllib.error.HTTPError as he:
                            body = he.read().decode('utf-8')
                            print(f'PagerDuty incidents {team["name"]} 400: {body}')
                            raise Exception(f'incidents: {body}')

                        incidents = incident_data.get('incidents', [])
                        triggered_count = len([i for i in incidents if i.get('status') == 'triggered'])
                        acknowledged_count = len([i for i in incidents if i.get('status') == 'acknowledged'])
                        resolved_count = len([i for i in incidents if i.get('status') == 'resolved'])

                        results.append({
                            'team': team['name'],
                            'person': oncall_person,
                            'triggered': triggered_count,
                            'acknowledged': acknowledged_count,
                            'resolved': resolved_count,
                            'incidents': [
                                {
                                    'id':         inc.get('id', ''),
                                    'title':      inc.get('title', ''),
                                    'status':     inc.get('status', ''),
                                    'urgency':    inc.get('urgency', ''),
                                    'html_url':   inc.get('html_url', ''),
                                    'created_at': inc.get('created_at', ''),
                                }
                                for inc in incidents
                                if inc.get('status') in ('triggered', 'acknowledged')
                            ],
                        })
                    except Exception as e:
                        print(f'PagerDuty error for {team["name"]}: {e}')
                        results.append({'team': team['name'], 'person': 'Error', 'triggered': 0, 'acknowledged': 0, 'resolved': 0, 'incidents': []})

                body = json.dumps(results).encode()
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                print(f'PagerDuty proxy error: {e}')
                self.send_response(502)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'error': str(e)}).encode())
            return

        # ── Serve HTML ────────────────────────────────────────────────────────
        if parsed.path in ('/', '/office-calendar-countdown.html', '/calendar.html'):
            try:
                with open(HTML_FILE, 'rb') as f:
                    body = f.read()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except FileNotFoundError:
                self.send_error(404, f'HTML file not found: {HTML_FILE}')
            return

        # ── Static files (Images/, etc.) ──────────────────────────────────────
        safe_path = parsed.path.lstrip('/')
        # Check BASE_DIR first (user-editable files), then BUNDLE_DIR (bundled assets)
        static_file = None
        for _search in [BASE_DIR, BUNDLE_DIR]:
            _candidate = os.path.normpath(os.path.join(_search, safe_path))
            if _candidate.startswith(_search) and os.path.isfile(_candidate):
                static_file = _candidate
                break
        if static_file:
            ext = os.path.splitext(static_file)[1].lower()
            mime = {
                '.ico': 'image/x-icon', '.png': 'image/png',
                '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                '.svg': 'image/svg+xml', '.css': 'text/css',
                '.js': 'application/javascript',
            }.get(ext, 'application/octet-stream')
            with open(static_file, 'rb') as f:
                body = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        self.send_error(404)

    def do_POST(self):
        global SLACK_CLIENT_TOKEN, SLACK_CLIENT_COOKIE, SLACK_VERSION_TS, SLACK_CLIENT_CSID
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == '/slack/login':
            params = urllib.parse.parse_qs(parsed.query)
            if params.get('_k', [None])[0] != PROXY_SECRET:
                self.send_response(403)
                self._cors()
                self.end_headers()
                return
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length) or b'{}')
            email    = body.get('email', '').strip()
            password = body.get('password', '').strip()
            try:
                token, cookie_str, version_ts, csid = run_slack_playwright_login(email, password)
                if token:
                    SLACK_CLIENT_TOKEN = token
                if cookie_str:
                    SLACK_CLIENT_COOKIE = cookie_str
                if version_ts:
                    SLACK_VERSION_TS = version_ts
                if csid:
                    SLACK_CLIENT_CSID = csid
                self.send_response(200)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    'ok':          True,
                    'token':       token,
                    'cookie':      cookie_str,
                    'version_ts':  version_ts,
                    'csid':        csid,
                }).encode())
            except Exception as e:
                print(f'slack login error: {e}')
                self.send_response(500)
                self._cors()
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({'ok': False, 'error': str(e)}).encode())
            return

        if parsed.path == '/slack/restore':
            params = urllib.parse.parse_qs(parsed.query)
            if params.get('_k', [None])[0] != PROXY_SECRET:
                self.send_response(403)
                self._cors()
                self.end_headers()
                return
            length = int(self.headers.get('Content-Length', 0))
            body   = json.loads(self.rfile.read(length) or b'{}')
            SLACK_CLIENT_TOKEN  = body.get('token')      or None
            SLACK_CLIENT_COOKIE = body.get('cookie')     or None
            SLACK_VERSION_TS    = body.get('version_ts') or None
            SLACK_CLIENT_CSID   = body.get('csid')       or None
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'ok': bool(SLACK_CLIENT_TOKEN)}).encode())
            return

        self.send_error(404)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin',  '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Cache-Control')


_last_sha       = None
_last_feeds_sha = None

def _github_opener():
    ssl_ctx      = make_ssl_context()
    https_handler = urllib.request.HTTPSHandler(context=ssl_ctx)
    proxy_url    = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if proxy_url:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({'https': proxy_url, 'http': proxy_url}), https_handler)
    else:
        opener = urllib.request.build_opener(https_handler)
    return opener

def pull_calendar_html(content=None):
    try:
        if content is None:
            opener = _github_opener()
            req = urllib.request.Request(
                GITHUB_API_URL,
                headers={
                    'Authorization': f'token {GITHUB_PAT}',
                    'Accept': 'application/vnd.github.raw',
                }
            )
            with opener.open(req, timeout=20) as r:
                content = r.read()
        ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
        content = re.sub(rb'<meta name="version"[^>]*>', b'', content)
        content = content.replace(b'</head>', f'<meta name="version" content="{ts}"></head>'.encode())
        with open(HTML_FILE, 'wb') as f:
            f.write(content)
        print(f'calendar.html updated → {HTML_FILE}')
    except Exception as e:
        print(f'pull_calendar_html error: {e}')

def check_and_pull():
    global _last_sha, _last_feeds_sha
    opener = _github_opener()
    auth   = {'Authorization': f'token {GITHUB_PAT}'}
    try:
        req  = urllib.request.Request(GITHUB_API_URL, headers=auth)
        with opener.open(req, timeout=20) as r:
            meta = json.loads(r.read())
        sha = meta.get('sha')
        if sha and sha != _last_sha:
            _last_sha = sha
            encoded = (meta.get('content') or '').replace('\n', '')
            content  = base64.b64decode(encoded) if encoded else None
            pull_calendar_html(content)
    except Exception as e:
        print(f'check_and_pull (calendar.html) error: {e}')
    try:
        req  = urllib.request.Request(GITHUB_FEEDS_URL, headers=auth)
        with opener.open(req, timeout=20) as r:
            meta = json.loads(r.read())
        sha = meta.get('sha')
        if sha and sha != _last_feeds_sha:
            _last_feeds_sha = sha
            encoded = (meta.get('content') or '').replace('\n', '')
            content  = base64.b64decode(encoded) if encoded else None
            if content:
                feeds_file = os.path.join(BASE_DIR, 'feeds.json')
                with open(feeds_file, 'wb') as f:
                    f.write(content)
                print(f'feeds.json updated → {feeds_file}')
    except Exception as e:
        print(f'check_and_pull (feeds.json) error: {e}')

def pull_loop():
    while True:
        time.sleep(PULL_INTERVAL)
        check_and_pull()

class _ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        if issubclass(sys.exc_info()[0], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


if __name__ == '__main__':
    if AUTO_PULL:
        pull_calendar_html()
        check_and_pull()   # also pulls feeds.json on startup
        t = threading.Thread(target=pull_loop, daemon=True)
        t.start()
    else:
        print('Auto-pull disabled — serving local files.')
    os.chdir(BASE_DIR)
    port = PORT
    while True:
        try:
            server = _ThreadedHTTPServer(('', port), Handler)
            break
        except OSError:
            print(f'Port {port} unavailable, trying {port + 1}...')
            port += 1
            if port > PORT + 20:
                print('ERROR: Could not find an available port.')
                sys.exit(1)
    _feeds_path = os.path.join(BASE_DIR, 'feeds.json')
    if not os.path.exists(_feeds_path):
        print('ERROR: feeds.json not found.')
        print(f'  Expected at: {_feeds_path}')
        print(f'  Copy feeds.example.json to feeds.json and customize it.')
        sys.exit(1)
    print(f'Serving at http://localhost:{port}')
    print(f'HTML:   {HTML_FILE}')
    print(f'Feeds:  {_feeds_path}')
    proxy_url = os.environ.get('HTTPS_PROXY') or os.environ.get('https_proxy')
    if proxy_url:
        print(f'Proxy: {proxy_url}')
    else:
        print('Proxy: none — using ' + ('Windows cert store' if os.name == 'nt' else 'system CA bundle') + ' for SSL')
    print('Press Ctrl+C to stop.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')