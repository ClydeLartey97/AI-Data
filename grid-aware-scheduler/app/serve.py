"""
Local server for the dashboard — the thing you actually "run".

    ~/venvs/national-grid/bin/python -m app.serve

Then open http://localhost:8765. The page rebuilds itself from live market
data when you reload, rather than you re-running a command.

Nothing here is on the internet. This binds to 127.0.0.1, which means the
loopback interface only: other machines on the network cannot reach it, and
neither can anything outside. It is a local process that happens to speak
HTTP, which is how Jupyter and Ollama work too — the browser is only the
drawing surface.

That matters for where this is going. The real scheduler needs to read
hardware power draw (NVML, powermetrics) and stay alive long enough to launch
a job deferred to a window six hours out. A page served from a real website
could do neither. A local process serving a local page does both.

Data is cached briefly between requests because the underlying signals only
move on a half-hour settlement boundary — reloading twice in a minute should
not mean two round trips to the market APIs.
"""
from __future__ import annotations

import argparse
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from adapters.gb import GBAdapter
from app.dashboard import render
from core.grid import Job

#: Market signals move on a half-hour boundary, so anything fresher than this
#: is the same answer with extra network calls attached.
CACHE_SECONDS = 300


class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._html: str | None = None
        self._at: float = 0.0
        self._error: str | None = None

    def get(self, build) -> tuple[str, bool]:
        """Return (html, was_cached). Rebuilds when stale."""
        with self._lock:
            fresh = self._html is not None and (time.monotonic() - self._at) < CACHE_SECONDS
            if fresh:
                return self._html, True
            self._html = build()
            self._at = time.monotonic()
            return self._html, False


def _error_page(message: str) -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Grid Signal — error</title>
<style>
:root {{ color-scheme: light dark; }}
body {{ margin:0; min-height:100vh; display:grid; place-items:center;
  background:#F2F2F7; color:#000;
  font:15px/1.6 -apple-system, BlinkMacSystemFont, "SF Pro Text", Helvetica, Arial, sans-serif; }}
@media (prefers-color-scheme: dark) {{ body {{ background:#000; color:#fff; }} }}
.box {{ max-width:34rem; padding:28px 32px; border-radius:18px; background:#fff; }}
@media (prefers-color-scheme: dark) {{ .box {{ background:#1C1C1E; }} }}
h1 {{ margin:0 0 8px; font-size:22px; letter-spacing:-0.015em; }}
p {{ margin:0 0 6px; opacity:.65; }}
code {{ font-size:13px; }}
</style></head>
<body><div class="box">
<h1>Couldn't reach the market APIs</h1>
<p>The dashboard needs live data from the Carbon Intensity API and Elexon
Insights. Both are public and keyless, so this is almost always a network
problem rather than a credentials one.</p>
<p><code>{message}</code></p>
<p>Reload once you're back online.</p>
</div></body></html>"""


def make_handler(days: int, job: Job, cache: _Cache):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 - stdlib naming
            if self.path not in ("/", "/index.html"):
                self.send_error(404, "Not found")
                return

            def build() -> str:
                end = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
                adapter = GBAdapter()
                series = adapter.get_data(end - timedelta(days=days), end)
                if not series:
                    raise RuntimeError("adapter returned no settlement periods")
                return render(series, job, adapter.market_name, adapter.currency)

            try:
                html, cached = cache.get(build)
                status = 200
            except Exception as exc:  # network down, API outage, bad date range
                html, cached, status = _error_page(str(exc)), False, 503

            body = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            print(f"  {self.path} → {status} ({'cached' if cached else 'rebuilt from live data'})")

        def log_message(self, *args):  # quieter than the stdlib default
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the grid dashboard locally.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--power", type=float, default=6.5, help="job power draw in kW")
    ap.add_argument("--hours", type=float, default=4.0, help="job duration in hours")
    ap.add_argument("--deadline", type=float, default=24.0, help="deadline in hours")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser")
    args = ap.parse_args()

    job = Job(
        name="fine-tune run",
        power_kw=args.power,
        duration_periods=max(int(args.hours * 2), 1),
        deadline_periods=max(int(args.deadline * 2), 1),
    )

    url = f"http://localhost:{args.port}"
    # 127.0.0.1, not 0.0.0.0 — loopback only, so nothing outside this machine
    # can reach it.
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.days, job, _Cache()))

    print(f"Grid Signal running at {url}")
    print("  Local only — nothing is exposed to the network. Ctrl-C to stop.")
    if not args.no_open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
