import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from local_voice_studio.core import Config, StudioError
from local_voice_studio.product import produce_video, read_subtitles, srt_text, probe_input


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.video = self.root / "input.mp4"
        self.video.write_bytes(b"test video")
        self.config = self.root / "config.toml"
        self.config.write_text("[voice]\nengine='kokoro'\n")
        self.srt = self.root / "captions.srt"
        self.srt.write_text("1\n00:00:00,250 --> 00:00:03,000\nHello world.\n")
        self.output = self.root / "run"

    def planned(self, **kwargs):
        with patch("local_voice_studio.product.probe_input", return_value={"duration": 5., "width": 360, "height": 640, "fps": 30.}):
            return produce_video(self.video, self.config, self.output, **kwargs)

    def test_video_needs_exactly_one_source(self):
        for kwargs in ({}, {"narration": "Hello", "subtitles": self.srt}):
            with self.assertRaisesRegex(StudioError, "exactly one"):
                self.planned(**kwargs)
        self.assertFalse(self.output.exists())

    def test_dry_run_validates_inputs_without_reserving_output_or_synthesizing(self):
        with patch("local_voice_studio.providers.create_backend", side_effect=AssertionError("Must not synthesize")):
            for source in ({"narration": "A literal script"}, {"subtitles": self.srt}):
                result = self.planned(**source, dry_run=True)
                self.assertEqual(result["status"], "planned")
                self.assertFalse(result["summary"]["voice_generated"])
                self.assertEqual(result["outputs"], {})
        self.assertFalse(self.output.exists())

    def test_existing_run_is_never_overwritten(self):
        self.output.mkdir()
        existing = self.output / "video.mp4"
        existing.write_bytes(b"keep me")
        with self.assertRaisesRegex(StudioError, "not empty"):
            self.planned(narration="Hello")
        self.assertEqual(existing.read_bytes(), b"keep me")

    def test_reference_sample_rejected_by_incapable_provider_without_model_call(self):
        reference = self.root / "sample.wav"
        reference.write_bytes(b"sample")
        with patch("local_voice_studio.providers.create_backend", side_effect=AssertionError("Must reject before model")):
            with self.assertRaisesRegex(StudioError, "cloning"):
                self.planned(narration="Hello", reference_audio=reference, reference_text="Sample")
        self.assertFalse(self.output.exists())

    def test_transcript_without_sample_is_rejected(self):
        with self.assertRaisesRegex(StudioError, "reference audio"):
            self.planned(narration="Hello", reference_text="Sample")

    def test_backend_failure_retains_actionable_receipt_and_leaves_input_unchanged(self):
        with patch("local_voice_studio.providers.create_backend", side_effect=StudioError("model not installed")):
            with self.assertRaisesRegex(StudioError, "model not installed"):
                self.planned(narration="Hello")
        receipt = json.loads((self.output / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("model not installed", receipt["error"])
        self.assertEqual(self.video.read_bytes(), b"test video")
        self.assertFalse((self.output / "result.json").exists())

    def test_webvtt_and_srt_have_identical_spoken_text_and_cue_windows(self):
        vtt = self.root / "captions.vtt"
        vtt.write_text("WEBVTT\n\noptional-id\n00:00.250 --> 00:03.000\nHello world.\n")
        first, second = read_subtitles(self.srt, 5), read_subtitles(vtt, 5)
        self.assertEqual([(x.start,x.end,x.text) for x in first], [(x.start,x.end,x.text) for x in second])
        self.assertEqual(srt_text(first), self.srt.read_text())

    def test_subtitle_overlaps_out_of_video_and_markup_fail_before_synthesis(self):
        cases = [
            "WEBVTT\n\n00:00.000 --> 00:06.000\nToo long.\n",
            "WEBVTT\n\n00:00.000 --> 00:03.000\nFirst.\n\n00:02.000 --> 00:04.000\nOverlap.\n",
            "WEBVTT\n\n00:00.000 --> 00:03.000 align:start\nPositioned.\n",
            "WEBVTT\n\n00:00.000 --> 00:03.000\n<v speaker>Tagged.</v>\n",
            "WEBVTT\n\nSTYLE\n::cue { color: red; }\n",
        ]
        for text in cases:
            path = self.root / "bad.vtt"
            path.write_text(text)
            with self.subTest(text=text), self.assertRaises(StudioError):
                self.planned(subtitles=path)
        self.assertFalse(self.output.exists())

    def test_probe_accounts_for_phone_rotation(self):
        response = json.dumps({"streams":[{"width":640,"height":360,"duration":"5","r_frame_rate":"30/1","side_data_list":[{"rotation":90}]}],"format":{"duration":"5"}})
        with patch("local_voice_studio.product.executable", return_value="ffprobe"), patch("local_voice_studio.product.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = response
            self.assertEqual(probe_input(self.video, Config()), {"duration":5.,"fps":30.,"width":360,"height":640})

    def test_non_utf8_subtitles_return_actionable_input_error_without_reserving_run(self):
        self.srt.write_bytes(b"\xff\xfe not UTF-8")
        with self.assertRaisesRegex(StudioError,"UTF-8"):
            self.planned(subtitles=self.srt)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
