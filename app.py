"""Local M3U library manager. Run with python app.py."""
import argparse
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

DATA = Path(__file__).parent / 'data'
LOCK = threading.Lock()
JOBS = {}


def db():
    DATA.mkdir(exist_ok=True)
    con = sqlite3.connect(DATA / 'library.sqlite3')
    con.row_factory = sqlite3.Row
    con.execute('CREATE TABLE IF NOT EXISTS entries (id INTEGER PRIMARY KEY, title TEXT, url TEXT, "group" TEXT, metadata TEXT)')
    return con


def parse_m3u(text):
    entries, pending = [], None
    for line in text.lstrip('\ufeff').splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('#EXTINF:'):
            match = re.match(r'#EXTINF:((?:[^",]|"[^"]*")*),(.*)$', line)
            if not match:
                pending = None
                continue
            attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', match[1]))
            pending = {'title': match[2].strip(), 'group': attrs.pop('group-title', ''), 'metadata': attrs}
        elif line.startswith('#EXTGRP:') and pending:
            pending['group'] = line[8:]
        elif not line.startswith('#'):
            entry = pending or {'title': line.rsplit('/', 1)[-1], 'group': '', 'metadata': {}}
            entries.append(dict(entry, url=line))
            pending = None
    return entries


def export_m3u(entries):
    def clean(value):
        return str(value).replace('"', "'").replace('\r', ' ').replace('\n', ' ')
    lines = ['#EXTM3U']
    for entry in entries:
        attrs = dict(entry['metadata'], **{'group-title': entry['group']})
        attributes = ' '.join(f'{key}="{clean(value)}"' for key, value in attrs.items() if re.fullmatch(r'[\w-]+', key))
        lines.extend([f'#EXTINF:-1 {attributes},{clean(entry["title"])}', clean(entry['url'])])
    return '\n'.join(lines) + '\n'


def entries():
    with db() as con:
        return [dict(row, metadata=json.loads(row['metadata'])) for row in con.execute('SELECT * FROM entries ORDER BY id')]


def validate_entry(entry):
    if not isinstance(entry, dict) or not all(isinstance(entry.get(k), str) for k in ('title', 'url', 'group')):
        raise ValueError('Title, URL and group must be text.')
    if not entry['title'].strip() or not entry['url'].strip() or any(c in entry['url'] for c in '\r\n'):
        raise ValueError('Provide a title and a single-line URL.')
    if not isinstance(entry.get('metadata'), dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in entry['metadata'].items()):
        raise ValueError('Metadata must be a JSON object containing text values.')


def download(job_id, entry):
    folder = DATA / 'downloads'
    folder.mkdir(exist_ok=True)
    name = re.sub(r'[^\w .-]', '_', entry['title']).strip('. ')[:100] or 'movie'
    suffix = Path(urlsplit(entry['url']).path).suffix.lower()
    suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,8}', suffix) else '.bin'
    target = folder / f'{name}-{job_id[:8]}{suffix}'
    partial = target.with_suffix(target.suffix + '.part')
    try:
        if urlsplit(entry['url']).scheme not in ('http', 'https'):
            raise ValueError('Downloads require an HTTP or HTTPS URL.')
        if suffix in ('.m3u', '.m3u8', '.mpd'):
            raise ValueError('Streaming manifests are not movie files. Use a direct movie URL.')
        with urlopen(Request(entry['url'], headers={'User-Agent': 'M3uApp/1.0'}), timeout=30) as response:
            if urlsplit(response.url).scheme not in ('http', 'https'):
                raise ValueError('Unsupported redirect.')
            if any(t in response.headers.get('Content-Type', '').lower() for t in ('mpegurl', 'dash+xml', 'text/html')):
                raise ValueError('The URL returned a playlist or web page, not a movie file.')
            with partial.open('xb') as output:
                while chunk := response.read(256 * 1024):
                    output.write(chunk)
                    with LOCK:
                        JOBS[job_id]['bytes'] += len(chunk)
        partial.rename(target)
        with LOCK:
            JOBS[job_id].update(status='complete', file=target.name)
    except Exception as exc:
        partial.unlink(missing_ok=True)
        with LOCK:
            JOBS[job_id].update(status='failed', error=str(exc))


class Handler(BaseHTTPRequestHandler):
    def send(self, payload, status=200, content_type='application/json'):
        raw = json.dumps(payload).encode() if content_type == 'application/json' else payload.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        if content_type.startswith('audio/'):
            self.send_header('Content-Disposition', 'attachment; filename="library.m3u"')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/':
            self.send(Path(__file__).with_name('index.html').read_text(), content_type='text/html')
        elif path == '/api/entries':
            self.send(entries())
        elif path == '/api/jobs':
            with LOCK:
                self.send(dict(JOBS))
        elif path == '/export':
            self.send(export_m3u(entries()), content_type='audio/x-mpegurl')
        else:
            self.send({'error': 'Not found'}, 404)

    def do_POST(self):
        # Local-only UI: reject cross-site writes and DNS-rebinding hosts.
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        if host.split(':')[0] not in ('127.0.0.1', 'localhost') or (origin and origin != 'http://' + host):
            return self.send({'error': 'Local requests only'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 20 * 1024 * 1024:
                raise ValueError('Request must be between 1 byte and 20 MB.')
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/import':
                imported = parse_m3u(body['text'])
                if not imported:
                    raise ValueError('No playlist entries found.')
                with db() as con:
                    for entry in imported:
                        validate_entry(entry)
                        con.execute('INSERT INTO entries(title,url,"group",metadata) VALUES(?,?,?,?)', (entry['title'], entry['url'], entry['group'], json.dumps(entry['metadata'])))
                self.send({'imported': len(imported)})
            elif self.path == '/api/save':
                validate_entry(body)
                with db() as con:
                    result = con.execute('UPDATE entries SET title=?,url=?,"group"=?,metadata=? WHERE id=?', (body['title'], body['url'], body['group'], json.dumps(body['metadata']), body['id']))
                    if not result.rowcount:
                        raise ValueError('Entry not found.')
                self.send({'saved': True})
            elif self.path == '/api/delete':
                with db() as con:
                    con.execute('DELETE FROM entries WHERE id=?', (body['id'],))
                self.send({'deleted': True})
            elif self.path == '/api/download':
                entry = next((e for e in entries() if e['id'] == body['id']), None)
                if not entry:
                    raise ValueError('Entry not found.')
                with LOCK:
                    if sum(j['status'] == 'downloading' for j in JOBS.values()) >= 3:
                        raise ValueError('Three downloads are already running. Wait for one to finish.')
                    job = uuid.uuid4().hex
                    JOBS[job] = {'title': entry['title'], 'status': 'downloading', 'bytes': 0}
                threading.Thread(target=download, args=(job, entry), daemon=True).start()
                self.send({'job': job}, 202)
            else:
                self.send({'error': 'Not found'}, 404)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    with db():
        pass
    print(f'M3uApp listening on http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
