"""CPU-only HTTP contract tests. The injected renderer is a fixture, not actual TTS."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from local_voice_studio import api
from local_voice_studio.core import StudioError,TimingOverflow


SRT=b'1\n00:00:00,000 --> 00:00:02,000\nOne clear example.\n'


def fake_result(**kwargs):
    folder=kwargs['output_dir'];folder.mkdir(parents=True)
    video=folder/'video.mp4';video.write_bytes(b'HTTP fixture renderer output; not actual media')
    return {'schema_version':1,'status':'succeeded','outputs':{'video':{'path':str(video),'sha256':hashlib.sha256(video.read_bytes()).hexdigest()},
            'receipt':{'path':str(folder/'private-receipt.json'),'sha256':'0'*64}},
            'summary':{'width':1080,'height':1920,'fps':30,'duration':2,'audio_present':True,'voice_generated':True,'subtitles_burned':bool(kwargs['subtitles']),
                       'private_debug':'/private/server/path'}}


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='voice-api-test-',dir='/private/tmp')
        self.root=Path(self.tmp.name);self.profile=self.root/'profile.toml';self.profile.write_text('')
        self.data=self.root/'service-data'

    def tearDown(self):self.tmp.cleanup()

    def app(self,renderer=fake_result):
        return api.create_app({'default':self.profile},self.data,renderer=renderer)

    def upload(self,client,**extra):
        return client.post('/v1/videos',files={'video':('video.mp4',b'fixture input video','video/mp4'),
                                             'subtitles':('captions.srt',SRT,'application/x-subrip')},**extra)

    def terminal(self,client,url):
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            response=client.get(url);self.assertEqual(response.status_code,200)
            record=response.json()
            if record['status'] in api.TERMINAL:return record
            time.sleep(.01)
        self.fail('HTTP fixture job did not finish')

    def test_upload_poll_and_download_lifecycle_has_no_server_paths(self):
        with TestClient(self.app()) as client:
            response=self.upload(client);self.assertEqual(response.status_code,202)
            accepted=response.json();self.assertEqual(accepted['status'],'queued')
            record=self.terminal(client,accepted['status_url']);self.assertEqual(record['status'],'succeeded')
            self.assertTrue(record['result']['summary']['voice_generated'])
            self.assertNotIn('private_debug',record['result']['summary'])
            self.assertNotIn(str(self.root),json.dumps(record))
            download=client.get(accepted['download_url']);self.assertEqual(download.status_code,200)
            self.assertEqual(hashlib.sha256(download.content).hexdigest(),record['result']['outputs']['video']['sha256'])
            self.assertIn(accepted['id'],download.headers['content-disposition'])

    def test_exactly_one_narration_source_and_named_profile_are_required(self):
        with TestClient(self.app()) as client:
            self.assertEqual(client.post('/v1/videos',files={'video':('v.mp4',b'x')}).status_code,422)
            self.assertEqual(self.upload(client,data={'narration':'Both supplied.'}).status_code,422)
            self.assertEqual(self.upload(client,data={'profile':str(self.profile)}).status_code,422)

    def test_text_narration_is_passed_to_facade_without_subtitles(self):
        seen=[]
        def renderer(**kwargs):seen.append(kwargs);return fake_result(**kwargs)
        with TestClient(self.app(renderer)) as client:
            response=client.post('/v1/videos',files={'video':('../../outside.mp4',b'v','video/mp4')},data={'narration':'A complete explanation.'})
            record=self.terminal(client,response.json()['status_url'])
            self.assertEqual(record['status'],'succeeded');self.assertEqual(seen[0]['narration'],'A complete explanation.')
            self.assertIsNone(seen[0]['subtitles']);self.assertEqual(seen[0]['video'].name,'video.upload')
            self.assertTrue(seen[0]['video'].is_relative_to(self.data))
            self.assertFalse((self.root/'outside.mp4').exists())

    def test_vtt_content_selects_generated_vtt_path(self):
        seen=[]
        def renderer(**kwargs):seen.append(kwargs);return fake_result(**kwargs)
        with TestClient(self.app(renderer)) as client:
            response=client.post('/v1/videos',files={'video':('v.mov',b'v'),
                'subtitles':('../../anything.exe',b'WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nHello.\n')})
            self.assertEqual(self.terminal(client,response.json()['status_url'])['status'],'succeeded')
            self.assertEqual(seen[0]['subtitles'].name,'subtitles.vtt')

    def test_upload_limit_returns_413_and_removes_partial_upload(self):
        with patch.object(api,'MAX_VIDEO_BYTES',4):
            with TestClient(self.app()) as client:
                self.assertEqual(self.upload(client).status_code,413)
                self.assertEqual(list((self.data/'jobs').iterdir()),[])

    def test_subtitle_limit_and_invalid_text_are_rejected(self):
        with patch.object(api,'MAX_SUBTITLE_BYTES',8):
            with TestClient(self.app()) as client:self.assertEqual(self.upload(client).status_code,413)
        with TestClient(self.app()) as client:
            response=client.post('/v1/videos',files={'video':('v',b'v'),'subtitles':('bad.srt',b'not subtitles')})
            self.assertEqual(response.status_code,422)

    def test_reference_voice_is_capability_gated_and_passed_when_supported(self):
        seen=[]
        def renderer(**kwargs):seen.append(kwargs);return fake_result(**kwargs)
        status={'engine':'fixture','capabilities':{'reference_audio':True,'reference_text_required':True},'prerequisites_ready':True,'blockers':[]}
        with patch.object(api,'_profile_status',return_value=status):
            with TestClient(self.app(renderer)) as client:
                files={'video':('v',b'v'),'voice_sample':('../../recording.wav',b'fixture recording','audio/wav')}
                self.assertEqual(client.post('/v1/videos',files=files,data={'narration':'Hello.'}).status_code,422)
                response=client.post('/v1/videos',files=files,data={'narration':'Hello.','voice_sample_text':'Recorded words.'})
                self.assertEqual(self.terminal(client,response.json()['status_url'])['status'],'succeeded')
                self.assertEqual(seen[0]['reference_audio'].name,'voice-sample.wav')
                self.assertEqual(seen[0]['reference_text'],'Recorded words.')
        status['capabilities']={'reference_audio':False}
        with patch.object(api,'_profile_status',return_value=status):
            with TestClient(self.app()) as client:
                self.assertEqual(client.post('/v1/videos',files={'video':('v',b'v'),'voice_sample':('voice.wav',b'a')},data={'narration':'Hello.'}).status_code,422)

    def test_one_worker_serializes_renderer_calls_and_download_is_unavailable_while_running(self):
        entered=threading.Event();release=threading.Event();lock=threading.Lock();state={'active':0,'maximum':0,'calls':0}
        def renderer(**kwargs):
            with lock:
                state['active']+=1;state['maximum']=max(state['maximum'],state['active']);state['calls']+=1;first=state['calls']==1
            if first:entered.set();release.wait(3)
            try:return fake_result(**kwargs)
            finally:
                with lock:state['active']-=1
        with TestClient(self.app(renderer)) as client:
            try:
                one=self.upload(client).json();self.assertTrue(entered.wait(2))
                two=self.upload(client).json();self.assertEqual(client.get(two['status_url']).json()['status'],'queued')
                self.assertEqual(client.get(one['download_url']).status_code,409)
                release.set()
                self.assertEqual(self.terminal(client,one['status_url'])['status'],'succeeded')
                self.assertEqual(self.terminal(client,two['status_url'])['status'],'succeeded')
                self.assertEqual(state['maximum'],1)
            finally:release.set()

    def test_restart_blocks_queued_or_running_records_without_reexecution(self):
        for index,status in enumerate(['queued','running']):
            job_id=f'{index+1:032x}';folder=self.data/'jobs'/job_id;folder.mkdir(parents=True)
            (folder/'record.json').write_text(json.dumps({'id':job_id,'status':status,'profile':'default','created_at':'2026-11-17T00:00:00+00:00','updated_at':'2026-11-17T00:00:00+00:00','inputs':{}}))
        calls=[]
        def renderer(**kwargs):calls.append(kwargs);return fake_result(**kwargs)
        with TestClient(self.app(renderer)) as client:
            for index in range(2):
                result=client.get(f'/v1/videos/{index+1:032x}').json()
                self.assertEqual(result['status'],'blocked');self.assertEqual(result['error']['code'],'interrupted_restart')
        self.assertEqual(calls,[])

    def test_needs_retime_is_meaningful_and_server_failures_do_not_leak_paths(self):
        def overflow(**kwargs):
            raise TimingOverflow({'id':'C01','available_seconds':2,'observed_seconds':3,'last_speed':1.15,'max_speed':1.15})
        with TestClient(self.app(overflow)) as client:
            accepted=self.upload(client).json();result=self.terminal(client,accepted['status_url'])
            self.assertEqual(result['status'],'needs_retime');self.assertEqual(result['error']['observed_seconds'],3)
        def fail(**kwargs):raise StudioError('Cannot read /private/server/secret.wav; api_key=hidden-value')
        with TestClient(self.app(fail)) as client:
            accepted=self.upload(client).json();result=self.terminal(client,accepted['status_url'])
            self.assertEqual(result['status'],'failed')
            self.assertNotIn('/private/server',json.dumps(result));self.assertNotIn('hidden-value',json.dumps(result))
            self.assertTrue((self.data/'jobs'/accepted['id']/'error.log').is_file())

    def test_tampered_output_is_not_downloaded(self):
        with TestClient(self.app()) as client:
            accepted=self.upload(client).json();self.terminal(client,accepted['status_url'])
            record=json.loads((self.data/'jobs'/accepted['id']/'record.json').read_text())
            Path(record['result']['outputs']['video']['path']).write_bytes(b'changed')
            self.assertEqual(client.get(accepted['download_url']).status_code,409)
            self.assertEqual(client.get(accepted['status_url']).json()['status'],'blocked')

    def test_health_ui_and_profiles_do_not_expose_profile_paths(self):
        info={'engine':'fixture','capabilities':{'reference_audio':False},'prerequisites_ready':False,'blockers':['A local prerequisite is missing.']}
        with patch.object(api,'_profile_status',return_value=info):
            with TestClient(self.app()) as client:
                self.assertEqual(client.get('/health').json(),{'status':'ok','worker_count':1})
                self.assertIn('Create voiced video',client.get('/').text)
                response=client.get('/v1/profiles');self.assertEqual(response.status_code,200)
                self.assertNotIn(str(self.profile),response.text)
                self.assertEqual(response.json()['default_profile'],'default')

    def test_compressed_uploaded_reference_is_deferred_to_facade_not_backend_preflight(self):
        calls=[];seen=[]
        def status(config,*,reference_audio=None,reference_text=None,**kwargs):
            calls.append((reference_audio,reference_text))
            if reference_audio is not None:
                raise StudioError('Compressed references must be normalized by the product facade first.')
            return {'engine':'mlx_audio','capabilities':{'reference_audio':True,'reference_text_required':True},
                    'prerequisites_ready':True,'blockers':[]}
        def renderer(**kwargs):seen.append(kwargs);return fake_result(**kwargs)
        app=self.app(renderer);app.state.jobs.check_backend=True
        with patch('local_voice_studio.providers.backend_status',side_effect=status):
            with TestClient(app) as client:
                listing=client.get('/v1/profiles').json()['profiles'][0]
                self.assertTrue(listing['prerequisites_ready'])
                response=client.post('/v1/videos',files={'video':('v.mp4',b'v'),
                    'voice_sample':('sample.mp3',b'fixture compressed sample','audio/mpeg')},
                    data={'narration':'An explanation.','voice_sample_text':'Recorded words.'})
                self.assertEqual(response.status_code,202)
                self.assertEqual(self.terminal(client,response.json()['status_url'])['status'],'succeeded')
        self.assertTrue(calls);self.assertTrue(all(audio is None and text is None for audio,text in calls))
        self.assertEqual(seen[0]['reference_audio'].suffix,'.mp3')
        self.assertEqual(seen[0]['reference_text'],'Recorded words.')

    def test_configured_cross_origin_preflight_and_status_requests(self):
        app=api.create_app({'default':self.profile},self.data,renderer=fake_result,allowed_origins=['http://localhost:3000'])
        with TestClient(app) as client:
            response=client.options('/v1/videos',headers={'Origin':'http://localhost:3000',
                'Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'Content-Type'})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.headers['access-control-allow-origin'],'http://localhost:3000')
            self.assertIn('POST',response.headers['access-control-allow-methods'])
            self.assertEqual(client.get('/health',headers={'Origin':'http://localhost:3000'}).headers['access-control-allow-origin'],'http://localhost:3000')
            denied=client.options('/v1/videos',headers={'Origin':'http://elsewhere.test',
                'Access-Control-Request-Method':'POST'})
            self.assertEqual(denied.status_code,400)
            self.assertNotIn('access-control-allow-origin',denied.headers)
        with self.assertRaises(ValueError):
            api.create_app({'default':self.profile},self.data,renderer=fake_result,allowed_origins=['*'])

    def test_chunked_request_is_bounded_without_content_length(self):
        with patch.object(api,'MAX_VIDEO_BYTES',4),patch.object(api,'MAX_SUBTITLE_BYTES',4),patch.object(api,'MAX_VOICE_BYTES',4):
            with TestClient(self.app()) as client:
                prefix=b'--fixture\r\nContent-Disposition: form-data; name="video"; filename="v.mp4"\r\nContent-Type: video/mp4\r\n\r\n'
                def body():
                    yield prefix
                    yield b'x'*(2*1024*1024+32)
                    yield b'\r\n--fixture--\r\n'
                response=client.post('/v1/videos',content=body(),headers={'Content-Type':'multipart/form-data; boundary=fixture'})
                self.assertEqual(response.status_code,413)


if __name__=='__main__':unittest.main()
