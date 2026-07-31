#!/usr/bin/env python3
"""
Unit and integration tests for OmniVoice Local GPU Voice Migration.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import omnivoice_tool
import provider


class TestOmniVoiceMigration(unittest.TestCase):

    def test_omnivoice_availability(self):
        """Verify that local OmniVoice installation and python/infer executables exist."""
        self.assertTrue(omnivoice_tool.is_available(), "OmniVoice local installation at G:\\VS-Project\\OmniVoice must be available")

    @patch("omnivoice_tool.generate_speech")
    def test_provider_audio_routing(self, mock_gen_speech):
        """Verify that FlowToolProvider routes submit_audio to OmniVoice Local without needing Atlas API Key."""
        mock_gen_speech.return_value = os.path.abspath("out/test_unit_omnivoice.mp3")
        prov = provider.get_provider("flow_tool")

        output_file = os.path.abspath("out/test_unit_omnivoice.mp3")
        with open(output_file, "w") as f:
            f.write("mock_audio_content")

        job_id = prov.submit_audio("omnivoice/tts", text="Unit test for OmniVoice migration.", dest=output_file, language="en")
        self.assertTrue(job_id.startswith("file:"), f"Job ID should start with file: prefix, got {job_id}")

        st = prov.get_status(job_id)
        self.assertEqual(st["status"], "completed")
        self.assertTrue(os.path.exists(st["output"]))
        self.assertGreater(os.path.getsize(st["output"]), 0)


if __name__ == "__main__":
    unittest.main()
