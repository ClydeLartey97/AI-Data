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
import html
import json
import os
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app import api, dashboard, decisions, planner, simulator, workloads
from app.markets import load_market, summarise_market
from core import audit_store, evidence_store
from core.grid import Job
from hardware.providers import LocalDetector, RedfishFleetProvider
from hardware.calibration import load_profiles
from hardware import apple_benchmark, inventory_store
from hardware.reference_language import catalogue_entry

#: Market signals move on a half-hour boundary, so anything fresher than this
#: is the same answer with extra network calls attached.
CACHE_SECONDS = 300
SITE_CACHE_SECONDS = 1800

#: Facility discovery configuration (docs/discovery.md). Local operator
#: config, never committed; overridable for tests and alternate deployments.
DISCOVERY_CONFIG_PATH = Path(__file__).resolve().parents[1] / "data" / "discovery.json"


def _discovery_config_path() -> Path:
    override = os.environ.get("DISCOVERY_CONFIG")
    return Path(override) if override else DISCOVERY_CONFIG_PATH


class _Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[object, float]] = {}

    def get(self, build, key: str = "default") -> tuple[object, bool]:
        """Return (value, was_cached). Rebuilds when stale."""
        with self._lock:
            entry = self._entries.get(key)
            if entry and (time.monotonic() - entry[1]) < CACHE_SECONDS:
                return entry[0], True
            rendered = build()
            self._entries[key] = (rendered, time.monotonic())
            return rendered, False


