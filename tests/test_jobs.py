"""Client job behavior. Mock production artifacts are test fixtures, not delivered speech."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from local_voice_studio import jobs
from local_voice_studio.core import sha256,TimingOverflow


class JobTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='voice-job-test-')
        self.root=Path(self.tmp.name)
        (self.root/'config').mkdir();self.profile=self.root/'config/explainer.toml';self.profile.write_text('')
        self.video=self.root/'clip.mov';self.video.write_bytes(b'test fixture video identity')
        self.segments=self.root/'segments.json';self.segments.write_text(json.dumps({'segments':[{'id':'S01','start':0,'duration':4,'text':'One clear example.'}],'duration':4}))
        self.srt=self.root/'captions.srt';self.srt.write_text('1\n00:00:00,200 --> 00:00:03,800\nOne clear example.\n')
        self.timeline=self.root/'timeline.json';self.timeline.write_text(json.dumps({'scenes':[{'duration':4,'narration':'One clear example.'}]}))
        self.sidecar=self.root/'clip.voice.json';self.sidecar.write_text(json.dumps({'schema_version':1,'video':{'path':'clip.mov','sha256':sha256(self.video),'width':1080,'height':1920,'fps':30,'duration':4},'narration':{'segments_path':'segments.json','captions_srt_path':'captions.srt','timeline_path':'timeline.json'}}))
        self.request={'schema_version':1,'job_id':'test-item-001','client':'example-client','item_id':'media-001','action':'render',
                      'video':{'path':str(self.video),'sha256':sha256(self.video)},'sidecar':{'path':str(self.sidecar),'sha256':sha256(self.sidecar)},
                      'profile':{'path':'config/explainer.toml','sha256':sha256(self.profile)},
                      'inputs_sha256':{str(p):sha256(p) for p in [self.segments,self.srt,self.timeline]},
                      'expectations':{'width':1080,'height':1920,'fps':30,'duration':4}}
        self.path=self.root/'request.json';self.save()
        self.root_patch=patch.object(jobs,'VOICE_ROOT',self.root);self.root_patch.start()
        self.runtime=patch.object(jobs,'environment_status',return_value={'blockers':[]});self.runtime.start()
        self.video_probe=patch.object(jobs,'probe_video',return_value={});self.video_probe.start()
        self.audio_probe=patch.object(jobs,'probe_audio',return_value=True);self.audio_probe.start()
        self.pipeline=patch.object(jobs,'run_pipeline',side_effect=self.fake_pipeline);self.producer=self.pipeline.start()

    def tearDown(self):
        for patcher in [self.pipeline,self.audio_probe,self.video_probe,self.runtime,self.root_patch]:patcher.stop()
        self.tmp.cleanup()

    def test_previous_item_identifier_alias_is_still_accepted(self):
        self.request['episode_id'] = self.request.pop('item_id')
        self.save()
        result = jobs.run_job(self.path, dry_run=True)
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['dry_run'])

    def test_conflicting_item_identifiers_are_rejected(self):
        self.request['episode_id'] = 'different-item'
        self.save()
        result = jobs.run_job(self.path, dry_run=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['error']['code'], 'invalid_request')

    def save(self):self.path.write_text(json.dumps(self.request))

    def fake_pipeline(self,plan,config,args):
        folder=args.output_dir;folder.mkdir(parents=True)
        audio=folder/'narration.wav'
        with wave.open(str(audio),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
            output.writeframes(b'\x01\x00'*(24000*4))
        video=folder/'voiced.mp4';video.write_bytes(b'test fixture only')
        receipt={'status':'video_complete','audio':str(audio),'audio_sha256':sha256(audio),'video':str(video),'video_sha256':sha256(video)}
        (folder/'receipt.json').write_text(json.dumps(receipt))
        return receipt

    def test_matching_completed_retry_verifies_outputs_without_resynthesis(self):
        first=jobs.run_job(self.path);second=jobs.run_job(self.path)
        self.assertEqual(first['status'],'succeeded');self.assertTrue(second['reused'])
        self.assertTrue(second['summary']['voice_generated']);self.assertTrue(second['summary']['audio_present'])
        self.assertEqual(self.producer.call_count,1)

    def test_completed_retry_rejects_changed_caption_snapshot(self):
        jobs.run_job(self.path);self.srt.write_text(self.srt.read_text()+'\n')
        result=jobs.run_job(self.path)
        self.assertEqual(result['status'],'blocked');self.assertEqual(result['error']['code'],'source_snapshot_mismatch')
        self.assertEqual(self.producer.call_count,1)

    def test_completed_retry_rejects_tampered_output(self):
        first=jobs.run_job(self.path);Path(first['outputs']['video']['path']).write_bytes(b'tampered')
        result=jobs.run_job(self.path)
        self.assertEqual(result['status'],'blocked');self.assertEqual(result['error']['code'],'output_hash_mismatch')
        self.assertEqual(self.producer.call_count,1)

    def test_same_id_with_changed_request_never_clobbers(self):
        first=jobs.run_job(self.path)
        stored=(self.root/'outputs/jobs/test-item-001/result.json').read_bytes()
        self.request['attempt_nonce']='explicit-attempt-2';self.save()
        result=jobs.run_job(self.path)
        self.assertEqual(result['error']['code'],'job_id_conflict')
        self.assertEqual((self.root/'outputs/jobs/test-item-001/result.json').read_bytes(),stored)

    def test_dry_run_does_not_reserve_job_or_generate_audio(self):
        result=jobs.run_job(self.path,dry_run=True)
        self.assertEqual(result['status'],'succeeded');self.assertTrue(result['dry_run'])
        self.assertFalse(result['summary']['voice_generated']);self.producer.assert_not_called()
        self.assertFalse((self.root/'outputs/jobs').exists())

    def test_every_sidecar_text_asset_hash_is_required(self):
        self.request['inputs_sha256'].pop(str(self.srt));self.save()
        result=jobs.run_job(self.path)
        self.assertEqual(result['status'],'blocked');self.assertEqual(result['error']['code'],'source_snapshot_mismatch')
        self.producer.assert_not_called()

    def test_timing_overflow_is_recorded_and_not_automatically_retried(self):
        self.producer.side_effect=TimingOverflow({'id':'C01','observed_seconds':5,'available_seconds':4,'last_speed':1.15,'max_speed':1.15})
        first=jobs.run_job(self.path);second=jobs.run_job(self.path)
        self.assertEqual(first['status'],'needs_retime');self.assertEqual(second['status'],'blocked')
        self.assertEqual(self.producer.call_count,1)

    def test_malformed_descriptor_still_returns_compact_error(self):
        self.request['video']='wrong-type';self.save();result=jobs.run_job(self.path)
        self.assertEqual(result['status'],'blocked');self.assertIn('error',result)

    def test_fingerprint_excludes_id_and_is_key_order_independent(self):
        other=dict(reversed(list(self.request.items())));other['job_id']='another-id'
        self.assertEqual(jobs.fingerprint(self.request),jobs.fingerprint(other))
        other['attempt_nonce']='retry';self.assertNotEqual(jobs.fingerprint(self.request),jobs.fingerprint(other))


if __name__=='__main__':unittest.main()
