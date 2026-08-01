#!/usr/bin/env python3
"""
Upscale Clips Stage for Vox-Director.
Upscales AI video clips in a project to 1080p Full HD using Flow Tool API.

Usage:
  python3 scripts/upscale_clips.py <project_dir> [--shots 1a,1b]
"""

import json
import os
import sys
import time

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import flow_tool
from provider import get_provider


def shots_of(beat):
    if "shots" in beat:
        for shot in beat["shots"]:
            yield shot, f"{beat['id']}{shot['id']}"
    else:
        yield beat, str(beat["id"])


def run_upscale(project_dir: str, target_shots: list = None):
    bpath = os.path.join(project_dir, "beats.json")
    if not os.path.exists(bpath):
        print(f"Error: beats.json not found in {project_dir}")
        sys.exit(1)

    with open(bpath, encoding="utf-8") as f:
        doc = json.load(f)

    cdir = os.path.join(project_dir, "clips")
    os.makedirs(cdir, exist_ok=True)
    prov = get_provider(doc.get("provider"))

    print(f"============================================================")
    print(f" 🚀 STARTING 1080p FULL HD UPSCALE FOR: {os.path.basename(project_dir)}")
    print(f"============================================================")

    upscale_queue = {}
    for beat in doc["beats"]:
        for shot, key in shots_of(beat):
            if target_shots and key not in target_shots:
                continue

            # Skip if already upscaled
            if shot.get("upscaled") and os.path.exists(shot.get("clip_path", "")):
                print(f"[{key}] Already upscaled to 1080p HD. Skipping.")
                continue

            job_id = shot.get("job_id") or shot.get("clip_job_id")
            # If job_id not stored, extract job_id from clip_url if possible
            clip_url = shot.get("clip_url", "")
            if not job_id and "outputs/output_" in clip_url:
                job_id = clip_url.split("output_")[-1].split(".mp4")[0]

            if not job_id:
                print(f"[{key}] Notice: No job_id found to trigger upscale. Skipping.")
                continue

            print(f"[{key}] Triggering 1080p Upscale for job {job_id}...")
            try:
                up_res = flow_tool.upscale_video(job_id)

                if up_res.get("success"):
                    upscale_queue[key] = {"job_id": job_id, "shot": shot}
                    print(f"[{key}] Upscale queued successfully.")
                else:
                    print(f"[{key}] Upscale request failed: {up_res}")
            except Exception as e:
                print(f"[{key}] Upscale API error: {e}")

    if not upscale_queue:
        print("No clips to upscale.")
        return

    print(f"\n--- Polling {len(upscale_queue)} upscale jobs ---")
    start_time = time.time()
    pending_keys = list(upscale_queue.keys())

    while pending_keys and time.time() - start_time < 900:
        time.sleep(5)
        for key in list(pending_keys):
            info = upscale_queue[key]
            jid = info["job_id"]
            shot = info["shot"]
            
            try:
                st = flow_tool.get_job_status(jid)
                status = st["status"]
                if status == "completed":
                    dest = os.path.join(cdir, f"clip_{key}.mp4")
                    new_url = st["output"]
                    prov.download(new_url, dest)

                    shot["clip_url"] = new_url
                    shot["clip_path"] = dest
                    shot["upscaled"] = True
                    shot["resolution"] = "1920x1080"
                    
                    print(f"[{key}] 🚀 1080p HD Upscale Complete -> {dest}")
                    pending_keys.remove(key)
                elif status == "failed":
                    print(f"[{key}] ❌ Upscale Failed: {st.get('error')}")
                    pending_keys.remove(key)
            except Exception as e:
                print(f"[{key}] Error checking upscale status: {e}")

    # Atomic write beats.json
    tmp_path = bpath + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, bpath)
    print("\nUpdated beats.json with 1080p HD upscale statuses.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/upscale_clips.py <project_dir> [--shots 1a,1b]")
        sys.exit(1)

    proj = sys.argv[1]
    shots_filter = None
    if "--shots" in sys.argv:
        idx = sys.argv.index("--shots")
        if idx + 1 < len(sys.argv):
            shots_filter = sys.argv[idx + 1].split(",")

    run_upscale(os.path.abspath(proj), shots_filter)
