#!/usr/bin/env python3
"""
Vox Studio Live Dashboard Server
Lightweight HTTP + SSE Server for real-time monitoring of Vox-Director video projects.
Runs on http://localhost:3300 by default.
"""

import json
import mimetypes
import os
import re
import shutil
import sys
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("VOX_DASHBOARD_PORT", 3300))
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
OUT_DIR = os.path.join(BASE_DIR, "out")
UI_DIR = os.path.join(BASE_DIR, "ui")

# Global SSE listeners
_SSE_CLIENTS = []


def safe_read_file(path, retries=5, delay=0.1):
    """Non-blocking file read with retry for Windows file locking."""
    for attempt in range(retries):
        try:
            if not os.path.exists(path):
                return None
            with open(path, "rb") as f:
                return f.read()
        except (PermissionError, OSError):
            if attempt < retries - 1:
                time.sleep(delay)
    return None


def get_projects_list():
    """List all projects in out/ sorted by modification time."""
    if not os.path.exists(OUT_DIR):
        return []
    projects = []
    for item in os.listdir(OUT_DIR):
        p_dir = os.path.join(OUT_DIR, item)
        if os.path.isdir(p_dir):
            bfile = os.path.join(p_dir, "beats.json")
            mtime = os.path.getmtime(bfile) if os.path.exists(bfile) else os.path.getmtime(p_dir)
            projects.append({"id": item, "name": item, "mtime": mtime, "has_beats": os.path.exists(bfile)})
    projects.sort(key=lambda x: x["mtime"], reverse=True)
    return projects


def get_project_state(project_id=None):
    """Get complete snapshot state for a project."""
    projects = get_projects_list()
    if not projects:
        return {"projects": [], "active_project": None, "state": None}

    active_id = project_id
    if not active_id or not any(p["id"] == active_id for p in projects):
        active_id = projects[0]["id"]

    p_dir = os.path.join(OUT_DIR, active_id)
    beats_file = os.path.join(p_dir, "beats.json")

    beats_doc = None
    if os.path.exists(beats_file):
        raw = safe_read_file(beats_file)
        if raw:
            try:
                beats_doc = json.loads(raw.decode("utf-8"))
            except Exception:
                pass

    keyframes_dir = os.path.join(p_dir, "keyframes")
    clips_dir = os.path.join(p_dir, "clips")
    final_file = os.path.join(p_dir, "final.mp4")
    master_audio = os.path.join(p_dir, "audio", "master_narration.mp3")

    keyframes = {}
    if os.path.exists(keyframes_dir):
        for f in os.listdir(keyframes_dir):
            if f.endswith((".jpg", ".jpeg", ".png")):
                fpath = os.path.join(keyframes_dir, f)
                keyframes[f] = {"name": f, "mtime": os.path.getmtime(fpath), "size": os.path.getsize(fpath)}

    clips = {}
    if os.path.exists(clips_dir):
        for f in os.listdir(clips_dir):
            if f.endswith(".mp4"):
                fpath = os.path.join(clips_dir, f)
                size = os.path.getsize(fpath)
                if size > 0:  # Ignore 0-byte writing files
                    clips[f] = {"name": f, "mtime": os.path.getmtime(fpath), "size": size}

    return {
        "projects": projects,
        "active_id": active_id,
        "doc": beats_doc,
        "keyframes": keyframes,
        "clips": clips,
        "has_final": os.path.exists(final_file) and os.path.getsize(final_file) > 0,
        "has_master_audio": os.path.exists(master_audio) and os.path.getsize(master_audio) > 0,
        "timestamp": time.time()
    }


def broadcast_sse_event(event_type, data):
    """Notify all connected SSE browser clients."""
    msg = f"event: {event_type}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
    dead = []
    for client in _SSE_CLIENTS:
        try:
            client.wfile.write(msg)
            client.wfile.flush()
        except Exception:
            dead.append(client)
    for d in dead:
        if d in _SSE_CLIENTS:
            _SSE_CLIENTS.remove(d)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class DashboardRequestHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Suppress noisy GET log spam
        pass

    def send_range_headers(self, file_size, start, end):
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", mimetypes.guess_type(self.path)[0] or "application/octet-stream")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        return start, length

    def handle_static_media(self, abs_path):
        """Serve media/static files with Range support (Code 206) for video scrubbing."""
        if not os.path.exists(abs_path):
            self.send_error(404, "File not found")
            return

        file_size = os.path.getsize(abs_path)
        range_header = self.headers.get("Range")

        if range_header:
            match = re.search(r"bytes=(\d+)-(\d*)", range_header)
            if match:
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else file_size - 1
                if end >= file_size:
                    end = file_size - 1
                start, length = self.send_range_headers(file_size, start, end)

                with open(abs_path, "rb") as f:
                    f.seek(start)
                    chunk_size = 64 * 1024
                    remaining = length
                    while remaining > 0:
                        chunk = f.read(min(chunk_size, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                return

        # Regular non-range response
        self.send_response(200)
        mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        data = safe_read_file(abs_path)
        if data:
            self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # 1. SSE Live Events Stream
        if path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            _SSE_CLIENTS.append(self)
            # Keep-alive loop
            try:
                while True:
                    time.sleep(15)
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except Exception:
                if self in _SSE_CLIENTS:
                    _SSE_CLIENTS.remove(self)
            return

        # 2. REST API: Current State Snapshot
        if path == "/api/state":
            project_id = query.get("project", [None])[0]
            state = get_project_state(project_id)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(state).encode("utf-8"))
            return

        # 3. Static Media from out/ directory
        if path.startswith("/media/"):
            rel_path = path[7:]  # Strip /media/
            abs_path = os.path.join(OUT_DIR, rel_path)
            self.handle_static_media(abs_path)
            return

        # 4. Frontend Web UI Files
        if path == "/" or path == "/index.html":
            target = os.path.join(UI_DIR, "index.html")
        else:
            target = os.path.join(UI_DIR, path.lstrip("/"))

        if os.path.exists(target) and not os.path.isdir(target):
            self.send_response(200)
            mime = mimetypes.guess_type(target)[0] or "text/html"
            self.send_header("Content-Type", mime)
            self.end_headers()
            with open(target, "rb") as f:
                self.wfile.write(f.read())
            return

        self.send_error(404, "Page not found")


def start_file_watcher():
    """Background thread watching out/ directory and pushing SSE events."""
    import threading

    def watch_loop():
        last_state_hash = None
        while True:
            try:
                state = get_project_state()
                current_hash = hashlib_state(state)
                if last_state_hash and current_hash != last_state_hash:
                    broadcast_sse_event("state_update", state)
                last_state_hash = current_hash
            except Exception as e:
                pass
            time.sleep(1.0)

    t = threading.Thread(target=watch_loop, daemon=True)
    t.start()


def hashlib_state(state):
    import hashlib
    clean_state = {k: v for k, v in state.items() if k != "timestamp"}
    s = json.dumps(clean_state, sort_keys=True)
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def main():
    os.makedirs(UI_DIR, exist_ok=True)
    os.makedirs(OUT_DIR, exist_ok=True)

    start_file_watcher()
    server = ThreadedHTTPServer(("0.0.0.0", PORT), DashboardRequestHandler)
    print(f"============================================================")
    print(f"  VOX STUDIO LIVE DASHBOARD RUNNING AT:")
    print(f"  http://localhost:{PORT}")
    print(f"============================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Dashboard server.")
        server.server_close()


if __name__ == "__main__":
    main()
