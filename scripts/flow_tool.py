#!/usr/bin/env python3
"""
Flow Tool API client for vox-director.

Client wrapper for Flow Tool Multi-Account Cluster Backend (Google VEO 3.1 & Omni Flash).
Handles job submission (T2I, I2I, T2V, I2V_S, I2V_SE, R2V, Edit Video), status polling,
image/video uploads, upscaling, and downloading.

Env: FLOW_TOOL_BASE_URL (default: "http://localhost:8000")
"""
import base64
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("FLOW_TOOL_BASE_URL", "http://localhost:8000").rstrip("/")
UA = "vox-director/0.1 (+flow-tool)"


class FlowToolError(RuntimeError):
    pass


def _headers(json_body: bool = True) -> dict:
    h = {"User-Agent": UA}
    if json_body:
        h["Content-Type"] = "application/json"
    return h


def _post(path: str, payload: dict, base: str = BASE_URL, timeout: int = 60, max_retries: int = 4) -> dict:
    url = base + path
    data = json.dumps(payload).encode("utf-8")
    for attempt in range(1, max_retries + 1):
        req = urllib.request.Request(url, data=data, headers=_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429 and attempt < max_retries:
                print(f"[flow_tool] HTTP 429 Rate Limit (attempt {attempt}/{max_retries}). Waiting 15s...")
                time.sleep(15.0)
                continue
            raise FlowToolError(f"POST {path} -> {e.code}: {body[:400]}") from e
        except Exception as e:
            if attempt < max_retries:
                time.sleep(4.0)
                continue
            raise FlowToolError(f"POST {path} failed: {e}") from e


def _get(path: str, base: str = BASE_URL, timeout: int = 60, retries: int = 3) -> dict:
    url = base + path
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=_headers(json_body=False))
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            try:
                return json.loads(body)
            except ValueError:
                last = FlowToolError(f"GET {path} -> {e.code}: {body[:400]}")
                break
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2 ** i)
    raise FlowToolError(f"GET {path} failed after {retries} tries: {last}")


def submit_job(mode: str, prompt: str, **kwargs) -> str:
    """Submit a media generation job to Flow Tool. Returns job_id."""
    payload = {"mode": mode, "prompt": prompt}
    for k, v in kwargs.items():
        if v is not None:
            payload[k] = v
    res = _post("/v1/jobs", payload)
    if not res.get("success") and "job_id" not in res:
        raise FlowToolError(f"Job submission failed: {res}")
    return res["job_id"]


def get_job_status(job_id: str) -> dict:
    """Poll job status. Returns normalized dict:
    {"status": "pending"|"completed"|"failed", "output": <url|None>, "error": <msg|None>}
    """
    try:
        d = _get(f"/v1/jobs/{job_id}")
    except FlowToolError as e:
        return {"status": "failed", "output": None, "error": str(e)}

    st = d.get("status", "").upper()
    if st == "SUCCESS":
        urls = d.get("result_urls") or []
        out = urls[0] if urls else None
        return {"status": "completed", "output": out, "error": None}
    elif st in ("FAILED", "CANCELLED"):
        err = d.get("error") or d.get("message") or f"Job {st}"
        return {"status": "failed", "output": None, "error": err}
    else:
        # QUEUED, PENDING, PROCESSING
        return {"status": "pending", "output": None, "error": None}


def upload_image(file_path_or_url: str) -> str:
    """Convert local file path or HTTP URL to Base64 Data URI for Flow Tool."""
    if not file_path_or_url:
        return file_path_or_url
    if file_path_or_url.startswith("data:"):
        return file_path_or_url
    if file_path_or_url.startswith(("http://", "https://")):
        try:
            req = urllib.request.Request(file_path_or_url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                content_type = resp.headers.get("Content-Type", "image/jpeg").split(";")[0]
                b64 = base64.b64encode(data).decode("utf-8")
                return f"data:{content_type};base64,{b64}"
        except Exception:
            return file_path_or_url
    if os.path.exists(file_path_or_url):
        ext = os.path.splitext(file_path_or_url)[1].lower().lstrip(".")
        mime = "image/png" if ext == "png" else "image/jpeg"
        with open(file_path_or_url, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:{mime};base64,{b64}"
    return file_path_or_url


def upload_video(file_path: str, profile_id: str = None) -> tuple:
    """Upload video to Flow Tool via /v1/upload-video. Returns (media_id, profile_id)."""
    if not os.path.exists(file_path):
        raise FlowToolError(f"Video file not found: {file_path}")
    ext = os.path.splitext(file_path)[1].lower().lstrip(".")
    mime = "video/webm" if ext == "webm" else "video/mp4"
    with open(file_path, "rb") as f:
        v_b64 = base64.b64encode(f.read()).decode("utf-8")
    payload = {
        "video_data": v_b64,
        "mime_type": mime,
        "file_name": os.path.basename(file_path),
    }
    if profile_id:
        payload["profile_id"] = profile_id
    res = _post("/v1/upload-video", payload, timeout=120)
    if not res.get("success"):
        raise FlowToolError(f"Upload video failed: {res.get('error')}")
    return res["media_id"], res["profile_id"]


def upscale_image(job_id: str, resolution: str = "2K") -> dict:
    return _post(f"/v1/jobs/{job_id}/upscale-image", {"target_resolution": resolution})


def upscale_video(job_id: str) -> dict:
    return _post(f"/v1/jobs/{job_id}/upscale", {})


def download(url: str, dest: str) -> str:
    """Download output file safely via urllib or curl."""
    abs_dest = os.path.abspath(dest)
    os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=90) as resp, open(abs_dest, "wb") as f:
            f.write(resp.read())
    except Exception as e:
        subprocess.run(["curl", "-s", "-L", "--retry", "3", "-o", abs_dest, url], check=True)

    if not os.path.exists(abs_dest) or os.path.getsize(abs_dest) == 0:
        raise FlowToolError(f"Download produced empty file: {url}")
    return abs_dest
