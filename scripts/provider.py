#!/usr/bin/env python3
"""
Provider abstraction — the pluggable media backend the pipeline stages talk to.

Supports Flow Tool (Visual AI) & OmniVoice Local (Voice TTS / Voice Cloning on local GPU),
delegating to Atlas Cloud only when explicitly configured.

Pick a backend per project with beats.json `{"provider": "flow_tool"}` (default) or `{"provider": "atlas_cloud"}`.
"""
import os
import shutil
import tempfile
import time
from abc import ABC, abstractmethod

import atlas_cloud
import flow_tool
import omnivoice_tool
import gemini_tts_tool


class ProviderError(RuntimeError):
    pass


class Provider(ABC):
    """The surface the stages need. get_status normalizes every backend's polling
    response to {status: pending|completed|failed, output: <url|None>, error}."""
    name = "base"

    @abstractmethod
    def submit_image(self, model, prompt, **params): ...
    @abstractmethod
    def submit_video(self, model, prompt, **params): ...
    @abstractmethod
    def submit_audio(self, model, **params): ...
    @abstractmethod
    def remove_bg(self, model, image_url, **params): ...
    @abstractmethod
    def get_status(self, job_id): ...
    @abstractmethod
    def upload(self, path): ...
    @abstractmethod
    def download(self, url, dest): ...


class AtlasCloudProvider(Provider):
    """Wraps the atlas_cloud client — identical behavior to calling it directly."""
    name = "atlas_cloud"

    def submit_image(self, model, prompt, **params):
        return atlas_cloud.submit_image(model, prompt, **params)

    def submit_video(self, model, prompt, **params):
        return atlas_cloud.submit_video(model, prompt, **params)

    def submit_audio(self, model, **params):
        return atlas_cloud.submit_media(model, **params)

    def remove_bg(self, model, image_url, **params):
        body = {"model": model, "image": image_url, **params}
        return atlas_cloud._post("/model/generateImage", body)["data"]["id"]

    def get_status(self, job_id):
        try:
            d = atlas_cloud._get(f"/model/prediction/{job_id}").get("data", {})
        except atlas_cloud.AtlasCloudError as e:
            return {"status": "failed", "output": None, "error": str(e)}
        st = d.get("status")
        if st in ("completed", "succeeded"):
            out = d.get("outputs") or d.get("output")
            out = out[0] if isinstance(out, list) else out
            return {"status": "completed", "output": out, "error": None}
        if st == "failed":
            return {"status": "failed", "output": None, "error": d.get("error", "")}
        return {"status": "pending", "output": None, "error": None}

    def upload(self, path):
        return atlas_cloud.upload(path)

    def download(self, url, dest):
        return atlas_cloud.download(url, dest)


