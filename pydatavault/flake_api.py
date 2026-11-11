"""Legacy loopback manual-analysis prototype, separate from the planned storage API."""

from __future__ import annotations

import argparse
import base64
import binascii
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import re
import sqlite3

from .flake_analysis import FlakeAnalysisService, StaleModelError
from .flake_imaging import MAX_IMAGE_BYTES, PROCESSING_VERSION, decode_image, masks_from_polygons
from .flake_training import MODEL_SCHEMA

API_VERSION = 1
MAX_REQUEST_BYTES = 46 * 1024 * 1024


def make_server(database_path, host="127.0.0.1", port=51237) -> HTTPServer:
    """Loopback-only, read-only inference. No file paths or database writes in requests."""
    if host != "127.0.0.1":
        raise ValueError("The inference server only binds to 127.0.0.1")
    service = FlakeAnalysisService(database_path)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def reply(self, status, payload):
            data = json.dumps(payload, allow_nan=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/v1/health":
                self.reply(200, {"api_version": API_VERSION, "processing_version": PROCESSING_VERSION,
                                 "capabilities": ["manual_analysis", "profiles", "trained_model_export"],
                                 "model_schema": MODEL_SCHEMA})
            elif self.path == "/v1/profiles":
                self.reply(200, {"profiles": service.store.list_profiles()})
            elif match := re.fullmatch(r"/v1/profiles/([1-9][0-9]*)/model", self.path):
                self.read_model(profile_id=int(match[1]))
            elif match := re.fullmatch(r"/v1/models/([1-9][0-9]*)", self.path):
                self.read_model(model_id=int(match[1]))
            else:
                self.reply(404, {"error": "Unknown endpoint"})

        def read_model(self, **selection):
            try:
                model = service.store.get_model(**selection)
                if model is None:
                    self.reply(404, {"error": "No trained model found"})
                else:
                    self.reply(200, {"api_version": API_VERSION, "model": model})
            except sqlite3.Error:
                self.reply(503, {"error": "Photo database is temporarily unavailable"})

        def do_POST(self):
            if self.path != "/v1/analyze":
                self.reply(404, {"error": "Unknown endpoint"})
                return
            try:
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Content-Type must be application/json")
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_REQUEST_BYTES:
                    self.reply(413, {"error": "Missing or oversized request body"})
                    return
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict) or type(payload.get("profile_id")) is not int:
                    raise ValueError("profile_id must be an integer")
                data = base64.b64decode(payload["image_base64"], validate=True)
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("Image exceeds 32 MiB")
                rgb = decode_image(data)
                if "options" in payload or "substrate_rect" in payload:
                    raise ValueError("Automatic segmentation is unavailable; provide substrate_polygons and flake_polygons")
                labels, mask = masks_from_polygons(rgb.shape[:2], payload.get("substrate_polygons"), payload.get("flake_polygons"))
                result = service.analyze(rgb, payload["profile_id"], substrate_mask=mask, labels=labels,
                                         training_group_id=payload.get("training_group"))
                self.reply(200, {"api_version": API_VERSION, **result})
            except StaleModelError as exc:
                self.reply(409, {"error": str(exc), "code": "model_stale"})
            except (ValueError, TypeError, KeyError, binascii.Error) as exc:
                self.reply(400, {"error": str(exc)})
            except sqlite3.Error:
                self.reply(503, {"error": "Photo database is temporarily unavailable"})

        def log_message(self, format, *args):
            pass

    return HTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", help="Photo database path; otherwise use VAULT_DB_PATH")
    parser.add_argument("--port", type=int, default=51237)
    args = parser.parse_args()
    with make_server(args.db, port=args.port) as server:
        print(f"Flake inference API: http://127.0.0.1:{server.server_port}/v1/profiles", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
