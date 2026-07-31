#!/usr/bin/env python3
"""
Experiment script to generate AI Videos using I2V_S and R2V modes on Flow Tool backend
for all 12 shots of the 'to-lam-power' project.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import flow_tool
import provider

PROJ_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "out", "to-lam-power"))
BEATS_FILE = os.path.join(PROJ_DIR, "beats.json")

def main():
    with open(BEATS_FILE, "r", encoding="utf-8") as f:
        doc = json.load(f)

    i2v_dir = os.path.join(PROJ_DIR, "clips_i2v_s")
    r2v_dir = os.path.join(PROJ_DIR, "clips_r2v")
    os.makedirs(i2v_dir, exist_ok=True)
    os.makedirs(r2v_dir, exist_ok=True)

    prov = provider.get_provider("flow_tool")

    i2v_jobs = {}
    r2v_jobs = {}
    shot_info = {}

    print("=== SUBMITTING I2V_S & R2V JOBS FOR 12 SHOTS ===")
    for beat in doc["beats"]:
        for shot in beat["shots"]:
            key = f"{beat['id']}{shot['id']}"
            kf_path = shot.get("keyframe_path")
            if not kf_path or not os.path.exists(kf_path):
                print(f"Skipping {key}: keyframe missing {kf_path}")
                continue

            prompt = shot.get("scene", "")
            dur = int(shot.get("dur", 8))
            safe_dur = dur if dur in (4, 6, 8) else 8

            shot_info[key] = {
                "key": key,
                "title": beat.get("title_en") or beat.get("title_cn") or f"Beat {beat['id']}",
                "prompt": prompt,
                "kf_path": kf_path
            }

            # Submit I2V_S
            try:
                job_i2v = flow_tool.submit_job(
                    "I2V_S",
                    prompt,
                    images=[kf_path],
                    quality="lite_low_priority",
                    ratio="landscape",
                    duration=safe_dur
                )
                i2v_jobs[key] = job_i2v
                print(f"[{key}] Submitted I2V_S -> {job_i2v}")
            except Exception as e:
                print(f"[{key}] I2V_S submission failed: {e}")

            # Submit R2V
            try:
                job_r2v = flow_tool.submit_job(
                    "R2V",
                    prompt,
                    images=[kf_path],
                    quality="lite_low_priority",
                    ratio="landscape",
                    duration=safe_dur
                )
                r2v_jobs[key] = job_r2v
                print(f"[{key}] Submitted R2V   -> {job_r2v}")
            except Exception as e:
                print(f"[{key}] R2V submission failed: {e}")

    print("\n=== POLLING AND DOWNLOADING I2V_S & R2V JOBS ===")
    results = {}
    for key in shot_info:
        results[key] = {"i2v_status": "pending", "r2v_status": "pending", "i2v_path": None, "r2v_path": None}

    start_time = time.time()
    deadline = start_time + 600  # 10 mins deadline

    while time.time() < deadline:
        all_done = True
        for key in shot_info:
            res = results[key]
            # Check I2V
            if res["i2v_status"] == "pending" and key in i2v_jobs:
                st = flow_tool.get_job_status(i2v_jobs[key])
                if st["status"] == "completed":
                    dest = os.path.join(i2v_dir, f"clip_i2v_s_{key}.mp4")
                    try:
                        flow_tool.download(st["output"], dest)
                        res["i2v_status"] = "completed"
                        res["i2v_path"] = dest
                        print(f"[{key}] I2V_S Completed & Downloaded -> {dest}")
                    except Exception as e:
                        res["i2v_status"] = f"download_error: {e}"
                elif st["status"] == "failed":
                    res["i2v_status"] = f"failed: {st.get('error')}"
                    print(f"[{key}] I2V_S Failed: {st.get('error')}")
                else:
                    all_done = False

            # Check R2V
            if res["r2v_status"] == "pending" and key in r2v_jobs:
                st = flow_tool.get_job_status(r2v_jobs[key])
                if st["status"] == "completed":
                    dest = os.path.join(r2v_dir, f"clip_r2v_{key}.mp4")
                    try:
                        flow_tool.download(st["output"], dest)
                        res["r2v_status"] = "completed"
                        res["r2v_path"] = dest
                        print(f"[{key}] R2V Completed & Downloaded   -> {dest}")
                    except Exception as e:
                        res["r2v_status"] = f"download_error: {e}"
                elif st["status"] == "failed":
                    res["r2v_status"] = f"failed: {st.get('error')}"
                    print(f"[{key}] R2V Failed: {st.get('error')}")
                else:
                    all_done = False

        if all_done:
            print("\n=== ALL I2V_S AND R2V JOBS COMPLETED ===")
            break
        time.sleep(5)

    # Save experiment results json
    exp_summary_path = os.path.join(PROJ_DIR, "i2v_r2v_experiment_results.json")
    summary_data = {"shots": shot_info, "results": results}
    with open(exp_summary_path, "w", encoding="utf-8") as f:
        json.dump(summary_data, f, ensure_ascii=False, indent=2)
    print(f"Saved experiment summary to {exp_summary_path}")

if __name__ == "__main__":
    main()
