"""Serve the scraper review UI on this computer only."""

import argparse
import csv
import io
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from .__main__ import run_csv
from .prices import write_csv
from .body_parts import OVERVIEW_FIELDS, overview_records


PAGE = Path(__file__).with_name("review.html")
MAX_UPLOAD = 2_000_000


class ReviewHandler(BaseHTTPRequestHandler):
    def reply(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def json_reply(self, status, payload):
        self.reply(status, json.dumps(payload).encode("utf-8"))

    @property
    def host(self):
        return f"127.0.0.1:{self.server.server_port}"

    def do_GET(self):
        if self.headers.get("Host") != self.host or self.path != "/":
            return self.json_reply(404, {"error": "Page not found"})
        self.reply(200, PAGE.read_bytes(), "text/html; charset=utf-8")

    def do_POST(self):
        if self.path != "/run":
            return self.json_reply(404, {"error": "Page not found"})
        if self.headers.get("Host") != self.host or self.headers.get("Origin") != f"http://{self.host}":
            return self.json_reply(403, {"error": "Open the review page at its local URL"})
        if self.headers.get_content_type() != "application/json":
            return self.json_reply(415, {"error": "Expected JSON"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_UPLOAD:
                return self.json_reply(413, {"error": "Combined upload must be under 2 MB"})
            self.connection.settimeout(10)
            request = json.loads(self.rfile.read(size))
            if not isinstance(request, dict) or not isinstance(request.get("csv"), str):
                raise ValueError("Upload a CSV with a pricing_url column")
            previous = request.get("offers", [])
            if not isinstance(previous, list) or any(not isinstance(row, dict) for row in previous):
                raise ValueError("The baseline must contain an offers array")
            report = run_csv(io.StringIO(request["csv"].lstrip("\ufeff"), newline=""), previous)
        except (ValueError, TypeError, KeyError, OSError, csv.Error) as exc:
            return self.json_reply(400, {"error": str(exc)})
        prices = io.StringIO(newline="")
        write_csv(prices, report["pricing_rows"])
        summary = io.StringIO(newline="")
        write_csv(summary, overview_records(report["body_part_overview"], report["pricing_rows"]), OVERVIEW_FIELDS)
        self.json_reply(200, {**report, "prices_csv": prices.getvalue(), "overview_csv": summary.getvalue()})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default=8765, type=int)
    args = parser.parse_args()
    with HTTPServer(("127.0.0.1", args.port), ReviewHandler) as server:
        print(f"Open http://127.0.0.1:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
