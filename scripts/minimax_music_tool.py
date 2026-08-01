#!/usr/bin/env python3
"""
MiniMax Music 3.0 Tool for Vox-Director
Generates custom instrumental BGM MP3 tracks via MiniMax API (music-3.0).
"""

import json
import os
import urllib.error
import urllib.request

import hashlib
import time

API_KEY = "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJHcm91cE5hbWUiOiJYdcOibiBUw7NjIOG7qCDEkOG7jyIsIlVzZXJOYW1lIjoiWHXDom4gVMOzYyDhu6ggxJDhu48iLCJBY2NvdW50IjoiIiwiU3ViamVjdElEIjoiMTkxMjgxMDAzNzA4NDk1MTA0MSIsIlBob25lIjoiIiwiR3JvdXBJRCI6IjE5MTI4MTAwMzcwNzY1NjI0MzMiLCJQYWdlTmFtZSI6IiIsIk1haWwiOiJ4YXVucGFya0BnbWFpbC5jb20iLCJDcmVhdGVUaW1lIjoiMjAyNS0wNC0xNyAyMTo1OTo1MiIsIlRva2VuVHlwZSI6MiwiaXNzIjoibWluaW1heCJ9.w8GmF-7Cjdwasdme7BqGMJTHIdShpp3ZVrYEzoNcNVE8nVLmf0cQcoyxu3symXBZM1Ix3DYXNJHeiUdm-N4kwGvtkzEHMwBZmz_tWqUmRyuL3nj3ppmkzDhl_SOBkaUgYSSn4VSGaDXs1WT_3eTvFHU6DPHAAkjRo2QEBpErIzQgm9JN7almAAKyEOjUiunMKuKaqO6MDKD9UO3ENvRSN6h6iYPT1ufrJPo30LfuLDMXh6BofTxDSrIOXfs967vjD_achMvVZHoFpm4fl1LjxI_wtbG6SST1VW6MemOCzoPZRMnRGHqv9Lju7h_9TJrjdTbcJ-wFGCGpcn8jOLmmBw"
GROUP_ID = "1912810037076562433"

# RPM Limit = 3 (Requests Per Minute) -> 20.5 seconds minimum interval between API calls
RPM_INTERVAL_SEC = 20.5
_LAST_REQUEST_TIMESTAMP = 0.0


def generate_bgm(prompt: str, dest_path: str, is_instrumental: bool = True, max_retries: int = 3) -> str:
    """
    Generates a BGM MP3 file given a style prompt using MiniMax Music 3.0 API.
    Enforces RPM=3 rate limiting (sleeping >= 20.5s between consecutive calls)
    and automatic retries. Saves to dest_path and returns the output path.
    """
    global _LAST_REQUEST_TIMESTAMP

    # Enforce RPM=3 rate-limit spacing
    now = time.time()
    elapsed = now - _LAST_REQUEST_TIMESTAMP
    if elapsed < RPM_INTERVAL_SEC:
        wait_time = RPM_INTERVAL_SEC - elapsed
        print(f"[minimax_music] RPM=3 rate limit safety: waiting {wait_time:.1f}s before next API request...")
        time.sleep(wait_time)

    url = f"https://api.minimaxi.chat/v1/music_generation?GroupId={GROUP_ID}"
    payload = {
        "model": "music-3.0-free",
        "prompt": prompt,
        "lyrics": "[Intro]\n(Instrumental Riff)\n[Verse]\n(Upbeat Solo)",
        "is_instrumental": is_instrumental
    }

    for attempt in range(1, max_retries + 1):
        _LAST_REQUEST_TIMESTAMP = time.time()
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {API_KEY}"
            },
            method="POST"
        )

        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                status = res_json.get("base_resp", {}).get("status_code")
                if status != 0:
                    msg = res_json.get("base_resp", {}).get("status_msg", "Unknown error")
                    if "rate" in msg.lower() or status in (1000, 2013, 429):
                        print(f"[minimax_music] Rate limit hit (attempt {attempt}/{max_retries}): {msg}. Waiting 25s...")
                        time.sleep(25.0)
                        continue
                    raise RuntimeError(f"MiniMax Music API error {status}: {msg}")

                audio_hex = res_json.get("data", {}).get("audio")
                if not audio_hex:
                    raise RuntimeError("No audio data returned in MiniMax Music response")

                audio_bytes = bytes.fromhex(audio_hex)
                os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
                with open(dest_path, "wb") as f:
                    f.write(audio_bytes)

                print(f"[minimax_music] Generated BGM MP3: {dest_path} ({len(audio_bytes)} bytes)")
                return dest_path

        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429 and attempt < max_retries:
                print(f"[minimax_music] HTTP 429 Rate Limit (attempt {attempt}/{max_retries}). Waiting 30s...")
                time.sleep(30.0)
                continue
            raise RuntimeError(f"MiniMax Music HTTP Error {e.code}: {body}")
        except Exception as e:
            if attempt < max_retries:
                print(f"[minimax_music] Retry {attempt}/{max_retries} on error: {e}")
                time.sleep(10.0)
                continue
            raise RuntimeError(f"MiniMax Music Generation Failed: {e}")

    raise RuntimeError(f"MiniMax Music Generation failed after {max_retries} attempts")


if __name__ == "__main__":
    import sys
    out = r"g:\VS-Project\Vox-Director\out\minimax_test.mp3"
    generate_bgm("Upbeat 70s rock guitar instrumental background music", out)
