"""A local HTTP server a test points callbacks at."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class Receiver:
    """A local HTTP server keeping what is POSTed to it."""

    def __init__(self):
        got = self.got = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                got.append(json.loads(self.rfile.read(int(self.headers["content-length"]))))
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/done"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
