"""Credential-free OAuth/join-key fixture for ephemeral desktop enrollment."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

AUDIT = {"oauth_tokens": 0, "join_keys": 0, "requests": []}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        if self.path == "/shutdown":
            self.reply(200, {})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        AUDIT["requests"].append(self.path)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        if self.path == "/api/v2/oauth/token":
            body = parse_qs(raw)
            if body.get("client_id") != ["molecule-client"] or body.get("scope") != ["auth_keys"]:
                self.reply(403, {"message": "scope not granted"})
                return
            AUDIT["oauth_tokens"] += 1
            self.reply(200, {"access_token": "molecule-token"})
        elif self.path == "/api/v2/tailnet/-/keys":
            body = json.loads(raw)
            expected = {"reusable": False, "ephemeral": True, "preauthorized": True, "tags": ["tag:codex"]}
            if (
                self.headers.get("Authorization") != "Bearer molecule-token"
                or body.get("expirySeconds") != 3600
                or body.get("capabilities", {}).get("devices", {}).get("create") != expected
            ):
                self.reply(400, {"message": "not a single-use tagged ephemeral key"})
                return
            AUDIT["join_keys"] += 1
            self.reply(200, {"key": f"molecule-join-{AUDIT['join_keys']}"})
        else:
            self.reply(404, {})

    def do_GET(self):
        self.reply(200, AUDIT) if self.path == "/audit" else self.reply(404, {})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port-file", required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Path(args.port_file).write_text(str(server.server_port))
    try:
        server.serve_forever()
    finally:
        server.server_close()
