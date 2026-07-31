#!/usr/bin/env python3
"""
OmniVoice Local client wrapper for vox-director.

Interfaces with the local OmniVoice model (installed at G:\\VS-Project\\OmniVoice)
for high-speed local TTS (Text-To-Speech) and Zero-shot Voice Cloning on GPU.
"""
import os
import subprocess
import sys
import tempfile

OMNIVOICE_DIR = os.path.abspath(r"G:\VS-Project\OmniVoice")
OMNIVOICE_PYTHON = os.path.join(OMNIVOICE_DIR, ".venv", "Scripts", "python.exe")
RUN_INFER_PY = os.path.join(OMNIVOICE_DIR, "run_infer.py")

LANG_MAP = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "vi": "Vietnamese",
}


class OmniVoiceError(RuntimeError):
    pass


def is_available() -> bool:
    """Check if local OmniVoice installation and python runner exist."""
    return os.path.exists(OMNIVOICE_PYTHON) and os.path.exists(RUN_INFER_PY)


def generate_speech(
    text: str,
    output_path: str,
    language: str = "en",
    instruct: str = None,
    ref_audio: str = None,
    ref_text: str = None,
    duration: float = None,
    speed: float = 1.0,
    num_step: int = 4,
) -> str:
    """Generate audio via local OmniVoice Python runner.

    Returns the absolute path to the generated output audio file.
    """
    if not is_available():
        raise OmniVoiceError(f"OmniVoice python runner not found at: {RUN_INFER_PY}")

    abs_output_path = os.path.abspath(output_path)
    os.makedirs(os.path.dirname(abs_output_path), exist_ok=True)
    lang_name = LANG_MAP.get(language.lower(), language)

    # Temporary wav output if requested dest is mp3
    target_ext = os.path.splitext(abs_output_path)[1].lower()
    wav_output = abs_output_path if target_ext == ".wav" else abs_output_path + ".tmp.wav"
    os.makedirs(os.path.dirname(wav_output), exist_ok=True)

    # Write text cleanly to temp file to avoid Windows command line character corruption
    tmp_txt = abs_output_path + ".prompt.txt"
    with open(tmp_txt, "w", encoding="utf-8") as f:
        f.write(text)

    cmd = [
        OMNIVOICE_PYTHON,
        RUN_INFER_PY,
        "--text_file", tmp_txt,
        "--output", wav_output,
        "--num_step", str(num_step),
    ]

    if lang_name:
        cmd.extend(["--language", lang_name])

    # 1. Voice Cloning Mode
    if ref_audio and os.path.exists(ref_audio):
        cmd.extend(["--ref_audio", os.path.abspath(ref_audio)])
        # Fast path: provide default ref_text if missing to avoid Whisper ASR model loading delay
        sample_text = ref_text or "Audio reference sample for zero shot voice cloning."
        cmd.extend(["--ref_text", sample_text])

    # 2. Voice Design Mode
    elif instruct:
        clean_instruct = instruct
        if "vietnamese" in str(instruct).lower():
            clean_instruct = "male, low pitch"
        cmd.extend(["--instruct", clean_instruct])

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"

    try:
        # DEVNULL prevents Windows subprocess pipe blocking on PyTorch tqdm output
        res = subprocess.run(
            cmd,
            cwd=OMNIVOICE_DIR,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=True
        )
    except subprocess.CalledProcessError as e:
        raise OmniVoiceError(f"OmniVoice python runner execution failed: {e}") from e
    finally:
        if os.path.exists(tmp_txt):
            try:
                os.remove(tmp_txt)
            except Exception:
                pass

    if not os.path.exists(wav_output) or os.path.getsize(wav_output) == 0:
        raise OmniVoiceError(f"OmniVoice generated empty output file for: {text}")

    # Convert wav to mp3 if requested
    if target_ext == ".mp3":
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", wav_output, "-c:a", "libmp3lame", abs_output_path],
            check=True
        )
        if os.path.exists(wav_output):
            os.remove(wav_output)
        return abs_output_path

    return wav_output


if __name__ == "__main__":
    if len(sys.argv) > 2:
        txt, out = sys.argv[1], sys.argv[2]
        generate_speech(txt, out)
        print("Generated:", out)
    else:
        print("OmniVoice local helper ready. Available:", is_available())