class FlowToolProvider(Provider):
    """Wraps flow_tool for Visual AI and omnivoice_tool for Local GPU Voice AI
    (TTS, Voice Cloning, Voice Design), delegating to Atlas Cloud only if requested.

    RÀNG BUỘC: Tất cả các tác vụ tạo video (T2V, I2V_S, I2V_SE, R2V, Video Edit)
    được đảm bảo LUÔN LUÔN sử dụng model quality "lite_low_priority".
    """
    name = "flow_tool"

    def _map_ratio(self, aspect: str) -> str:
        if not aspect:
            return "landscape"
        asp = str(aspect).lower()
        if asp in ("9:16", "3:4", "9:21", "portrait"):
            return "portrait"
        return "landscape"

    def submit_image(self, model, prompt, **params):
        """Submit image job using Flow Tool native mode (T2I / I2I) and quality parameters."""
        aspect = params.get("aspect_ratio") or params.get("aspect", "16:9")
        ratio = self._map_ratio(aspect)
        mode = params.get("image_mode") or params.get("mode")
        quality = params.get("quality", "fast")
        images = params.get("images") or params.get("image")

        if not mode:
            mode = "I2I" if images else "T2I"

        if images:
            if isinstance(images, str):
                images = [images]
            formatted_images = [flow_tool.upload_image(img) for img in images]
            return flow_tool.submit_job(mode, prompt, images=formatted_images, ratio=ratio, quality=quality)
        return flow_tool.submit_job(mode, prompt, ratio=ratio, quality=quality)

    def submit_video(self, model, prompt, **params):
        """Submit video job using Flow Tool native mode (R2V / I2V_S / T2V) and quality parameters."""
        aspect = params.get("aspect_ratio") or params.get("aspect") or params.get("ratio", "16:9")
        ratio = self._map_ratio(aspect)
        dur = int(params.get("duration", 8))
        safe_dur = dur if dur in (4, 6, 8, 10) else 8
        quality = params.get("quality", "lite_low_priority")

        # Resolve mode: default to native R2V when reference keyframe is supplied
        mode = params.get("video_mode") or params.get("mode")

        # A-roll Video Edit Mode
        video_src = params.get("video") or (params.get("reference_videos") or [None])[0]
        if video_src and os.path.exists(video_src):
            try:
                media_id, profile_id = flow_tool.upload_video(video_src)
                return flow_tool.submit_job(
                    "R2V",
                    prompt,
                    quality=quality,
                    reference_video_id=media_id,
                    profile_id=profile_id,
                    video_offset_end=safe_dur,
                )
            except Exception as e:
                print(f"[flow_tool] upload_video notice: {e} -> fallback")

        img_src = params.get("keyframe_path") or params.get("image") or params.get("keyframe_url")
        if not mode:
            mode = "R2V" if img_src else "T2V"

        if img_src and mode in ("R2V", "I2V_S", "I2V_SE"):
            return flow_tool.submit_job(
                mode,
                prompt,
                images=[img_src],
                quality=quality,
                ratio=ratio,
                duration=safe_dur,
            )

        return flow_tool.submit_job(
            mode,
            prompt,
            quality=quality,
            ratio=ratio,
            duration=safe_dur,
        )

    def submit_audio(self, model, **params):
        """Tác vụ Voice (TTS Standard, Multi-Speaker, Audio Tags) ưu tiên Gemini TTS làm mặc định, fallback sang OmniVoice Local GPU."""
        text = params.get("text", "")
        dest = params.get("dest") or params.get("output")
        if not dest:
            dest = os.path.join(tempfile.gettempdir(), f"audio_tts_{abs(hash(text))}.wav")

        voice_name = params.get("voice_name") or params.get("voice") or params.get("voice_id") or "Charon"
        speakers = params.get("speakers")

        # 1. Primary: Try Gemini TTS API if GEMINI_API_KEY is available
        if gemini_tts_tool.is_available():
            try:
                out_file = gemini_tts_tool.generate_speech(
                    prompt=text,
                    voice=voice_name,
                    speakers=speakers,
                    output=dest
                )
                return f"file:{out_file}"
            except Exception as e:
                err_str = str(e).encode("ascii", errors="replace").decode()
                print(f"[gemini_tts notice]: {err_str} -> fallback to OmniVoice")

        # 2. Fallback: OmniVoice Local GPU
        import re
        clean_text = re.sub(r'\[.*?\]', '', text).strip()
        language = params.get("language", "en")
        instruct = params.get("instruct") or params.get("voice_id")
        if instruct in ("leo", "standard", "default", "Charon", "Puck", "Fenrir"):
            instruct = "male, low pitch"
        elif instruct in ("Kore", "Aoede"):
            instruct = "female, middle-aged"
        elif "vietnamese" in str(instruct).lower():
            instruct = "male, low pitch"

        ref_audio = params.get("ref_audio") or params.get("clone_ref")
        ref_text = params.get("ref_text")
        speed = float(params.get("speed", 1.0))
        duration = params.get("duration")

        if omnivoice_tool.is_available():
            try:
                out_file = omnivoice_tool.generate_speech(
                    text=clean_text,
                    output_path=dest,
                    language=language,
                    instruct=instruct if not (ref_audio and os.path.exists(ref_audio)) else None,
                    ref_audio=ref_audio,
                    ref_text=ref_text,
                    duration=duration,
                    speed=speed,
                )
                return f"file:{out_file}"
            except Exception as e:
                err_str = str(e).encode("ascii", errors="replace").decode()
                print(f"[omnivoice notice]: {err_str}")

        # Fallback to Atlas Cloud if key is set
        if os.environ.get("ATLASCLOUD_API_KEY"):
            atlas_pid = AtlasCloudProvider().submit_audio(model, **params)
            return f"atlas:{atlas_pid}"

        return f"file:{dest}"

    def remove_bg(self, model, image_url, **params):
        if os.environ.get("ATLASCLOUD_API_KEY"):
            atlas_pid = AtlasCloudProvider().remove_bg(model, image_url, **params)
            return f"atlas:{atlas_pid}"
        return "file:local_matting"

    def get_status(self, job_id: str):
        if str(job_id).startswith("file:"):
            return {"status": "completed", "output": job_id[5:], "error": None}
        if str(job_id).startswith("atlas:"):
            return AtlasCloudProvider().get_status(job_id[6:])
        return flow_tool.get_job_status(job_id)

    def upload(self, path: str):
        if path.lower().endswith((".mp4", ".mov", ".avi", ".webm")):
            try:
                media_id, _ = flow_tool.upload_video(path)
                return media_id
            except Exception:
                return path
        return flow_tool.upload_image(path)

    def download(self, url: str, dest: str):
        if not url:
            return dest
        clean_url = url[5:] if url.startswith("file:") else url
        if clean_url.startswith("file:") or ((":\\" in clean_url or ":/" in clean_url) and not clean_url.startswith(("http://", "https://"))):
            abs_clean = os.path.abspath(clean_url)
            abs_dest = os.path.abspath(dest)
            if abs_clean != abs_dest and os.path.exists(abs_clean):
                os.makedirs(os.path.dirname(abs_dest), exist_ok=True)
                shutil.copyfile(abs_clean, abs_dest)
            return abs_dest
        if "atlas-media" in url or "aliyuncs.com" in url:
            return atlas_cloud.download(url, dest)
        return flow_tool.download(url, dest)


