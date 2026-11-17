"""Stdlib contract/timing tests. Fake samples are test-only, never delivered as voice."""
from array import array
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from local_voice_studio.core import (Clip, Config, StudioError, TimingOverflow, assemble_track,
    build_plan, environment_status, fit_clip, load_config, pronounced, read_srt,
    verify_plan_unchanged, write_wav)


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="voice-studio-test-")
        self.root = Path(self.tmp.name)
        self.video = self.root / "clip.mov"
        self.video.write_bytes(b"contract fixture, not an encoded video")
        self.segments = self.root / "segments.json"
        self.segments.write_text(json.dumps({"schema_version":1,"duration":4,"segments":[
            {"id":"S01","start":0,"duration":2,"text":"One clear example."},
            {"id":"S02","start":2,"duration":2,"text":"Then check the answer."}]}))
        self.captions = self.root / "captions.srt"
        self.captions.write_text("1\n00:00:00,200 --> 00:00:01,800\nOne clear example.\n\n2\n00:00:02,200 --> 00:00:03,800\nThen check the answer.\n")
        self.timeline = self.root / "timeline.json"
        self.timeline.write_text(json.dumps({"scenes":[
            {"duration":2,"narration":"One clear example."},
            {"duration":2,"narration":"Then check the answer."}]}))
        self.sidecar = self.root / "clip.voice.json"
        self.data = {"schema_version":1,"video":{"path":"clip.mov","sha256":hashlib.sha256(self.video.read_bytes()).hexdigest(),"duration":4,"width":1080,"height":1920,"fps":30},
                     "narration":{"segments_path":"segments.json","captions_srt_path":"captions.srt","timeline_path":"timeline.json"},
                     "target":{"platform":"youtube","format":"shorts","primary_device":"phone","max_duration_seconds":180}}
        self.save_sidecar()
        self.config=Config()

    def tearDown(self):
        self.tmp.cleanup()

    def save_sidecar(self):
        self.sidecar.write_text(json.dumps(self.data))

    def test_auto_discovery_and_duration_segments_keep_all_words(self):
        plan=build_plan(self.video,self.config)
        self.assertEqual(plan.sidecar,self.sidecar.resolve())
        self.assertEqual([c.text for c in plan.clips],["One clear example.","Then check the answer."])
        self.assertEqual(plan.duration,4)

    def test_stale_video_rejected(self):
        self.video.write_bytes(b"different edit")
        with self.assertRaisesRegex(StudioError,"SHA-256 mismatch"):
            build_plan(self.video,self.config)

    def test_missing_sidecar_does_not_guess_script(self):
        self.sidecar.unlink()
        with self.assertRaisesRegex(StudioError,"does not guess"):
            build_plan(self.video,self.config)

    def test_captions_cannot_drop_words(self):
        self.captions.write_text(self.captions.read_text().replace("Then check the answer.","Then the answer."))
        with self.assertRaisesRegex(StudioError,"does not match"):
            build_plan(self.video,self.config)

    def test_overlap_and_scene_crossing_rejected(self):
        self.captions.write_text(self.captions.read_text().replace("00:00:02,200","00:00:01,700"))
        with self.assertRaisesRegex(StudioError,"overlapping"):
            build_plan(self.video,self.config)

    def test_changed_timeline_after_plan_is_rejected(self):
        plan=build_plan(self.video,self.config)
        self.timeline.write_text("{}")
        with self.assertRaisesRegex(StudioError,"changed after planning"):
            verify_plan_unchanged(plan)

    def test_scene_mode_reports_caption_recheck(self):
        plan=build_plan(self.video,replace(self.config,fit_mode="scene"))
        self.assertEqual(plan.to_dict()["caption_alignment"],"requires_recheck")

    def test_timeline_narration_mismatch_rejected(self):
        self.timeline.write_text(self.timeline.read_text().replace("One clear example.","Different message."))
        with self.assertRaisesRegex(StudioError,"Timeline and voice-segments differ"):
            build_plan(self.video,self.config)

    def test_plan_cli_has_no_model_dependency_or_import(self):
        result=subprocess.run([sys.executable,"-m","local_voice_studio","plan","--video",str(self.video)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        output=json.loads(result.stdout)
        self.assertFalse(output["voice_generated"])
        self.assertEqual(len(output["clips"]),2)

    def test_timing_assembly_preserves_absolute_offsets_and_length(self):
        plan=build_plan(self.video,self.config)
        calls=[]
        def fake(text,speed):
            calls.append(text)
            return array("f",[.1,-.1])*1200
        track,clips,gain=assemble_track(plan,self.config,fake)
        self.assertEqual(calls,[c.text for c in plan.clips])
        self.assertEqual(len(track),4*24000)
        self.assertEqual(track[0],0)
        self.assertAlmostEqual(track[round(.2*24000)],.1,places=5)
        self.assertEqual(track[round(2*24000)],0)
        self.assertAlmostEqual(track[round(2.2*24000)],.1,places=5)
        self.assertEqual(len(clips),2)

    def test_fit_re_synthesizes_complete_text_with_bounded_speed(self):
        calls=[]
        def fake(text,speed):
            calls.append((text,speed))
            return array("f",[.1,-.1])*round((1.02/speed)*24000/2)
        config=replace(self.config,speed=.95,max_speed=1.15)
        audio,receipt=fit_clip(Clip("S",0,1.1,"All words remain here."),config,fake)
        self.assertGreater(len(calls),1)
        self.assertTrue(all(text=="All words remain here." for text,_ in calls))
        self.assertLessEqual(max(speed for _,speed in calls),1.15)
        self.assertLessEqual(len(audio),int(1.06*24000))

    def test_overflow_raises_retime_plan_without_cutting_samples(self):
        original=array("f",[.1,-.1])*24000
        calls=[]
        def fake(text,speed):
            calls.append(text);return original
        with self.assertRaises(TimingOverflow) as error:
            fit_clip(Clip("S",0,1,"Every word must remain."),self.config,fake)
        self.assertEqual(error.exception.details["text"],"Every word must remain.")
        self.assertEqual(len(original),48000)
        self.assertTrue(all(t=="Every word must remain." for t in calls))
        self.assertIn("never_truncate",error.exception.details["action"])

    def test_silent_and_nonfinite_synthesis_rejected(self):
        for samples in [array("f",[0])*2400,array("f",[float("nan")])*2400]:
            with self.assertRaises(StudioError):
                fit_clip(Clip("S",0,1,"Example text."),self.config,lambda text,speed:samples)

    def test_wav_normalization_prevents_pcm_clipping(self):
        path=self.root/"samples.wav"
        write_wav(path,array("f",[2,-2])*2400,24000,.95)
        with wave.open(str(path),"rb") as read:
            self.assertEqual(read.getnframes(),4800)
            self.assertEqual(read.getframerate(),24000)
            pcm=array("h",read.readframes(4800))
        self.assertLessEqual(max(abs(x) for x in pcm),round(.95*32767))

    def test_unknown_config_and_unsafe_speed_rejected(self):
        config=self.root/"config.toml"
        config.write_text('[voice]\nmax_speed=2\n')
        with self.assertRaisesRegex(StudioError,"max_speed"):
            load_config(config)
        config.write_text('[voice]\nspeeed=1\n')
        with self.assertRaisesRegex(StudioError,"Unknown"):
            load_config(config)

    def test_pronunciation_is_literal_whole_word(self):
        self.assertEqual(pronounced("AI and RAIL and AI.",{"AI":"A I"}),"A I and RAIL and A I.")

    def test_doctor_missing_dependencies_is_failure_not_ready(self):
        with patch("local_voice_studio.core.importlib.util.find_spec",return_value=None),patch("local_voice_studio.core.executable",return_value=None):
            status=environment_status(self.config)
        self.assertFalse(status["ready"])
        self.assertEqual(status["model_preflight"],"not_checked")
        self.assertTrue(any("Missing voice" in b for b in status["blockers"]))


if __name__=="__main__":
    unittest.main()
