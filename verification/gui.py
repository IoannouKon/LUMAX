#!/usr/bin/env python3
"""LUMAX local verification dashboard. One command: python3 gui.py start.

No Python packages or frontend build are required. The URL printed at startup
contains the private access key. Use --host 0.0.0.0 for access over your LAN,
or keep localhost and use SSH port forwarding.
"""
import argparse
import hmac
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
import gui_core as core
import cycle_compare


def access_token():
    core.RUNTIME.mkdir(mode=0o700, exist_ok=True)
    path = core.RUNTIME / 'token'
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f: f.write(secrets.token_urlsafe(32))
    return path.read_text().strip()


class Scheduler:
    def __init__(self, store):
        self.store, self.stop = store, threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop.wait(1):
            jobs = self.store.jobs_list()
            active = False
            for job in jobs:
                if job['status'] in core.FINAL or job['status'] == 'QUEUED': continue
                folder = self.store.folder(job['id'])
                if core.is_locked(folder / 'worker.lock'):
                    active = True
                else:
                    # A short grace period covers process startup before locking.
                    stamp = core.datetime.fromisoformat(job.get('heartbeat') or job['created']).timestamp()
                    if time.time() - stamp < 10:
                        active = True
                    else:
                        job.update(status='INTERRUPTED', phase='Worker exited without a final result', ended=core.now())
                        core.write_json(folder / 'job.json', job)
            if active: continue
            pending = [j for j in reversed(jobs) if j['status'] == 'QUEUED']
            if not pending: continue
            job = pending[0]
            folder = self.store.folder(job['id'])
            if (folder / 'STOP').exists():
                self.store.cancel(job['id']); continue
            job.update(status='STARTING', phase='Starting worker', heartbeat=core.now())
            core.write_json(folder / 'job.json', job)
            with (folder / 'worker.log').open('a') as output:
                try:
                    subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker', '--job', job['id']],
                        cwd=core.ROOT, stdin=subprocess.DEVNULL, stdout=output,
                        stderr=subprocess.STDOUT, start_new_session=True)
                except OSError as error:
                    job.update(status='ERROR', phase=str(error), ended=core.now())
                    core.write_json(folder / 'job.json', job)


class Handler(BaseHTTPRequestHandler):
    server_version = 'LUMAX/1.0'

    def log_message(self, fmt, *args):
        # No access-key URLs or test data in the HTTP request log.
        pass

    def authorized(self):
        token = self.headers.get('X-Lumax-Token', '')
        if not token:
            try:
                jar = cookies.SimpleCookie(self.headers.get('Cookie', ''))
                token = jar['lumax'].value if 'lumax' in jar else ''
            except cookies.CookieError:
                pass
        query = parse_qs(urlsplit(self.path).query)
        if not token: token = query.get('token', [''])[0]
        return bool(token) and hmac.compare_digest(token, self.server.token)

    def send(self, code, data, mime='application/json; charset=utf-8', cookie=False):
        if isinstance(data, (dict, list)): data = json.dumps(data).encode()
        if isinstance(data, str): data = data.encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if cookie:
            self.send_header('Set-Cookie', 'lumax=' + self.server.token + '; HttpOnly; SameSite=Strict; Path=/')
        self.end_headers()
        self.wfile.write(data)

    def payload(self):
        size = int(self.headers.get('Content-Length', '0'))
        if not 0 < size <= 1024 * 1024: raise ValueError('Request must contain at most 1 MiB of JSON')
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict): raise ValueError('Expected a JSON object')
        return value

    def do_GET(self):
        try:
            path = urlsplit(self.path).path
            if path in ('/', '/app.js', '/app.css'):
                filename = {'/': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}[path]
                mime = {'/': 'text/html; charset=utf-8', '/app.js': 'text/javascript; charset=utf-8', '/app.css': 'text/css; charset=utf-8'}[path]
                self.send(200, (core.ROOT / 'gui' / filename).read_bytes(), mime, self.authorized())
                return
            if not self.authorized():
                self.send(401, dict(error='Open the private URL printed by gui.py status.')); return
            store = self.server.store
            if path == '/api/initial':
                self.send(200, dict(designs=store.designs(), suites=store.suites(), patterns=core.PATTERNS,
                    builtins=core.BUILTINS, defaults=core.DEFAULT_OPTIONS, chipyard=str(core.hw.CY),
                    input_bits=16, features=dict(activation_formats=['int16', 'fp16'], fp_comparison=True,
                                                row_parallelism=True, y_slices=[1, 2]),
                    external=core.external_runs()))
            elif path == '/api/runs':
                self.send(200, dict(runs=store.jobs_list(), external=core.external_runs()))
            elif path == '/api/history':
                self.send(200, core.history())
            elif path == '/api/model/sources':
                self.send(200, dict(sources=cycle_compare.sources(core.LOGS), model=cycle_compare.model_info()))
            elif path.startswith('/api/runs/'):
                identifier = path.split('/')[3]
                self.send(200, core.read_json(store.folder(identifier) / 'job.json'))
            elif path in ('/api/log', '/api/download'):
                query = parse_qs(urlsplit(self.path).query)
                name = query.get('path', [''])[0]
                root = core.LOGS.resolve()
                target = (root / name).resolve()
                if not target.is_relative_to(root) or not target.is_file():
                    raise FileNotFoundError('Log not found')
                if path == '/api/download':
                    self.send(200, target.read_bytes(), 'text/plain; charset=utf-8')
                else:
                    size = target.stat().st_size
                    with target.open('rb') as f:
                        f.seek(max(0, size - 128 * 1024))
                        text = f.read().decode(errors='replace')
                    self.send(200, dict(path=name, bytes=size, text=text, truncated=size > 128 * 1024))
            else:
                self.send(404, dict(error='Not found'))
        except (ValueError, KeyError, json.JSONDecodeError) as error:
            self.send(400, dict(error=str(error)))
        except FileNotFoundError as error:
            self.send(404, dict(error=str(error)))
        except Exception as error:
            self.send(500, dict(error=str(error)))

    def do_POST(self):
        try:
            if not self.authorized():
                self.send(401, dict(error='Access key required')); return
            origin = self.headers.get('Origin')
            if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                self.send(403, dict(error='Request origin does not match dashboard')); return
            data = self.payload()
            path = urlsplit(self.path).path
            store = self.server.store
            if path == '/api/designs': result = store.save_design(data)
            elif path == '/api/suites': result = store.save_suite(data)
            elif path == '/api/plan': result = core.make_plan(core.normalize_suite(data, store.designs()))
            elif path == '/api/runs': result = store.create_job(data)
            elif path == '/api/model/cases':
                result = dict(cases=cycle_compare.cases(core.LOGS, data.get('sources')))
            elif path == '/api/model/compare':
                result = cycle_compare.compare(core.LOGS, data)
            elif path.startswith('/api/runs/') and path.endswith('/stop'):
                result = store.cancel(path.split('/')[3])
            else:
                self.send(404, dict(error='Not found')); return
            self.send(200, result)
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            self.send(400, dict(error=str(error)))
        except FileNotFoundError as error:
            self.send(404, dict(error=str(error)))
        except Exception as error:
            self.send(500, dict(error=str(error)))