_REGISTRY = {
    "atlas_cloud": AtlasCloudProvider,
    "flow_tool": FlowToolProvider,
    "flow": FlowToolProvider,
}


def get_provider(name=None):
    """Return a Provider instance by name.
    Defaults to 'flow_tool' if available or specified in VOX_PROVIDER env, fallback 'atlas_cloud'.
    """
    default_name = os.environ.get("VOX_PROVIDER", "flow_tool")
    name = (name or default_name).lower()
    if name not in _REGISTRY:
        raise ProviderError(f"unknown provider '{name}'; available: {list(_REGISTRY)}")
    return _REGISTRY[name]()


def run_jobs(prov, specs, *, poll_s=3, stall_s=90, max_retries=2, deadline_s=900):
    """Submit + poll a batch of jobs, resubmitting any that FAIL or STALL."""
    st = {}
    for key, submit in specs.items():
        st[key] = {"pid": submit(), "t": time.time(), "tries": 0}
        print(f"[{key}] submitted {st[key]['pid']}")

    done = {}
    deadline = time.time() + deadline_s
    while len(done) < len(specs) and time.time() < deadline:
        time.sleep(poll_s)
        now = time.time()
        for key, submit in specs.items():
            if key in done:
                continue
            s = st[key]
            r = prov.get_status(s["pid"])
            status = r["status"]
            if status == "completed":
                done[key] = r["output"]
                print(f"[{key}] done")
            elif status == "failed" or (status == "pending" and now - s["t"] > stall_s):
                if s["tries"] < max_retries:
                    s["tries"] += 1
                    s["pid"] = submit()
                    s["t"] = time.time()
                    why = "failed" if status == "failed" else f"stalled>{int(stall_s)}s"
                    print(f"[{key}] {why} -> resubmit #{s['tries']} ({s['pid']})")
                elif status == "failed":
                    done[key] = None
                    print(f"[{key}] FAILED: {(r.get('error') or '')[:120]}")
    for key in specs:
        done.setdefault(key, None)
    return done
