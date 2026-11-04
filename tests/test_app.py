import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
import app


class WorkflowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = app.DATA
        app.DATA = Path(self.tmp.name)
        app.JOBS.clear()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        app.DATA = self.original
        self.tmp.cleanup()

    def post(self, route, data):
        with urlopen(Request(self.base + route, data=json.dumps(data).encode(), headers={'Content-Type': 'application/json'})) as response:
            return json.load(response)

    def test_library_workflow(self):
        playlist = '\ufeff#EXTM3U\n#EXTINF:-1 tvg-id="42" group-title="Movies, HD",Example Movie\nhttps://example.com/movie.mp4\n'
        self.assertEqual(self.post('/api/import', {'text': playlist})['imported'], 1)
        with urlopen(self.base + '/api/entries') as response:
            entry = json.load(response)[0]
        self.assertEqual(entry['group'], 'Movies, HD')
        entry['group'] = 'Favorites'
        entry['metadata']['year'] = '2026'
        self.post('/api/save', entry)
        with urlopen(self.base + '/export') as response:
            exported = app.parse_m3u(response.read().decode())
        self.assertEqual(exported[0]['metadata'], {'tvg-id': '42', 'year': '2026'})
        self.assertEqual(exported[0]['group'], 'Favorites')
        self.post('/api/delete', {'id': entry['id']})
        self.assertEqual(app.entries(), [])

    def test_download(self):
        payload = b'movie bytes' * 100
        class Media(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'video/mp4')
                self.end_headers()
                self.wfile.write(payload)
        media = ThreadingHTTPServer(('127.0.0.1', 0), Media)
        threading.Thread(target=media.serve_forever, daemon=True).start()
        try:
            self.post('/api/import', {'text': f'#EXTM3U\n#EXTINF:-1,Movie\nhttp://127.0.0.1:{media.server_port}/movie.mp4\n'})
            job = self.post('/api/download', {'id': app.entries()[0]['id']})['job']
            for _ in range(100):
                if app.JOBS[job]['status'] != 'downloading':
                    break
                time.sleep(.02)
            self.assertEqual(app.JOBS[job]['status'], 'complete', app.JOBS[job])
            self.assertEqual((app.DATA / 'downloads' / app.JOBS[job]['file']).read_bytes(), payload)
        finally:
            media.shutdown()
            media.server_close()

    def test_reject_cross_origin(self):
        from urllib.error import HTTPError
        request = Request(self.base + '/api/import', data=b'{}', headers={'Origin': 'https://other.example'})
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 403)

    def test_stream_download_failure(self):
        app.JOBS['test'] = {'status': 'downloading', 'bytes': 0}
        app.download('test', {'title': 'Stream', 'url': 'https://example.com/movie.m3u8'})
        self.assertEqual(app.JOBS['test']['status'], 'failed')
        self.assertEqual(list((app.DATA / 'downloads').iterdir()), [])


if __name__ == '__main__':
    unittest.main()
