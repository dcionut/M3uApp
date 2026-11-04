"""Local M3U library manager. Run with python app.py."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qsl
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
        elif line.startswith('#EXTVLCOPT:') and pending:
            key, separator, value = line[11:].partition('=')
            if separator and key in ('http-user-agent', 'http-referrer'):
                pending['metadata'][key] = value
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
    partial = None
    try:
        folder = DATA / 'downloads'
        folder.mkdir(parents=True, exist_ok=True)
        # Some IPTV providers append VLC-style HTTP headers to media URLs.
        url, _, options = entry['url'].partition('|')
        headers = {'User-Agent': 'Mozilla/5.0 (compatible; M3uApp/1.0)'}
        metadata = entry.get('metadata', {})
        for key, header in (('http-user-agent', 'User-Agent'), ('http-referrer', 'Referer')):
            if metadata.get(key):
                headers[header] = metadata[key]
        for key, value in parse_qsl(options):
            header = {'user-agent': 'User-Agent', 'referer': 'Referer', 'referrer': 'Referer'}.get(key.lower())
            if header:
                headers[header] = value
        if urlsplit(url).scheme not in ('http', 'https'):
            raise ValueError('Downloads require an HTTP or HTTPS URL.')
        suffix = Path(urlsplit(url).path).suffix.lower()
        if suffix in ('.m3u', '.m3u8', '.mpd'):
            raise ValueError('This is an HLS/DASH stream. Download requires a direct movie file URL (.mp4, .mkv, etc.).')
        suffix = suffix if re.fullmatch(r'\.[a-z0-9]{1,8}', suffix) else '.bin'
        name = re.sub(r'[^\w .-]', '_', entry['title']).strip('. ')[:80] or 'movie'
        # A fixed prefix also avoids Windows reserved filenames such as CON.
        target = folder / f'movie-{name}-{job_id[:8]}{suffix}'
        partial = target.with_suffix(target.suffix + '.part')
        with urlopen(Request(url, headers=headers), timeout=60) as response:
            if urlsplit(response.url).scheme not in ('http', 'https'):
                raise ValueError('Unsupported redirect.')
            content_type = response.headers.get('Content-Type', '').lower()
            if any(t in content_type for t in ('mpegurl', 'dash+xml', 'text/html')):
                raise ValueError('The provider returned a stream playlist or login page. Use a direct movie file URL.')
            length = response.headers.get('Content-Length')
            expected = int(length) if length and length.isdecimal() else None
            received = 0
            with LOCK:
                JOBS[job_id]['total'] = expected
            with partial.open('xb') as output:
                while chunk := response.read(256 * 1024):
                    # Detect manifests even when providers send a generic content type.
                    if received == 0 and chunk.lstrip().startswith((b'#EXTM3U', b'<MPD', b'<!DOCTYPE html', b'<html')):
                        raise ValueError('The URL returned a stream playlist or web page instead of a movie file.')
                    output.write(chunk)
                    received += len(chunk)
                    with LOCK:
                        JOBS[job_id]['bytes'] = received
            if not received or (expected is not None and received != expected):
                raise ValueError('The connection ended before the complete movie arrived. Please retry the download.')
        partial.rename(target)
        with LOCK:
            JOBS[job_id].update(status='complete', file=target.name)
    except Exception as exc:
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(exc, HTTPError):
            error = {
                401: 'The provider requires authentication. Refresh your playlist or access link.',
                403: 'The provider refused access (403). The link may have expired or require a Referer/User-Agent.',
                404: 'Movie not found (404). Refresh your playlist or correct the media URL.',
                429: 'The provider is limiting requests (429). Wait before retrying.',
            }.get(exc.code, f'The provider returned HTTP {exc.code}. Please retry later.')
        elif isinstance(exc, URLError):
            error = 'Could not connect securely to the provider. Check your internet connection, the media URL, and your system certificates.'
        elif isinstance(exc, PermissionError):
            error = 'Cannot write to data/downloads. Move the app to a writable folder and restart it.'
        elif isinstance(exc, OSError):
            error = 'Network or disk error. Check your connection and available disk space, then retry.'
        else:
            error = str(exc)
        with LOCK:
            JOBS[job_id].update(status='failed', error=error)


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
        elif path in ('/player.js', '/vendor/hls.min.js'):
            self.send((Path(__file__).parent / path.lstrip('/')).read_text(encoding='utf-8'), content_type='application/javascript')
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
            elif self.path == '/api/clear':
                if body.get('confirm') is not True:
                    raise ValueError('Confirm clearing the library first.')
                with db() as con:
                    con.execute('DELETE FROM entries')
                self.send({'cleared': True})
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
