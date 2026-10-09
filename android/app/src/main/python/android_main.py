"""Starts SlideGen inside the Android app as a web server that only this phone can reach."""

import os
import tempfile
import threading

PORT = 8765  # fixed, so the WebView keeps the same origin (saved settings) between launches
_server = None


def start(data_dir):
    """Start the server once per app process and return its port."""
    global _server
    if _server is None:
        tmp = os.path.join(data_dir, "tmp")
        os.makedirs(tmp, exist_ok=True)
        os.environ["SLIDEGEN_DATA"] = data_dir
        os.environ.setdefault("HOME", data_dir)
        os.environ["TMPDIR"] = tempfile.tempdir = tmp

        from werkzeug.serving import make_server
        import app

        try:
            _server = make_server("127.0.0.1", PORT, app.app, threaded=True)
        except OSError:  # port taken by another app: any free port works
            _server = make_server("127.0.0.1", 0, app.app, threaded=True)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
    return _server.server_port
