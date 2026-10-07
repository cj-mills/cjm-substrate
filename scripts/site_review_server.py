"""Serve a built site for a render review: the tree as is, plus /__mode?to=<path>&m=dark|light, which
sets Quarto's colour-scheme key on the same origin and moves on to the page (the headless review's
dark mode, craft register 2026-10-02; the listing review of build b4897987, session 2026-10-07_11-19-37).

    python scripts/site_review_server.py <site dir> <port> [<bind address>]

The bind address defaults to 127.0.0.1; 0.0.0.0 serves the local network (a phone's review)."""

import functools
import http.server
import sys
from urllib.parse import parse_qs, urlsplit

ROOT, PORT = sys.argv[1], int(sys.argv[2])


class H(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/__mode":
            q = parse_qs(u.query)
            to, m = q.get("to", ["/"])[0], q.get("m", ["dark"])[0]
            val = "alternate" if m == "dark" else "default"
            body = (f"<script>localStorage.setItem('quarto-color-scheme','{val}');"
                    f"location.replace({to!r});</script>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def log_message(self, *a):
        pass


http.server.ThreadingHTTPServer((sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1", PORT), functools.partial(H, directory=ROOT)).serve_forever()
