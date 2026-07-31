#!/usr/bin/env python3
"""
Audio stage: per-beat narration (one consistent voice) + one instrumental BGM.

Narration uses OmniVoice Local GPU (installed at G:\\VS-Project\\OmniVoice) via Provider,
supporting both Standard/Design TTS and Zero-shot Voice Cloning natively on local GPU.
BGM uses Atlas Cloud when ATLASCLOUD_API_KEY is present, or ffmpeg acoustic synthesizer.

Usage: python3 audio.py <project_dir>
"""
import base64
import json
import os
import subprocess
import sys

import omnivoice_tool
from provider import get_provider, run_jobs

VOICE_MODEL = "omnivoice/tts"
CLONE_MODEL = "omnivoice/voice-cloning"
MUSIC_MODEL = "minimax/music-2.6"


def probe_dur(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def generate_fallback_narration(text: str, dest: str, language: str = "en"):
    """Fallback TTS via gTTS if OmniVoice local execution encounters an issue."""
    try:
        from gtts import gTTS
        tts = gTTS(text=text, lang=language)
        tts.save(dest)
    except Exception as e:
        print(f"[gtts fallback error]: {e}")


def generate_fallback_bgm(dest: str, duration: float = 25.0):
    """Fallback warm BGM acoustic chord track via ffmpeg."""
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"sine=frequency=220:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=330:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-filter_complex", "[0:a][1:a][2:a]amerge=inputs=3,volume=0.15,afade=t=in:st=0:d=1,afade=t=out:st=18:d=2[a]",
        "-map", "[a]", "-c:a", "libmp3lame", dest
    ], check=True)


def generate_silent_audio(dest: str, duration: float = 3.0):
    """Generate a silent audio file for beats without narration."""
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    try:
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-t", str(duration),
            "-c:a", "libmp3lame", dest
        ], check=True)
    except Exception:
        with wave.open(dest, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b"\x00" * int(44100 * 2 * 2 * duration))


