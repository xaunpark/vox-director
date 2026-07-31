#!/usr/bin/env python3
"""
Gemini Audio Multimodal Alignment Tool
Uses Gemini Flash Multimodal API to align master narration audio with script sentences,
returning exact start_sec and end_sec timestamps for each beat.
"""

import base64
import json
import os
import urllib.error
import urllib.request


def load_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        env_file = os.path.join(os.path.dirname(__file__), "..", ".env")
        if os.path.exists(env_file):
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("GEMINI_API_KEY="):
                        key = line.strip().split("=", 1)[1]
                        break
    return key


def align_audio_sentences(audio_path: str, sentences: list):
    """
    Given an audio file path (mp3/wav) and an ordered list of sentences,
    returns a list of dicts with [{'shot_index': 0, 'start_sec': 0.0, 'end_sec': 5.2, 'dur_sec': 5.2}, ...]
    """
    api_key = load_api_key()
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment or .env file.")

    with open(audio_path, "rb") as f:
        audio_bytes = f.read()

    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    mime_type = "audio/mp3" if audio_path.endswith(".mp3") else "audio/wav"

    sentences_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(sentences)])

    prompt = f"""You are a professional audio alignment engineer.
Listen to the attached master narration audio and align it with these exact {len(sentences)} ordered sentences:

{sentences_text}

Analyze the audio waveform and return a strict JSON array containing exact start and end timestamps in seconds (float numbers rounded to 2 decimal places) for each sentence in order.

Return ONLY a JSON array with format:
[
  {{"shot_index": 0, "start_sec": 0.0, "end_sec": 4.15, "dur_sec": 4.15}},
  {{"shot_index": 1, "start_sec": 4.15, "end_sec": 11.20, "dur_sec": 7.05}}
]
"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": b64_audio
                        }
                    },
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.0,
            "response_mime_type": "application/json"
        }
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    try:
        resp = urllib.request.urlopen(req)
        res_json = json.loads(resp.read().decode("utf-8"))
        candidates = res_json.get("candidates", [])
        if not candidates:
            raise ValueError("No candidates returned from Gemini Alignment API.")
        
        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise ValueError("No content parts returned from Gemini Alignment API.")

        json_str = parts[0].get("text", "").strip()
        alignments = json.loads(json_str)
        return alignments

    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        raise RuntimeError(f"Gemini Alignment HTTP Error {e.code}: {err_body}")
    except Exception as e:
        raise RuntimeError(f"Gemini Alignment Failed: {e}")


if __name__ == "__main__":
    test_audio = r"g:\VS-Project\Vox-Director\out\vietnam-rise-opening\audio\master_narration.mp3"
    test_sentences = [
        "Decades ago, it was a nation defined by war and struggle.",
        "Today, Vietnam is emerging as the unstoppable economic tiger of Southeast Asia.",
        "High-tech megafactories and semiconductor supply chains are flocking to its shores,",
        "driving billions of dollars in foreign investment into a massive industrial boom.",
        "How did a nation rebuild itself from the ground up to challenge Asia's giants?",
        "This is the story of Vietnam's meteoric rise to power."
    ]
    if os.path.exists(test_audio):
        res = align_audio_sentences(test_audio, test_sentences)
        print("Gemini Audio Alignment Result:")
        print(json.dumps(res, indent=2))
