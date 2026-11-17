"""Local multipart video-to-voice HTTP service with serialized model execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import threading
import traceback
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .core import StudioError, TimingOverflow, load_config, sha256

MAX_VIDEO_BYTES=512*1024*1024
MAX_SUBTITLE_BYTES=2*1024*1024
MAX_VOICE_BYTES=25*1024*1024
MAX_NARRATION_CHARS=100_000
MAX_REFERENCE_TEXT_CHARS=20_000
CHUNK_BYTES=1024*1024
TERMINAL={'succeeded','failed','blocked','needs_retime'}
JOB_ID=re.compile(r'[0-9a-f]{32}')


def _now():
    return datetime.now(timezone.utc).isoformat()


def _safe_message(value,limit=400):
    text=str(value)
    text=re.sub(r'https?://\S+','[endpoint]',text)
    text=re.sub(r'\b[A-Za-z]:\\[^\s\n,;]+','[server-path]',text)
    text=re.sub(r'(?<![\w])/[^\s\n,;\'"<>]+','[server-path]',text)
    text=re.sub(r'(?i)\b(api[_ -]?key|token|secret|password)\s*[=:]\s*[^\s,;]+',r'\1=[redacted]',text)
    return text[:limit]


def _profile_status(config_path,reference_audio=None,reference_text=None):
    try:
        from .providers import backend_status
        config=load_config(config_path)
        raw=backend_status(config,reference_audio=reference_audio,reference_text=reference_text)
        capabilities={key:value for key,value in raw.get('capabilities',{}).items()
                      if isinstance(key,str) and re.fullmatch(r'[a-zA-Z0-9_]{1,80}',key) and type(value) is bool}
        blockers=[_safe_message(value) for value in raw.get('blockers',[]) if isinstance(value,(str,int,float))]
        return {'engine':_safe_message(raw.get('engine',config.engine),80),'capabilities':capabilities,
                'prerequisites_ready':bool(raw.get('prerequisites_ready',not blockers)), 'blockers':blockers}
    except Exception:
        return {'engine':'unknown','capabilities':{},'prerequisites_ready':False,
                'blockers':['Backend status is unavailable; inspect the local server configuration.']}


def _write_json(path,value):
    temporary=path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    temporary.replace(path)


class _BodyLimit:
    """Bound total multipart bytes even when Content-Length is absent."""
    def __init__(self,app,maximum):
        self.app=app;self.maximum=maximum

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope.get('method')!='POST' or scope.get('path')!='/v1/videos':
            return await self.app(scope,receive,send)
        headers={key.lower():value for key,value in scope.get('headers',[])}
        length=headers.get(b'content-length')
        try:declared=int(length) if length else None
        except ValueError:declared=None
        if declared is not None and declared>self.maximum:
            return await JSONResponse({'detail':'Upload request is too large.'},status_code=413)(scope,receive,send)
        total=0
        async def bounded_receive():
            nonlocal total
            message=await receive()
            total+=len(message.get('body',b''))
            if total>self.maximum:
                raise HTTPException(status_code=413,detail='Upload request is too large.')
            return message
        await self.app(scope,bounded_receive,send)


class _Jobs:
    def __init__(self,root,profiles,renderer,check_backend):
        self.root=root;self.profiles=profiles;self.renderer=renderer;self.check_backend=check_backend
        self.lock=threading.RLock();self.records={};self.executor=None;self.closed=False
        (root/'jobs').mkdir(parents=True,exist_ok=True)

    def folder(self,job_id):
        return self.root/'jobs'/job_id

    def save(self,record):
        with self.lock:
            record['updated_at']=_now()
            self.records[record['id']]=record
            _write_json(self.folder(record['id'])/'record.json',record)

    def read(self,job_id):
        with self.lock:
            if not JOB_ID.fullmatch(job_id) or job_id not in self.records:
                raise HTTPException(status_code=404,detail='Video job was not found.')
            return json.loads(json.dumps(self.records[job_id]))

    def start(self):
        with self.lock:
            for path in (self.root/'jobs').glob('*/record.json'):
                try:
                    record=json.loads(path.read_text())
                    if not JOB_ID.fullmatch(record['id']) or path.parent.name!=record['id']:
                        continue
                    if record.get('status') in ('queued','running'):
                        record['status']='blocked'
                        record['error']={'code':'interrupted_restart','message':'The server stopped before this job completed. Submit a new upload to retry manually.'}
                    self.save(record)
                except (OSError,ValueError,KeyError,TypeError):
                    continue
            self.closed=False
            self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='voice-upload')

    def stop(self):
        with self.lock:self.closed=True
        if self.executor:
            self.executor.shutdown(wait=True,cancel_futures=True)
        with self.lock:
            for record in list(self.records.values()):
                if record.get('status')=='queued':
                    record['status']='blocked'
                    record['error']={'code':'server_stopped','message':'The server stopped before processing this upload. Submit a new upload to retry manually.'}
                    self.save(record)

    def submit(self,record):
        with self.lock:
            if self.closed or self.executor is None:
                raise HTTPException(status_code=503,detail='Voice worker is not available.')
            self.save(record)
            self.executor.submit(self.process,record['id'])

    def process(self,job_id):
        record=self.read(job_id);folder=self.folder(job_id)
        record['status']='running';self.save(record)
        try:
            profile=self.profiles[record['profile']]
            if sha256(profile)!=record['profile_sha256']:
                record['status']='blocked'
                record['error']={'code':'profile_changed','message':'The selected server profile changed while this job was queued. Submit again to use its current settings.'}
                self.save(record);return
            inputs=record['inputs']
            narration=Path(inputs['narration']).read_text() if inputs.get('narration') else None
            kwargs={'video':Path(inputs['video']),'config':profile,'output_dir':folder/'artifacts',
                    'narration':narration,'subtitles':Path(inputs['subtitles']) if inputs.get('subtitles') else None,
                    'reference_audio':Path(inputs['reference_audio']) if inputs.get('reference_audio') else None,
                    'reference_text':Path(inputs['reference_text']).read_text() if inputs.get('reference_text') else None}
            if self.check_backend:
                # Reference media may still be MP3/M4A/OGG here. The product facade
                # owns normalization and actual reference validation before factory use.
                status=_profile_status(profile)
                if not status['prerequisites_ready'] or status['blockers']:
                    record['status']='blocked'
                    record['error']={'code':'backend_not_ready','message':'The selected voice profile is not ready. Inspect its prerequisites on the server.'}
                    record['backend_status']=status
                    self.save(record);return
            with (folder/'server.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
                result=self.renderer(**kwargs)
            if not isinstance(result,dict) or result.get('status')!='succeeded':
                raise StudioError('Renderer did not return a successful video result.')
            output=result.get('outputs',{}).get('video',{})
            video=Path(output.get('path','')).resolve()
            if not video.is_relative_to((folder/'artifacts').resolve()) or not video.is_file():
                raise StudioError('Renderer output is missing or outside this upload job.')
            if sha256(video)!=output.get('sha256'):
                raise StudioError('Renderer video checksum does not match the produced file.')
            summary=result.get('summary',{})
            if summary.get('audio_present') is not True or summary.get('voice_generated') is not True:
                raise StudioError('Renderer did not verify generated voice in the final video.')
            record['status']='succeeded';record['result']=result
            self.save(record)
        except TimingOverflow as exc:
            record['status']='needs_retime'
            record['error']={'code':'needs_retime','message':'Narration does not fit the subtitle timing. Extend the affected timing or revise the narration, then submit a new upload.'}
            details=getattr(exc,'details',{})
            for key in ('available_seconds','observed_seconds'):
                value=details.get(key)
                if isinstance(value,(int,float)) and math.isfinite(value):record['error'][key]=value
            self._failure(record,folder,exc)
        except StudioError as exc:
            record['status']='failed'
            record['error']={'code':'production_failed','message':_safe_message(exc)}
            self._failure(record,folder,exc)
        except Exception as exc:
            record['status']='failed'
            record['error']={'code':'server_error','message':'The server could not finish this video. Inspect its local job log using the job ID.'}
            self._failure(record,folder,exc)

    def _failure(self,record,folder,exc):
        (folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
        self.save(record)


def _public_summary(summary):
    result={}
    for key in ('width','height','fps','duration'):
        value=summary.get(key)
        if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):result[key]=value
    for key in ('audio_present','voice_generated','subtitles_burned'):
        if type(summary.get(key)) is bool:result[key]=summary[key]
    return result


def _public_record(record):
    job_id=record['id']
    result={key:record[key] for key in ('id','status','profile','created_at','updated_at')}
    result.update(status_url=f'/v1/videos/{job_id}',download_url=f'/v1/videos/{job_id}/video')
    if record.get('error'):result['error']=record['error']
    if record.get('status')=='succeeded':
        private=record['result'];output=private['outputs']['video']
        result['result']={'schema_version':private.get('schema_version',1),'status':'succeeded',
                          'summary':_public_summary(private.get('summary',{})),
                          'outputs':{'video':{'download_url':result['download_url'],'sha256':output['sha256']}}}
    return result


async def _save_upload(upload,path,maximum,label):
    total=0
    with path.open('xb') as output:
        while chunk:=await upload.read(CHUNK_BYTES):
            total+=len(chunk)
            if total>maximum:raise HTTPException(status_code=413,detail=f'{label} exceeds its upload limit.')
            output.write(chunk)
    if total==0:raise HTTPException(status_code=422,detail=f'{label} is empty.')


def create_app(profiles:dict[str,Path],data_dir:Path,*,renderer=None,allowed_origins:list[str]|None=None)->FastAPI:
    """Build a single-process local service; model calls execute in one worker thread."""
    configured={str(name):Path(path).expanduser().resolve() for name,path in profiles.items()}
    if not configured:raise ValueError('At least one named server profile is required')
    if any(not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}',name) for name in configured):
        raise ValueError('Profile names must be safe identifiers')
    if any(not path.is_file() for path in configured.values()):raise ValueError('Configured profile file does not exist')
    default='default' if 'default' in configured else next(iter(configured))
    injected=renderer is not None
    if renderer is None:
        def renderer(**kwargs):
            from .product import produce_video
            return produce_video(**kwargs)
    root=Path(data_dir).expanduser().resolve()
    jobs=_Jobs(root,configured,renderer,check_backend=not injected)
    @asynccontextmanager
    async def lifespan(app):
        jobs.start()
        try:yield
        finally:jobs.stop()
    app=FastAPI(title='Local Voice Studio',version='1',lifespan=lifespan)
    app.state.jobs=jobs
    app.add_middleware(_BodyLimit,maximum=MAX_VIDEO_BYTES+MAX_SUBTITLE_BYTES+MAX_VOICE_BYTES+2*1024*1024)
    origins=[]
    for value in allowed_origins or []:
        parsed=urlsplit(value)
        try:parsed.port
        except ValueError:raise ValueError('Allowed origin has an invalid port')
        if value=='*' or parsed.scheme not in ('http','https') or not parsed.netloc or parsed.username is not None or parsed.password is not None or parsed.path not in ('','/') or parsed.query or parsed.fragment:
            raise ValueError('Allowed origins must be explicit HTTP(S) origins without credentials or paths')
        origins.append(value.rstrip('/'))
    if origins:
        app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=['GET','POST'],allow_headers=['Content-Type'])

    @app.get('/health')
    def health():
        return {'status':'ok','worker_count':1}

    @app.get('/v1/profiles')
    def profile_list():
        return {'profiles':[{'name':name,**_profile_status(path)} for name,path in configured.items()],
                'default_profile':default}

    @app.get('/',response_class=HTMLResponse)
    def home():
        return HTMLResponse(Path(__file__).with_name('web.html').read_text(encoding='utf-8'))

    @app.post('/v1/videos',status_code=202)
    async def upload_video(video:UploadFile=File(...),subtitles:UploadFile|None=File(None),
                           narration:str|None=Form(None),profile:str|None=Form(None),
                           voice_sample:UploadFile|None=File(None),voice_sample_text:str|None=Form(None)):
        selected=profile or default
        if selected not in configured:raise HTTPException(status_code=422,detail='Select a configured voice profile name.')
        text=narration.strip() if narration else None
        if bool(text)==bool(subtitles):raise HTTPException(status_code=422,detail='Provide exactly one narration text or subtitle file.')
        if text and len(text)>MAX_NARRATION_CHARS:raise HTTPException(status_code=413,detail='Narration text is too long.')
        reference_text=voice_sample_text.strip() if voice_sample_text else None
        if reference_text and len(reference_text)>MAX_REFERENCE_TEXT_CHARS:raise HTTPException(status_code=413,detail='Voice-sample transcript is too long.')
        if reference_text and voice_sample is None:raise HTTPException(status_code=422,detail='A voice-sample transcript requires a voice recording.')
        if voice_sample:
            capabilities=_profile_status(configured[selected])['capabilities']
            if not capabilities.get('reference_audio',False):raise HTTPException(status_code=422,detail='This voice profile does not support a reference recording.')
            if capabilities.get('reference_text_required',False) and not reference_text:raise HTTPException(status_code=422,detail='This voice profile requires the transcript spoken in the reference recording.')
        job_id=uuid4().hex;folder=jobs.folder(job_id);inputs_dir=folder/'inputs'
        inputs_dir.mkdir(parents=True)
        inputs={}
        try:
            video_path=inputs_dir/'video.upload'
            await _save_upload(video,video_path,MAX_VIDEO_BYTES,'Video')
            inputs['video']=str(video_path)
            if text:
                path=inputs_dir/'narration.txt';path.write_text(text,encoding='utf-8');inputs['narration']=str(path)
            if subtitles:
                path=inputs_dir/'subtitles.upload';await _save_upload(subtitles,path,MAX_SUBTITLE_BYTES,'Subtitles')
                try:content=path.read_text(encoding='utf-8-sig')
                except UnicodeError:raise HTTPException(status_code=422,detail='Subtitles must be UTF-8 SRT or VTT text.')
                if '-->' not in content:raise HTTPException(status_code=422,detail='Subtitles must contain SRT or VTT timing cues.')
                actual=path.with_suffix('.vtt' if content.lstrip().startswith('WEBVTT') else '.srt')
                path.replace(actual);inputs['subtitles']=str(actual)
            if voice_sample:
                extension={'audio/wav':'.wav','audio/x-wav':'.wav','audio/mpeg':'.mp3','audio/flac':'.flac','audio/mp4':'.m4a'}.get(voice_sample.content_type,'.audio')
                path=inputs_dir/('voice-sample'+extension);await _save_upload(voice_sample,path,MAX_VOICE_BYTES,'Voice recording')
                inputs['reference_audio']=str(path)
                if reference_text:
                    path=inputs_dir/'voice-sample.txt';path.write_text(reference_text,encoding='utf-8');inputs['reference_text']=str(path)
            timestamp=_now()
            record={'schema_version':1,'id':job_id,'status':'queued','profile':selected,'profile_sha256':sha256(configured[selected]),
                    'created_at':timestamp,'updated_at':timestamp,'inputs':inputs}
            jobs.submit(record)
            return _public_record(record)
        except Exception:
            if job_id not in jobs.records:shutil.rmtree(folder,ignore_errors=True)
            raise
        finally:
            for upload in (video,subtitles,voice_sample):
                if upload:await upload.close()

    @app.get('/v1/videos/{job_id}')
    def status(job_id:str):
        return _public_record(jobs.read(job_id))

    @app.get('/v1/videos/{job_id}/video')
    def download(job_id:str):
        record=jobs.read(job_id)
        if record['status']!='succeeded':raise HTTPException(status_code=409,detail='The video is not available for download yet.')
        output=record['result']['outputs']['video'];path=Path(output['path']).resolve()
        if not path.is_relative_to((jobs.folder(job_id)/'artifacts').resolve()) or not path.is_file() or sha256(path)!=output['sha256']:
            record['status']='blocked';record['error']={'code':'output_changed','message':'The stored output is missing or changed. Submit a new upload to retry manually.'}
            jobs.save(record)
            raise HTTPException(status_code=409,detail='The stored video is missing or changed.')
        mime='video/mp4' if path.suffix.lower()=='.mp4' else 'video/quicktime'
        return FileResponse(path,media_type=mime,filename=f'voice-video-{job_id}{path.suffix}',headers={'ETag':'"'+output['sha256']+'"'})
    return app
