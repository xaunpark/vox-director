#!/usr/bin/env python3
"""
Unit tests for Gemini TTS Migration & Fallback Routing.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import gemini_tts_tool
import omnivoice_tool
import provider


class TestGeminiTTSMigration(unittest.TestCase):

    @patch("gemini_tts_tool.load_env")
    def test_gemini_tts_availability(self, mock_load_env):
        """Verify availability logic when GEMINI_API_KEY is present or missing."""
        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey"}, clear=True):
            self.assertTrue(gemini_tts_tool.is_available())

        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(gemini_tts_tool.is_available())

    @patch("gemini_tts_tool.generate_speech")
    def test_provider_routes_to_gemini_tts(self, mock_gemini_speech):
        """Verify FlowToolProvider routes submit_audio to Gemini TTS when GEMINI_API_KEY is present."""
        mock_gemini_speech.return_value = os.path.abspath("out/test_gemini.wav")
        prov = provider.get_provider("flow_tool")

        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey"}, clear=True):
            job_id = prov.submit_audio("gemini/tts", text="Hello Gemini", voice="Charon")
            self.assertTrue(job_id.startswith("file:"), f"Expected job_id starting with file:, got {job_id}")
            mock_gemini_speech.assert_called_once()

    @patch("gemini_tts_tool.generate_speech")
    @patch("omnivoice_tool.generate_speech")
    def test_provider_fallback_to_omnivoice(self, mock_omni_speech, mock_gemini_speech):
        """Verify FlowToolProvider falls back to OmniVoice when Gemini TTS fails."""
        mock_gemini_speech.side_effect = RuntimeError("API rate limit exceeded")
        mock_omni_speech.return_value = os.path.abspath("out/test_omni.mp3")
        prov = provider.get_provider("flow_tool")

        with patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey"}, clear=True):
            job_id = prov.submit_audio("gemini/tts", text="Hello Fallback", voice="Charon")
            self.assertTrue(job_id.startswith("file:"), f"Expected job_id starting with file:, got {job_id}")
            mock_omni_speech.assert_called_once()


if __name__ == "__main__":
    unittest.main()
