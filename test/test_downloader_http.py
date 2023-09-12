#!/usr/bin/env python3

# Allow direct execution
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


import http.server
import re
import threading

from test.helper import http_server_port, try_rm
from yt_dlp import YoutubeDL
from yt_dlp.downloader.http import HttpFD
from yt_dlp.utils import encodeFilename
from yt_dlp.utils._utils import _YDLLogger as FakeLogger

TEST_DIR = os.path.dirname(os.path.abspath(__file__))


TEST_SIZE = 10 * 1024


class HTTPTestRequestHandler(http.server.BaseHTTPRequestHandler):
    # simulate network error only once
    _INJECTED = False

    def log_message(self, format, *args):
        pass

    def send_content_range(self, total):
        """
        Send `Content-Range` header according to the `Range` header in the request.
        Do nothing if `Range` is not present.

        @returns Value of `Content-Length` header
        """
        range_header = self.headers.get('Range')
        if not range_header:
            return total
        mobj = re.match(r'bytes=(?:(\d+)-(\d+)$|(\d+)-$)', range_header)
        if not mobj:
            return total
        start = int(mobj.group(1) or mobj.group(3))
        end = int(mobj.group(2) or (total - 1))
        self.send_header('Content-Range', f'bytes {start}-{end}/{total}')
        return (end - start + 1)

    def serve(self, range=True, content_length=True):
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        size = TEST_SIZE
        if range:
            size = self.send_content_range(TEST_SIZE)
        if content_length:
            self.send_header('Content-Length', size)
        self.end_headers()
        self.wfile.write(b'#' * size)

    def serve_too_short(self, actual_size):
        """Simulate broken connection"""
        self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Content-Length', TEST_SIZE)
        self.end_headers()
        self.wfile.write(b'#' * actual_size)

    def serve_range(self, total=TEST_SIZE):
        assert 'Range' in self.headers
        self.send_response(206)
        self.send_header('Content-Type', 'video/mp4')
        size = self.send_content_range(total)
        self.send_header('Content-Length', size)
        self.end_headers()
        self.wfile.write(b'#' * size)

    def do_GET(self):
        if self.path == '/regular':
            self.serve()
        elif self.path == '/no-content-length':
            self.serve(content_length=False)
        elif self.path == '/no-range':
            self.serve(range=False)
        elif self.path == '/no-range-no-content-length':
            self.serve(range=False, content_length=False)
        elif self.path == '/resume':
            if 'Range' in self.headers:
                self.serve_range()
            else:
                # simulate network error
                self.serve_too_short(actual_size=1024)
        elif self.path == '/resume-length-mismatch':
            if 'Range' in self.headers:
                self.serve_range(total=8 * 1024)
            elif HTTPTestRequestHandler._INJECTED:
                # normal response
                self.serve()
            else:
                # simulate network error
                self.serve_too_short(actual_size=1024)
                HTTPTestRequestHandler._INJECTED = True
        else:
            assert False


class TestHttpFD(unittest.TestCase):
    def setUp(self):
        self.httpd = http.server.HTTPServer(
            ('127.0.0.1', 0), HTTPTestRequestHandler)
        self.port = http_server_port(self.httpd)
        self.server_thread = threading.Thread(target=self.httpd.serve_forever)
        self.server_thread.daemon = True
        self.server_thread.start()

    def download(self, params, ep):
        params['logger'] = FakeLogger()
        ydl = YoutubeDL(params)
        downloader = HttpFD(ydl, params)
        filename = 'testfile.mp4'
        try_rm(encodeFilename(filename))
        self.assertTrue(downloader.real_download(filename, {
            'url': 'http://127.0.0.1:%d/%s' % (self.port, ep),
        }), ep)
        self.assertEqual(os.path.getsize(encodeFilename(filename)), TEST_SIZE, ep)
        try_rm(encodeFilename(filename))

    def download_all(self, params):
        for ep in ('regular', 'no-content-length', 'no-range', 'no-range-no-content-length'):
            self.download(params, ep)

    def test_regular(self):
        self.download_all({})

    def test_chunked(self):
        self.download_all({
            'http_chunk_size': 1000,
        })

    def test_resume(self):
        self.download({'retries': 1}, 'resume')

    def test_resume_length_mismatch(self):
        self.download({'retries': 2}, 'resume-length-mismatch')


if __name__ == '__main__':
    unittest.main()