def parse_silence_timestamps(wav_path: str, expected_count: int):
    """Use ffmpeg silencedetect to split a master narration wav into sentence timestamps."""
    cmd = [
        "ffmpeg", "-y", "-i", wav_path,
        "-af", "silencedetect=noise=-30dB:d=0.25",
        "-f", "null", "-"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    stderr = res.stderr
    
    silence_starts = []
    silence_ends = []
    for line in stderr.splitlines():
        if "silence_start:" in line:
            try:
                silence_starts.append(float(line.split("silence_start:")[1].split()[0]))
            except ValueError:
                pass
        elif "silence_end:" in line:
            try:
                silence_ends.append(float(line.split("silence_end:")[1].split()[0]))
            except ValueError:
                pass

    total_dur = probe_dur(wav_path)
    # Build sentence intervals based on silence pauses
    pauses = [0.0]
    for s_start, s_end in zip(silence_starts, silence_ends):
        # Pause midpoint
        pauses.append(round((s_start + s_end) / 2.0, 2))
    pauses.append(round(total_dur, 2))
    
    # Filter out pauses that are too close (e.g. < 1.0s apart)
    filtered = [pauses[0]]
    for p in pauses[1:]:
        if p - filtered[-1] >= 1.2 or p == pauses[-1]:
            filtered.append(p)
    pauses = filtered

    # If pause count matches expected_count + 1
    if len(pauses) == expected_count + 1:
        spans = []
        for i in range(expected_count):
            spans.append((pauses[i], pauses[i+1], round(pauses[i+1] - pauses[i], 2)))
        return spans

    # Fallback proportion-based spans if silence count differs
    spans = []
    dur_per_beat = total_dur / max(1, expected_count)
    t = 0.0
    for i in range(expected_count):
        nxt = round(t + dur_per_beat, 2)
        spans.append((round(t, 2), nxt, round(dur_per_beat, 2)))
        t = nxt
    return spans


def run(project_dir: str):
    bpath = os.path.join(project_dir, "beats.json")
    with open(bpath, encoding="utf-8") as f:
        doc = json.load(f)
    adir = os.path.join(project_dir, "audio")
    os.makedirs(adir, exist_ok=True)

    prov = get_provider(doc.get("provider"))
    voice = doc.get("voice", {})
    voice_id = voice.get("voice_id", "Charon")
    language = voice.get("language", doc.get("language", "en"))
    v_provider = voice.get("provider", "gemini_tts")

    # Single-pass Master Narration logic for Gemini TTS
    import gemini_tts_tool
    if v_provider == "gemini_tts" and gemini_tts_tool.is_available():
        print("[audio] Single-Pass Master Narration Mode via Gemini TTS...")
        narr_texts = [b.get("narration", "").strip() for b in doc["beats"] if b.get("narration", "").strip()]
        combined_text = " ".join(narr_texts)
        v_name = voice.get("voice_name", "Charon")
        master_wav = os.path.join(adir, "master_narration.wav")
        master_mp3 = os.path.join(adir, "master_narration.mp3")
        
        try:
            gemini_tts_tool.generate_speech(combined_text, voice=v_name, output=master_wav)
            # Convert WAV to MP3 for final mix
            subprocess.run([
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", master_wav, "-c:a", "libmp3lame", master_mp3
            ], check=True)
            
            doc["master_narration_path"] = master_mp3
            total_master_dur = probe_dur(master_mp3)
            doc["master_narration_dur"] = round(total_master_dur, 2)
            
            # Multimodal Timestamp alignment via Gemini Audio Alignment API
            import gemini_align_tool
            try:
                alignments = gemini_align_tool.align_audio_sentences(master_mp3, narr_texts)
                for beat, align in zip(doc["beats"], alignments):
                    st = round(float(align.get("start_sec", 0.0)), 2)
                    en = round(float(align.get("end_sec", st + 5.0)), 2)
                    dur = round(float(align.get("dur_sec", en - st)), 2)
                    dest_path = os.path.join(adir, f"narr_{beat['id']}.mp3")
                    
                    subprocess.run([
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-ss", str(st), "-to", str(en),
                        "-i", master_mp3, "-c:a", "libmp3lame", dest_path
                    ], check=True)
                    beat["narration_audio"] = dest_path
                    beat["audio_start"] = st
                    beat["audio_end"] = en
                    beat["narration_dur"] = dur
                    print(f"[narr {beat['id']}] Gemini Aligned [{st}s -> {en}s] ({dur}s) -> {dest_path}")
            except Exception as align_err:
                print(f"[audio] Gemini alignment failed: {align_err} -> fallback to silence detection")
                spans = parse_silence_timestamps(master_wav, len(doc["beats"]))
                for beat, (st, en, dur) in zip(doc["beats"], spans):
                    dest_path = os.path.join(adir, f"narr_{beat['id']}.mp3")
                    subprocess.run([
                        "ffmpeg", "-y", "-loglevel", "error",
                        "-ss", str(st), "-to", str(en),
                        "-i", master_mp3, "-c:a", "libmp3lame", dest_path
                    ], check=True)
                    beat["narration_audio"] = dest_path
                    beat["audio_start"] = st
                    beat["audio_end"] = en
                    beat["narration_dur"] = dur
                    print(f"[narr {beat['id']}] Silence Aligned [{st}s -> {en}s] ({dur}s) -> {dest_path}")
        except Exception as e:
            print(f"[audio] Single-pass master narration error: {e} -> fallback to per-beat audio")

    # Fallback to per-beat audio generation if master audio not present
    if not doc.get("master_narration_path"):
        specs = {}
        for beat in doc["beats"]:
            dest_path = os.path.join(adir, f"narr_{beat['id']}.mp3")
            narr_text = beat.get("narration", "").strip()
            if not narr_text:
                generate_silent_audio(dest_path, duration=float(beat.get("duration_sec", 3)))
                continue
            v_name = beat.get("voice_name") or voice.get("voice_name") or voice_id
            b_speakers = beat.get("speakers") or voice.get("speakers")
            specs[f"narr_{beat['id']}"] = (lambda t=beat["narration"], d=dest_path, vn=v_name, spk=b_speakers: prov.submit_audio(
                VOICE_MODEL, text=t, dest=d, language=language, voice_name=vn, speakers=spk
            ))
        
        done = run_jobs(prov, specs, poll_s=2, stall_s=120, max_retries=1, deadline_s=300)
        for beat in doc["beats"]:
            dest = os.path.join(adir, f"narr_{beat['id']}.mp3")
            if not os.path.exists(dest) or os.path.getsize(dest) == 0:
                generate_fallback_narration(beat["narration"], dest, language=language)
            beat["narration_audio"] = dest
            beat["narration_dur"] = round(probe_dur(dest), 2)
            print(f"[narr {beat['id']}] {beat['narration_dur']}s -> {dest}")

    # BGM handling
    bgm_path = os.path.join(adir, "bgm.mp3")
    if not os.path.exists(bgm_path):
        generate_fallback_bgm(bgm_path, duration=float(doc.get("master_narration_dur", 30.0)) + 5.0)

    if os.path.exists(bgm_path):
        doc["bgm_path"] = bgm_path
        doc["bgm_dur"] = round(probe_dur(bgm_path), 2)
        print(f"[bgm] {doc['bgm_dur']}s -> {bgm_path}")

    with open(bpath, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)
    print("updated", bpath)


if __name__ == "__main__":
    proj = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "out", "tang-30s")
    run(os.path.abspath(proj))

