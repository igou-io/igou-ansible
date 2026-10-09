"""Local, credential-free Tailscale API fixture for desktop teardown tests."""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


def device(node_id, hostname="codex-desktop", tag="tag:codex"):
    return {"id": node_id, "nodeId": node_id, "hostname": hostname, "tags": [tag]}


DEVICES = {
    "nDesktop": device("nDesktop"),
    "nWrongHost": device("nWrongHost", hostname="another-desktop"),
    "nWrongTag": device("nWrongTag", tag="tag:another"),
    "nWrongId": device("nOther"),
    "nUnrelated": device("nUnrelated", hostname="another-desktop"),
    "nFailure": device("nFailure"),
}
DELETIONS = []


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
        body = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode())
        if self.path != "/api/v2/oauth/token":
            self.reply(404, {})
        elif (
            body.get("client_id") != ["molecule-client"]
            or body.get("scope") != ["devices:core"]
            or body.get("tags") != ["tag:codex"]
        ):
            self.reply(403, {"message": "scope not granted"})
        else:
            self.reply(200, {"access_token": "molecule-token"})

    def do_GET(self):
        if self.path == "/audit":
            self.reply(200, {"deletions": DELETIONS, "devices": DEVICES})
            return
        if self.headers.get("Authorization") != "Bearer molecule-token":
            self.reply(401, {})
            return
        node_id = urlsplit(self.path).path.removeprefix("/api/v2/device/")
        self.reply(200, DEVICES[node_id]) if node_id in DEVICES else self.reply(404, {})

    def do_DELETE(self):
        if self.headers.get("Authorization") != "Bearer molecule-token":
            self.reply(401, {})
            return
        node_id = urlsplit(self.path).path.removeprefix("/api/v2/device/")
        if node_id == "nFailure":
            self.reply(500, {"message": "fixture deletion failure"})
            return
        if node_id not in DEVICES:
            self.reply(404, {})
            return
        DELETIONS.append(node_id)
        del DEVICES[node_id]
        self.reply(200, {})


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
