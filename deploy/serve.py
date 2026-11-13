"""Static server for the site on a home server: python3 deploy/serve.py PORT, run from the site directory. Stdlib only.

Over python -m http.server it adds what a 20 MB scene needs on a phone:
- gzip: when the client accepts it and FILE.gz exists (tools/build_site.py writes them), the .gz bytes go out with
  Content-Encoding: gzip, plus X-File-Size (the decoded size) so the viewer's progress bar stays right;
- revalidation: every response carries an ETag (size and mtime of the file) and Cache-Control: no-cache, so a repeat
  visit costs one 304 instead of the whole scene, and a new deploy shows up at once;
- threads, so the scene download never blocks the pages."""
import email.utils
import http.server
import os
import sys


class Handler(http.server.SimpleHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".glb": "model/gltf-binary", ".js": "text/javascript", ".mjs": "text/javascript",
                      ".wasm": "application/wasm", ".hdr": "image/vnd.radiance", ".json": "application/json"}

    def send_head(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            if not self.path.split("?")[0].endswith("/"):
                return super().send_head()
            path = os.path.join(path, "index.html")
        if not os.path.isfile(path):
            return super().send_head()
        st = os.stat(path)
        etag = f'"{st.st_size:x}-{st.st_mtime_ns:x}"'
        gz = path + ".gz"
        use_gz = "gzip" in self.headers.get("Accept-Encoding", "") and os.path.isfile(gz) \
            and os.stat(gz).st_mtime_ns >= st.st_mtime_ns
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Vary", "Accept-Encoding")
            self.end_headers()
            return None
        f = open(gz if use_gz else path, "rb")
        size = os.fstat(f.fileno()).st_size
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Length", str(size))
        if use_gz:
            self.send_header("Content-Encoding", "gzip")
            self.send_header("X-File-Size", str(st.st_size))
        self.send_header("Vary", "Accept-Encoding")
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", email.utils.formatdate(st.st_mtime, usegmt=True))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        return f

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
