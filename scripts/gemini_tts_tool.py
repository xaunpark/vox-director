#!/usr/bin/env python3
"""
Google Gemini TTS Integration Tool (gemini-3.1-flash-tts-preview).
Supports Single-Speaker, Multi-Speaker, Audio Tags, Auto-Retry 3x, and 44.1kHz Audio Resampling.
"""
import os
import sys
import json
import time
import base64
import wave
import subprocess
import urllib.request
import urllib.error

# Ensure UTF-8 console output for Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def load_env():
    env_file = r"g:\VS-Project\Vox-Director\.env"
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip().strip('"').strip("'")

def is_available():
    load_env()
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    return bool(key)

def extract_audio_base64(data):
    if isinstance(data, dict):
        if "output_audio" in data and isinstance(data["output_audio"], dict) and "data" in data["output_audio"]:
            return data["output_audio"]["data"]
        if "steps" in data and isinstance(data["steps"], list):
            for step in data["steps"]:
                if "content" in step and isinstance(step["content"], list):
                    for item in step["content"]:
                        if isinstance(item, dict) and "data" in item:
                            return item["data"]
    return None

def pcm_to_wav(pcm_bytes, wav_path, sample_rate=24000, channels=1, sample_width=2):
    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)
    raw_tmp = wav_path + ".raw.wav"
    with wave.open(raw_tmp, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)

    # Resample to 44.1kHz Stereo WAV via ffmpeg for perfect assemble.py compatibility
    try:
        cmd = ["ffmpeg", "-y", "-i", raw_tmp, "-ar", "44100", "-ac", "2", wav_path]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if os.path.exists(raw_tmp):
            os.remove(raw_tmp)
    except Exception:
        # Fallback if ffmpeg is missing
        if os.path.exists(raw_tmp):
            if os.path.exists(wav_path):
                os.remove(wav_path)
            os.rename(raw_tmp, wav_path)

def generate_speech(prompt, voice="Charon", speakers=None, output=None, model="gemini-3.1-flash-tts-preview", max_retries=3):
    load_env()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable is missing!")

    url = f"https://generativelanguage.googleapis.com/v1beta/interactions?key={api_key}"

    if speakers:
        speech_config = [{"speaker": sp["name"], "voice": sp.get("voice", "Charon")} for sp in speakers]
    else:
        speech_config = [{"voice": voice}]

    payload = {
        "model": model,
        "input": prompt,
        "response_format": {"type": "audio"},
        "generation_config": {
            "speech_config": speech_config
        }
    }

    if not output:
        out_dir = r"g:\VS-Project\Vox-Director\outudio"
        os.makedirs(out_dir, exist_ok=True)
        output = os.path.join(out_dir, f"gemini_tts_{int(time.time()*1000)}.wav")

    req_data = json.dumps(payload).encode("utf-8")

    for attempt in range(1, max_retries + 1):
        try:
            req = urllib.request.Request(url, data=req_data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))

            audio_b64 = extract_audio_base64(res_data)
            if not audio_b64:
                raise ValueError(f"No audio data payload in response: {res_data}")

            pcm_bytes = base64.b64decode(audio_b64)
            pcm_to_wav(pcm_bytes, output)
            return output

        except (urllib.error.HTTPError, urllib.error.URLError, ValueError) as e:
            print(f"[gemini_tts] Attempt {attempt}/{max_retries} failed: {e}")
            if attempt == max_retries:
                raise e
            time.sleep(attempt * 2)

    return output

if __name__ == "__main__":
    if is_available():
        res = generate_speech("[excitedly] Test Gemini TTS tool!", voice="Charon")
        print(f"Generated test audio: {res}")
    else:
        print("GEMINI_API_KEY is not available.")
