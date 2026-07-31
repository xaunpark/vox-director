#!/usr/bin/env python3
"""
Assembly stage (ffmpeg): multi-shot clips + per-beat narration + music -> final.mp4

Model: beats -> shots. Each shot is one short clip (its own cut). Narration and
captions are per BEAT and span all the beat's shots, so the voice stays aligned
while the visuals cut. BGM is ducked under the narration. Captions + watermark
are Pillow PNGs composited with `overlay` (this ffmpeg has no libass/drawtext).

Usage: python3 assemble.py <project_dir>   (default: out/tang-30s)
"""
import json
import os
import subprocess
import sys

import text_overlay

FPS, TAIL = 24, 0.5
WATERMARK = "Made with Atlas Cloud · vox-director"
RES = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}


def ff(args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


def probe_dur(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def shots_of(beat):
    if beat.get("shots"):
        for s in beat["shots"]:
            yield s
    else:
        yield beat


def run(project_dir):
    with open(os.path.join(project_dir, "beats.json"), encoding="utf-8") as f:
        doc = json.load(f)
    beats = doc["beats"]
    W, H = RES.get(doc.get("aspect", "16:9"), (1920, 1080))
    wm_text = doc.get("watermark", WATERMARK)
    mix = doc.get("mix", {})                      # per-project audio balance (optional)
    music_vol = float(mix.get("music", 0.6))      # BGM level (was a fixed 0.9 — lowered so VO leads)
    voice_vol = float(mix.get("voice", 1.25))     # narration boost before the duck + final mix
    cap_style = doc.get("caption_style", "white") # white (default, clean) | paper (collage)
    tmp = os.path.join(project_dir, "_seg")
    os.makedirs(tmp, exist_ok=True)

    # ---- flatten shots into timed segments; track each beat's span ----
    segs = []          # {clip, dur}
    beat_spans = []    # {start, dur, beat}
    t = 0.0
    master_dur = float(doc.get("master_narration_dur", 0.0))

    for i, beat in enumerate(beats):
        beat_start = t
        shot_list = list(shots_of(beat))

        # Compute segment span: from this beat's audio_start to the NEXT beat's audio_start
        # (or master_narration_dur for the last beat). This includes silence gaps between sentences.
        has_alignment = beat.get("audio_start") is not None and beat.get("audio_end") is not None
        if has_alignment and master_dur > 0:
            a_start = float(beat["audio_start"])
            if i + 1 < len(beats) and beats[i + 1].get("audio_start") is not None:
                a_end = float(beats[i + 1]["audio_start"])
            else:
                a_end = master_dur
            seg_dur = round(a_end - a_start, 2)
            t = round(a_start + seg_dur, 2)   # keep absolute clock in sync with audio
        else:
            seg_dur = float(beat.get("narration_dur", 0.0)) or float(sum(float(s.get("dur", 5.0)) for s in shot_list))
            t += seg_dur

        durs = [round(seg_dur / max(1, len(shot_list)), 3)] * len(shot_list)

        for s, d in zip(shot_list, durs):
            clip_path = s.get("clip_path") or s.get("keyframe_path")
            if not clip_path or not os.path.exists(clip_path):
                kf_file = os.path.join(project_dir, "keyframes", f"kf_{s.get('id', beat['id'])}.jpg")
                if os.path.exists(kf_file):
                    clip_path = kf_file
                else:
                    clip_path = s.get("keyframe_path", "")
            segs.append({"clip": clip_path, "dur": round(d, 3)})
        beat_spans.append({"start": beat_start, "dur": round(t - beat_start, 2), "beat": beat})

    total = round(master_dur if master_dur > 0 else t, 2)

    # ---- 1) normalise each shot to a silent segment of exactly its dur ----
    seg_files = []
    for i, s in enumerate(segs):
        out = os.path.join(tmp, f"seg_{i:02d}.mp4")
        is_image = s["clip"].lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
        cd = probe_dur(s["clip"]) if not is_image else s["dur"]
        factor = s["dur"] / cd if (cd > 0 and not is_image) else 1.0
        pre = f"setpts={factor:.4f}*PTS," if abs(factor - 1.0) > 0.01 else ""
        fc = (f"[0:v]{pre}split[s0][s1];"
              f"[s0]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
              f"boxblur=26:2,eq=brightness=-0.05[bg];"
              f"[s1]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
              f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps={FPS},"
              f"tpad=stop_mode=clone:stop_duration=1[v]")
        input_args = ["-loop", "1", "-i", s["clip"]] if is_image else ["-i", s["clip"]]
        ff(input_args + ["-an", "-filter_complex", fc, "-map", "[v]", "-t", f"{s['dur']}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", out])
        seg_files.append(out)

    # ---- 2) concat all shot segments (video only) ----
    listf = os.path.join(tmp, "list.txt")
    with open(listf, "w") as f:
        for s in seg_files:
            f.write(f"file '{os.path.abspath(s)}'\n")
    body = os.path.join(tmp, "body_silent.mp4")
    ff(["-f", "concat", "-safe", "0", "-i", listf, "-c", "copy", body])

    # ---- 3) captions (per beat) + watermark PNGs ----
    captions_on = bool(doc.get("captions", True))  # "captions": false -> no burned-in captions
    cap_pngs = []
    if captions_on:
        for bs in beat_spans:
            beat = bs["beat"]
            p = os.path.join(tmp, f"cap_{beat['id']}.png")
            acc = None
            if cap_style == "paper":              # only the paper style uses a per-beat keyline
                kf = next((s["keyframe_path"] for s in (beat.get("shots") or [beat])
                           if s.get("keyframe_path") and os.path.exists(s["keyframe_path"])), None)
                acc = text_overlay.accent_color(kf) if kf else None
            text_overlay.render_caption(beat["narration"], p, W, H, accent=acc, style=cap_style)
            cap_pngs.append(p)
    wm_png = text_overlay.render_watermark(wm_text, os.path.join(tmp, "wm.png"), W, H)

    # ---- 4) one pass: overlay captions+wm, mix narration, duck BGM ----
    master_narr = doc.get("master_narration_path")
    if master_narr and os.path.exists(master_narr):
        use_master = True
    else:
        use_master = False

    bgm_p = doc.get("bgm_path") or os.path.join(project_dir, "audio", "bgm.mp3")
    nb = len(beat_spans)
    ncap = len(cap_pngs)                        # 0 when captions are off
    inputs = ["-i", body]                       # 0
    for p in cap_pngs:
        inputs += ["-i", p]                     # 1..ncap
    inputs += ["-i", wm_png]                    # ncap+1

    if use_master:
        narr_base = ncap + 2
        inputs += ["-i", master_narr]           # ncap+2
        bgm_idx = narr_base + 1
        inputs += ["-i", bgm_p]                 # ncap+3
    else:
        narr_base = ncap + 2
        for bs in beat_spans:
            narr_path = bs["beat"].get("narration_audio") or os.path.join(project_dir, "audio", f"narr_{bs['beat']['id']}.mp3")
            inputs += ["-i", narr_path]   # narr inputs
        bgm_idx = narr_base + nb
        inputs += ["-i", bgm_p]

    chain, prev = [], "[0:v]"
    for i, bs in enumerate(beat_spans[:ncap]):
        s, e = bs["start"] + 0.2, bs["start"] + bs["dur"] - 0.1
        lbl = f"[v{i+1}]"
        chain.append(f"{prev}[{i+1}:v]overlay=0:0:enable='between(t,{s:.2f},{e:.2f})'{lbl}")
        prev = lbl
    chain.append(f"{prev}[{ncap+1}:v]overlay=0:0[v]")

    if use_master:
        chain.append(f"[{narr_base}:a]volume={voice_vol},apad,atrim=0:{total}[narrmix]")
    else:
        # per-beat narration delayed to its start, then mixed
        nlabels = []
        for i, bs in enumerate(beat_spans):
            ms = int(bs["start"] * 1000)
            chain.append(f"[{narr_base+i}:a]adelay={ms}:all=1[n{i}]")
            nlabels.append(f"[n{i}]")
        chain.append(f"{''.join(nlabels)}amix=inputs={nb}:normalize=0:duration=longest,volume={voice_vol},apad,atrim=0:{total}[narrmix]")

    chain.append("[narrmix]asplit=2[narrA][narrB]")
    chain.append(f"[{bgm_idx}:a]atrim=0:{total},volume={music_vol},afade=t=out:st={max(total-2,0):.2f}:d=2[bgt]")
    chain.append("[bgt][narrA]sidechaincompress=threshold=0.02:ratio=12:attack=5:release=350[bgd]")
    chain.append(f"[narrB][bgd]amix=inputs=2:normalize=0:duration=longest,volume=1.4,atrim=0:{total}[a]")
    filt = ";".join(chain)

    final = os.path.join(project_dir, "final.mp4")
    ff([*inputs, "-filter_complex", filt, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", final])
    print("FINAL:", final, f"(~{total}s, {len(segs)} shots)")


if __name__ == "__main__":
    proj = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "out", "tang-30s")
    run(os.path.abspath(proj))