class _AsyncSiteCache:
    """Non-blocking supplementary site signals for the Simulator.

    The core market and hardware page renders immediately. Weather and
    regional-carbon calls refresh in one daemon thread and the page polls the
    local endpoint. A stalled optional source can therefore never stall the
    product navigation path.
    """

    def __init__(self, build=None) -> None:
        self._build = build or (lambda: simulator.build_sites())
        self._lock = threading.Lock()
        self._data: dict = {}
        self._updated = 0.0
        self._refreshing = False

    def _refresh(self) -> None:
        try:
            result = self._build()
        except Exception:
            result = {}
        with self._lock:
            if result:
                self._data = result
                self._updated = time.monotonic()
            self._refreshing = False

    def snapshot(self) -> tuple[dict, bool]:
        with self._lock:
            stale = (not self._data or
                     (time.monotonic() - self._updated) >= SITE_CACHE_SECONDS)
            if stale and not self._refreshing:
                self._refreshing = True
                threading.Thread(target=self._refresh, daemon=True,
                                 name="site-signal-refresh").start()
            return dict(self._data), self._refreshing


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
<p>The selected market view needs public grid data. This is usually a network,
upstream availability, rate-limit, or location-identifier problem.</p>
<p><code>{html.escape(message)}</code></p>
<p>Reload once you're back online.</p>
</div></body></html>"""


def make_handler(days: int, job: Job, cache: _Cache, sim_cache: _Cache,
                 planner_cache: _Cache, data_cache: _Cache,
                 site_cache: _AsyncSiteCache | None = None):
    site_cache = site_cache or _AsyncSiteCache()
    def get_context(market: str, location: str, *, planning: bool, span: int):
        key = f"{market}:{location}:{planning}:{span}"
        value, _ = data_cache.get(
            lambda: load_market(market, location, days=span, planner=planning),
            key,
        )
        return value

    class Handler(BaseHTTPRequestHandler):
        def _send(self, body: bytes, status: int, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self' data:; script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'self'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Browsers cancel superseded navigation requests routinely.
                # The response is abandoned; the server itself remains healthy.
                pass

        def _send_json(self, payload: dict, status: int = 200) -> None:
            body = json.dumps(
                payload, ensure_ascii=False, allow_nan=False
            ).encode("utf-8")
            self._send(body, status, "application/json; charset=utf-8")

        @staticmethod
        def _market_location(query: dict[str, list[str]]) -> tuple[str, str]:
            market = query.get("market", ["GB"])[0].upper()
            default_location = {"CAISO": "sp15", "NYISO": "nyc", "MISO": "indiana"}.get(
                market, "national"
            )
            custom_node = query.get("custom_node", [""])[0].strip()
            location = custom_node or query.get("location", [default_location])[0]
            return market, location

        def do_GET(self):  # noqa: N802 - stdlib naming
            request = urlsplit(self.path)
            path = request.path.rstrip("/") or "/"
            query = parse_qs(request.query)
            if path == "/api/v1/health":
                self._send_json({
                    "status": "ok",
                    "api_version": api.API_VERSION,
                    "product_version": api.PRODUCT_VERSION,
                    "local_only": True,
                })
                return
            if path == "/api/v1/sites":
                sites, refreshing = site_cache.snapshot()
                self._send_json({
                    "api_version": api.API_VERSION,
                    "sites": sites,
                    "refreshing": refreshing,
                })
                return
            if path == "/api/v1/inventory":
                try:
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "product_version": api.PRODUCT_VERSION,
                        "configured": _discovery_config_path().exists(),
                        "summary": inventory_store.summary(),
                        "snapshot": inventory_store.latest(),
                    })
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, 400)
                return
            if path == "/api/v1/evidence/profiles":
                try:
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "product_version": api.PRODUCT_VERSION,
                        "summary": evidence_store.summary(),
                        "profiles": evidence_store.list_profiles(),
                        "collectors": [catalogue_entry()],
                    })
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, 400)
                return
            if path == "/api/v1/decisions":
                try:
                    limit = int(query.get("limit", ["50"])[0])
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "decisions": audit_store.list_decisions(limit),
                    })
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, 400)
                return
            if path.startswith("/api/v1/decisions/"):
                decision_id = path.rsplit("/", 1)[-1]
                decision = audit_store.get_decision(decision_id)
                if decision is None:
                    self._send_json({"error": "decision not found"}, 404)
                else:
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "decision": decision,
                    })
                return
            if path not in ("/", "/index.html", "/operations", "/grid",
                            "/simulator", "/planner", "/workloads", "/decisions",
                            "/api/v1/market"):
                self.send_error(404, "Not found")
                return

            market, location = self._market_location(query)
            market_cache_key = f"{market}:{location}:{days}"

            if path == "/api/v1/market":
                try:
                    context = get_context(market, location, planning=True, span=2)
                    self._send_json(api.market_response(context))
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, 400)
                except Exception as exc:
                    self._send_json({"error": str(exc)}, 503)
                return

            def build_simulator() -> str:
                context = get_context(market, location, planning=True, span=2)
                return simulator.render(simulator.device_specs(),
                                        simulator.model_specs(),
                                        summarise_market(context),
                                        site_cache.snapshot()[0],
                                        LocalDetector().detect().public_dict())

            def build_dashboard() -> str:
                context = get_context(market, location, planning=False, span=days)
                if not context.series:
                    raise RuntimeError("no market data available")
                return dashboard.render(context.series, job, context.market_name,
                                        context.currency, context=context)

            def build_planner() -> str:
                context = get_context(market, location, planning=True, span=2)
                if not context.series:
                    raise RuntimeError("no complete planning data available")
                return planner.render(context)

            def build_workloads() -> str:
                context = get_context(market, location, planning=True, span=2)
                if not context.series:
                    raise RuntimeError("no complete planning data available")
                return workloads.render(context)

            try:
                if path == "/decisions":
                    html, cached = decisions.render(), True
                elif path in ("/", "/index.html", "/operations", "/workloads"):
                    html, cached = planner_cache.get(
                        build_workloads, "workloads:" + market_cache_key
                    )
                elif path == "/simulator":
                    html, cached = sim_cache.get(build_simulator, market_cache_key)
                elif path == "/planner":
                    html, cached = planner_cache.get(build_planner, market_cache_key)
                elif path == "/grid":
                    html, cached = cache.get(build_dashboard, market_cache_key)
                else:  # guarded above; keeps static analysers exhaustive
                    raise RuntimeError("unsupported page route")
                status = 200
            except Exception as exc:  # network down, API outage, bad date range
                html, cached, status = _error_page(str(exc)), False, 503

            body = html.encode("utf-8")
            self._send(body, status, "text/html; charset=utf-8")
            print(f"  {path} -> {status} ({'cached' if cached else 'rebuilt from market data'})")

        def do_POST(self):  # noqa: N802 - stdlib naming
            request = urlsplit(self.path)
            path = request.path.rstrip("/") or "/"
            score_prefix = "/api/v1/decisions/"
            is_score = path.startswith(score_prefix) and path.endswith("/score")
            is_portfolio = path == "/api/v1/portfolio"
            is_evidence = path == "/api/v1/evidence/observations"
            is_evidence_probe = path == "/api/v1/evidence/probe"
            is_inventory_refresh = path == "/api/v1/inventory/refresh"
            if (path != "/api/v1/plan" and not is_portfolio
                    and not is_score and not is_evidence
                    and not is_evidence_probe and not is_inventory_refresh):
                self.send_error(404, "Not found")
                return
            if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
                self._send_json({"error": "Content-Type must be application/json"}, 415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json({"error": "invalid Content-Length"}, 400)
                return
            if length <= 0 or length > 65_536:
                self._send_json({"error": "JSON body must be between 1 byte and 64 KiB"}, 413)
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
                if is_inventory_refresh:
                    if not isinstance(payload, dict):
                        raise ValueError("refresh request must be an object")
                    config = _discovery_config_path()
                    if not config.exists():
                        self._send_json({
                            "error": f"no discovery configuration at {config}; "
                                     "declare read-only endpoints per docs/discovery.md",
                        }, 409)
                        return
                    result = RedfishFleetProvider.from_config(config).detect()
                    snapshot = result.public_dict()
                    snapshot_id = inventory_store.record_snapshot(snapshot)
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "product_version": api.PRODUCT_VERSION,
                        "snapshot_id": snapshot_id,
                        "snapshot": snapshot,
                        "summary": inventory_store.summary(),
                    }, 201)
                    return
                if is_evidence_probe:
                    if not isinstance(payload, dict):
                        raise ValueError("probe request must be an object")
                    result = apple_benchmark.run_mlx_probe(
                        payload.get("matrix_size", 256),
                        payload.get("iterations", 5),
                    )
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "product_version": api.PRODUCT_VERSION,
                        "probe": result,
                    })
                    return
                if is_evidence:
                    result = evidence_store.ingest_payload(payload)
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "product_version": api.PRODUCT_VERSION,
                        "result": result,
                        "summary": evidence_store.summary(),
                    }, 200 if result["duplicate"] else 201)
                    return
                if is_score:
                    decision_id = path[len(score_prefix):-len("/score")].strip("/")
                    decision = audit_store.get_decision(decision_id)
                    if decision is None:
                        self._send_json({"error": "decision not found"}, 404)
                        return
                    score = api.score_response(decision, payload)
                    audit_store.save_score(decision_id, score)
                    self._send_json({
                        "api_version": api.API_VERSION,
                        "score": score,
                    })
                    return
                query = parse_qs(request.query)
                if isinstance(payload, dict):
                    query.setdefault("market", [str(payload.get("market", "GB"))])
                    default = {"CAISO": "sp15", "NYISO": "nyc", "MISO": "indiana"}.get(
                        str(payload.get("market", "GB")).upper(), "national"
                    )
                    query.setdefault("location", [str(payload.get("location", default))])
                market, location = self._market_location(query)
                context = get_context(market, location, planning=True, span=2)
                if is_portfolio:
                    self._send_json(api.portfolio_response(
                        payload, context, evidence_store.profile_map(),
                    ))
                    return
                response = api.plan_response(payload, context, load_profiles())
                decision_id = audit_store.save_decision(
                    market=context.market_key,
                    location=context.location_name,
                    signal_mode=context.signal_mode,
                    request=payload,
                    response=response,
                    signals=context.series,
                )
                response["decision_id"] = decision_id
                self._send_json(response)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                self._send_json({"error": f"invalid JSON: {exc}"}, 400)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self._send_json({"error": str(exc)}, 503)

        def log_message(self, *args):  # quieter than the stdlib default
            pass

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="Serve the AI operations console locally.")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--days", type=int, default=400)
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
    server = ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(args.days, job, _Cache(), _Cache(), _Cache(), _Cache()),
    )

    print(f"AI Operations running at {url}")
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
