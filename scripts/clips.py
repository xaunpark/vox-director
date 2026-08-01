#!/usr/bin/env python3
"""
Motion stage: animates each poster into a living motion clip.

Supports both AI Image-to-Video models and high-quality local Ken Burns 2D motion
graphics rendering directly from keyframe poster images (kf_1a.jpg, kf_2a.jpg, etc.).
This ensures 100% exact retention of baked headline typography ("REAL MEXICAN FOOD", etc.)
and visual collage DNA when image-to-video AI uploads are unavailable.

Usage: python3 clips.py <project_dir>
"""
import json
import os
import subprocess
import sys

from provider import get_provider, run_jobs
from styles import resolve_video_aspect

VIDEO_MODEL = "google/gemini-omni-flash/image-to-video"


def probe_dur(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def render_kenburns_clip(kf_path: str, dest: str, camera_move: str = "push_in", duration: int = 5, aspect: str = "16:9"):
    """Render high-quality 2D motion clip directly from keyframe poster image."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    frames = duration * 24
    
    # Map resolution from aspect
    if aspect in ("9:16", "portrait"):
        scale = "1080x1920"
    else:
        scale = "1920x1080"

    move = (camera_move or "push_in").lower()
    if move in ("push_in", "zoom_in"):
        vf = f"zoompan=z='min(zoom+0.0012,1.15)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={scale}:fps=24"
    elif move in ("pull_out", "zoom_out"):
        vf = f"zoompan=z='max(1.15-0.0012*on,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={scale}:fps=24"
    elif move in ("pan", "tilt", "parallax"):
        vf = f"zoompan=z='1.08':x='if(eq(on,1),0,x+1.2)':y='ih/2-(ih/zoom/2)':d={frames}:s={scale}:fps=24"
    else:  # static
        vf = f"zoompan=z='1.02':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={scale}:fps=24"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-loop", "1", "-i", kf_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        dest
    ]
    subprocess.run(cmd, check=True)
    print(f"[kenburns] Motion rendered directly from {os.path.basename(kf_path)} -> {dest}")


def shots_of(beat):
    if "shots" in beat:
        for shot in beat["shots"]:
            yield shot, f"{beat['id']}{shot['id']}"
    else:
        yield beat, str(beat["id"])


def run(project_dir: str, target_shots: list = None):
    bpath = os.path.join(project_dir, "beats.json")
    with open(bpath, encoding="utf-8") as f:
        doc = json.load(f)

    cdir = os.path.join(project_dir, "clips")
    os.makedirs(cdir, exist_ok=True)

    prov = get_provider(doc.get("provider"))
    aspect = doc.get("aspect", "16:9")
    vid_res = doc.get("video_resolution", "720p")
    motion_style = doc.get("motion_style", "punchy")
    constraints = doc.get("constraints", "strict")
    model = doc.get("video_model", VIDEO_MODEL)

    resolved_aspect, model = resolve_video_aspect(aspect, model)
    if resolved_aspect != aspect and not doc.get("aspect_approx_confirmed"):
        print(f"ERROR: Video aspect ratio approximation from '{aspect}' to '{resolved_aspect}' requires confirmation.")
        sys.exit(1)

    specs = {}
    by_key = {}
    for beat in doc["beats"]:
        for shot, key in shots_of(beat):
            if target_shots and key not in target_shots:
                continue
            if shot.get("clip_url"):
                continue
            kf_path = shot.get("keyframe_path")
            url = shot.get("keyframe_url")
            if not url and kf_path and os.path.exists(kf_path):
                url = prov.upload(kf_path)
                shot["keyframe_url"] = url

            dur = int(shot.get("dur", 5))
            camera = shot.get("camera_move", "push_in")
            
            # Pass specs for provider video generation
            params = dict(
                image=url,
                keyframe_path=kf_path,
                duration=dur,
                aspect_ratio=aspect,
                resolution=vid_res,
                video_mode=doc.get("video_mode"),
                quality=doc.get("quality", "lite_low_priority"),
            )
            def make_submitter(m=model, p=shot.get("scene", ""), pr=params, s_ref=shot):
                jid = prov.submit_video(m, p, **pr)
                s_ref["job_id"] = jid
                return jid

            specs[key] = make_submitter
            by_key[key] = shot

    done = run_jobs(prov, specs, poll_s=4, stall_s=60, max_retries=2, deadline_s=900, max_concurrency=4, submit_delay_s=2.5)

    for beat in doc["beats"]:
        for shot, key in shots_of(beat):
            if target_shots and key not in target_shots:
                continue
            url = done.get(key)
            dest = os.path.join(cdir, f"clip_{key}.mp4")

            if url:
                try:
                    prov.download(url, dest)
                    shot["clip_url"] = url
                    shot["clip_path"] = dest
                    shot["clip_dur"] = round(probe_dur(dest), 2)
                    print(f"[{key}] {shot['clip_dur']}s (AI video, job_id={shot.get('job_id')}) -> {dest}")
                except Exception as e:
                    print(f"[{key}] AI clip download failed: {e}")
            else:
                # User requested NO Ken Burns fallback; leave clip incomplete so it can be retried
                print(f"[{key}] WARNING: AI video generation did not yield a clip.")

    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("updated", bpath)


if __name__ == "__main__":
    proj = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "out", "tang-30s")
    shots_filter = None
    if "--shots" in sys.argv:
        idx = sys.argv.index("--shots")
        if idx + 1 < len(sys.argv):
            shots_filter = sys.argv[idx + 1].split(",")
    run(os.path.abspath(proj), target_shots=shots_filter)
