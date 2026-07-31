#!/usr/bin/env python3
"""
Live generation test script to verify actual Image and Video generation via Flow Tool backend.
ASCII-only output to ensure compatibility across all Windows codepages.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import flow_tool
import provider


def test_live():
    base_url = os.environ.get("FLOW_TOOL_BASE_URL", "http://localhost:8000").rstrip("/")
    print(f"--- TESTING FLOW TOOL BACKEND AT: {base_url} ---")

    prov = provider.get_provider("flow_tool")

    # 1. Test Image Generation (T2I)
    print("\n[1/2] Submitting Image Generation (T2I)...")
    img_url = None
    try:
        img_job_id = prov.submit_image(
            "google/nano-banana-2/text-to-image",
            "A vibrant paper-collage illustration of a classic retro red sports car, 16:9 poster",
            aspect_ratio="16:9"
        )
        print(f"  -> Submitted Image Job ID: {img_job_id}")

        # Poll status
        print("  -> Polling image status...")
        start_t = time.time()
        while time.time() - start_t < 180:
            st = prov.get_status(img_job_id)
            print(f"     Status: {st['status']} | Output: {st.get('output')} | Error: {st.get('error')}")
            if st["status"] == "completed":
                img_url = st["output"]
                print(f"  [SUCCESS] IMAGE CREATED: {img_url}")
                break
            elif st["status"] == "failed":
                print(f"  [FAILED] IMAGE FAILED: {st.get('error')}")
                break
            time.sleep(4)

    except Exception as e:
        print(f"  [ERROR] Image Test Exception: {e}")

    # 2. Test Video Generation (I2V_S / T2V with quality='lite_low_priority')
    print("\n[2/2] Submitting Video Generation (with quality='lite_low_priority')...")
    try:
        test_img = img_url if img_url else "https://storage.googleapis.com/gtv-videos-bucket/sample/images/BigBuckBunny.jpg"
        video_job_id = prov.submit_video(
            "google/gemini-omni-flash/image-to-video",
            "A fast push-in shot of the red sports car driving down a mountain road, paper collage motion graphic",
            image=test_img,
            duration=8,
            aspect_ratio="16:9"
        )
        print(f"  -> Submitted Video Job ID: {video_job_id}")

        # Poll status
        print("  -> Polling video status...")
        start_t = time.time()
        while time.time() - start_t < 300:
            st = prov.get_status(video_job_id)
            print(f"     Status: {st['status']} | Output: {st.get('output')} | Error: {st.get('error')}")
            if st["status"] == "completed":
                print(f"  [SUCCESS] VIDEO CREATED: {st['output']}")
                break
            elif st["status"] == "failed":
                print(f"  [FAILED] VIDEO FAILED: {st.get('error')}")
                break
            time.sleep(5)

    except Exception as e:
        print(f"  [ERROR] Video Test Exception: {e}")


if __name__ == "__main__":
    test_live()