def dashboard_url(args, token):
    host = socket.gethostname() if args.host in ('0.0.0.0', '::') else args.host
    return f'http://{host}:{args.port}/?token={token}'


def serve(args):
    token = access_token()
    with (core.RUNTIME / 'server.lock').open('a+') as lock:
        core.fcntl.flock(lock, core.fcntl.LOCK_EX | core.fcntl.LOCK_NB)
        server = ThreadingHTTPServer((args.host, args.port), Handler)
        server.daemon_threads = True
        server.token, server.store = token, core.Store()
        scheduler = Scheduler(server.store)
        scheduler.thread.start()
        core.write_json(core.RUNTIME / 'server.json', dict(pid=os.getpid(), url=dashboard_url(args, token), host=args.host, port=args.port))
        (core.RUNTIME / 'STOP').unlink(missing_ok=True)
        def watch():
            while not (core.RUNTIME / 'STOP').exists(): time.sleep(0.5)
            scheduler.stop.set()
            server.shutdown()
        threading.Thread(target=watch, daemon=True).start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: (core.RUNTIME / 'STOP').touch())
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        print('LUMAX dashboard: ' + dashboard_url(args, token), flush=True)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            scheduler.stop.set()
            server.server_close()
            (core.RUNTIME / 'server.json').unlink(missing_ok=True)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', nargs='?', default='start', choices=('start', 'restart', 'serve', 'status', 'stop', 'worker'))
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--job', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535: parser.error('Port must be 1024–65535')
    if args.action == 'worker':
        if not args.job: parser.error('worker requires a job identifier')
        return core.execute_job(core.Store().folder(args.job))
    token = access_token()
    if args.action == 'serve': return serve(args)
    if args.action == 'stop':
        (core.RUNTIME / 'STOP').touch()
        print('Dashboard stop requested. Active verification jobs continue; queued jobs wait until the dashboard restarts.')
        return 0
    if args.action == 'restart':
        info = core.read_json(core.RUNTIME / 'server.json', {})
        if not any(a == '--host' or a.startswith('--host=') for a in sys.argv): args.host = info.get('host', args.host)
        if not any(a == '--port' or a.startswith('--port=') for a in sys.argv): args.port = info.get('port', args.port)
        if core.is_locked(core.RUNTIME / 'server.lock'):
            (core.RUNTIME / 'STOP').touch()
            for _ in range(150):
                if not core.is_locked(core.RUNTIME / 'server.lock'): break
                time.sleep(0.1)
            else: raise RuntimeError('Dashboard did not stop; active verification jobs were not stopped')
    if core.is_locked(core.RUNTIME / 'server.lock'):
        info = core.read_json(core.RUNTIME / 'server.json', {})
        print('Dashboard active.\n' + info.get('url', 'Starting…'))
        return 0
    if args.action == 'status':
        print('Dashboard is not running.'); return 0
    (core.RUNTIME / 'server.json').unlink(missing_ok=True)
    cmd = [sys.executable, str(Path(__file__).resolve()), 'serve', '--host', args.host, '--port', str(args.port)]
    with (core.RUNTIME / 'server.log').open('w') as output:
        process = subprocess.Popen(cmd, cwd=core.ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(100):
        if core.is_locked(core.RUNTIME / 'server.lock') and (core.RUNTIME / 'server.json').exists():
            print('Dashboard started in background. You may close SSH.\n' + dashboard_url(args, token))
            return 0
        if process.poll() is not None:
            raise RuntimeError((core.RUNTIME / 'server.log').read_text()[-2000:])
        time.sleep(0.1)
    raise RuntimeError('Dashboard startup timed out; inspect verification/.gui/server.log')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f'ERROR: {error}', file=sys.stderr)
        raise SystemExit(1)
