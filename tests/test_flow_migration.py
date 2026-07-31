#!/usr/bin/env python3
"""
Unit and integration tests for Flow Tool migration and Provider routing in vox-director.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add scripts directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))

import flow_tool
import provider


class TestFlowToolClient(unittest.TestCase):

    def test_upload_image_url_and_b64(self):
        url = "https://example.com/test.jpg"
        self.assertEqual(flow_tool.upload_image(url), url)

        data_uri = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        self.assertEqual(flow_tool.upload_image(data_uri), data_uri)

    def test_get_job_status_parsing(self):
        with patch("flow_tool._get") as mock_get:
            # Test QUEUED -> pending
            mock_get.return_value = {"status": "QUEUED", "progress": 0}
            res = flow_tool.get_job_status("job123")
            self.assertEqual(res["status"], "pending")
            self.assertIsNone(res["output"])

            # Test PROCESSING -> pending
            mock_get.return_value = {"status": "PROCESSING", "progress": 50}
            res = flow_tool.get_job_status("job123")
            self.assertEqual(res["status"], "pending")

            # Test SUCCESS -> completed
            mock_get.return_value = {
                "status": "SUCCESS",
                "progress": 100,
                "result_urls": ["https://storage.googleapis.com/out.mp4"]
            }
            res = flow_tool.get_job_status("job123")
            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["output"], "https://storage.googleapis.com/out.mp4")

            # Test FAILED -> failed
            mock_get.return_value = {
                "status": "FAILED",
                "error": "Google Content Policy violation"
            }
            res = flow_tool.get_job_status("job123")
            self.assertEqual(res["status"], "failed")
            self.assertIn("Content Policy", res["error"])


class TestFlowToolProvider(unittest.TestCase):

    def setUp(self):
        self.prov = provider.FlowToolProvider()

    def test_provider_registry(self):
        p_flow = provider.get_provider("flow_tool")
        self.assertIsInstance(p_flow, provider.FlowToolProvider)

        p_atlas = provider.get_provider("atlas_cloud")
        self.assertIsInstance(p_atlas, provider.AtlasCloudProvider)

    @patch("flow_tool.submit_job")
    def test_submit_image_t2i(self, mock_submit):
        mock_submit.return_value = "flow_job_1"
        job_id = self.prov.submit_image("google/nano-banana-2/text-to-image", "a red seal", aspect_ratio="16:9")
        self.assertEqual(job_id, "flow_job_1")
        mock_submit.assert_called_with("T2I", "a red seal", ratio="landscape", quality="fast")

    @patch("flow_tool.submit_job")
    def test_submit_image_i2i(self, mock_submit):
        mock_submit.return_value = "flow_job_2"
        job_id = self.prov.submit_image("google/nano-banana-2/edit", "make it vintage", images=["https://ex.com/a.png"], aspect="9:16")
        self.assertEqual(job_id, "flow_job_2")
        mock_submit.assert_called_with("I2I", "make it vintage", images=["https://ex.com/a.png"], ratio="portrait", quality="fast")

    @patch("flow_tool.submit_job")
    def test_submit_video_always_uses_lite_low_priority(self, mock_submit):
        mock_submit.return_value = "flow_job_3"
        # Test I2V_S
        job_id = self.prov.submit_video("google/gemini-omni-flash/image-to-video", "animate paper", image="https://ex.com/poster.jpg", duration=8, aspect_ratio="16:9")
        self.assertEqual(job_id, "flow_job_3")
        mock_submit.assert_called_with("R2V", "animate paper", quality="lite_low_priority", images=["https://ex.com/poster.jpg"], ratio="landscape", duration=8)

        # Test T2V
        job_id_t2v = self.prov.submit_video("google/gemini-omni-flash/text-to-video", "flying cloud", aspect_ratio="9:16")
        mock_submit.assert_called_with("T2V", "flying cloud", quality="lite_low_priority", ratio="portrait", duration=8)

    @patch("gemini_tts_tool.generate_speech")
    def test_submit_audio_delegation(self, mock_gen_speech):
        mock_gen_speech.return_value = r"C:\tmp\audio.wav"
        job_id = self.prov.submit_audio("xai/tts-v1", text="Hello world", language="en")
        self.assertEqual(job_id, r"file:C:\tmp\audio.wav")

    @patch("flow_tool.get_job_status")
    @patch("atlas_cloud._get")
    def test_get_status_routing(self, mock_atlas_get, mock_flow_get):
        # Flow job routing
        mock_flow_get.return_value = {"status": "completed", "output": "http://flow.out/v.mp4", "error": None}
        st_flow = self.prov.get_status("flow_job_123")
        self.assertEqual(st_flow["status"], "completed")

        # Atlas delegated job routing
        mock_atlas_get.return_value = {"data": {"status": "completed", "outputs": ["http://atlas.out/a.mp3"]}}
        st_atlas = self.prov.get_status("atlas:atlas_job_456")
        self.assertEqual(st_atlas["status"], "completed")
        self.assertEqual(st_atlas["output"], "http://atlas.out/a.mp3")


if __name__ == "__main__":
    unittest.main()
