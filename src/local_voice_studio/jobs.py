"""Hash-bound, idempotent client jobs. Full production state stays in this repository."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import traceback
import wave
from array import array

from .core import (StudioError,TimingOverflow,build_plan,environment_status,load_config,
                   number,object_value,read_json,required_text,sha256,probe_video)

VOICE_ROOT=Path(__file__).resolve().parents[2]


class JobBlocked(StudioError):
    def __init__(self,code,message):
        self.code=code
        super().__init__(message)


def fingerprint(request: dict) -> str:
    value={key:item for key,item in request.items() if key!='job_id'}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()


def digest_value(value,label):
    value=required_text(value,label)
    if not re.fullmatch(r'[0-9a-f]{64}',value):
        raise JobBlocked('invalid_request',f'{label} requires a lowercase SHA-256 hex digest')
    return value


def check_request(request):
    if type(request.get('schema_version')) is not int or request['schema_version']!=1:
        raise JobBlocked('invalid_request','schema_version must be 1')
    permitted={'schema_version','job_id','client','item_id','episode_id','action','video','sidecar','profile','inputs_sha256','expectations','options','attempt_nonce'}
    if set(request)-permitted:
        raise JobBlocked('invalid_request',f'Unknown request keys: {sorted(set(request)-permitted)}')
    job_id=required_text(request.get('job_id'),'job_id')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}',job_id) or job_id in ('.','..'):
        raise JobBlocked('invalid_request','job_id must be a safe identifier of 1–128 characters')
    required_text(request.get('client'),'client')
    item_id = required_text(request.get('item_id', request.get('episode_id')), 'item_id')
    if 'item_id' in request and 'episode_id' in request and request['episode_id'] != item_id:
        raise JobBlocked('invalid_request', 'item_id and its compatibility alias disagree')
    if request.get('action')!='render':
        raise JobBlocked('invalid_request','Only action=render is supported by protocol v1')
    for key in ('video','sidecar','profile'):
        item=object_value(request.get(key),key)
        if set(item)!={'path','sha256'}:
            raise JobBlocked('invalid_request',f'{key} requires exactly path and sha256')
        required_text(item['path'],key+'.path');digest_value(item['sha256'],key+'.sha256')
    options=object_value(request.get('options',{}),'options')
    if set(options)-{'audio_only','allow_downloads'} or any(type(value) is not bool for value in options.values()):
        raise JobBlocked('invalid_request','options supports only boolean audio_only and allow_downloads')
    expectations=object_value(request.get('expectations'),'expectations')
    if set(expectations)!={'width','height','fps','duration'}:
        raise JobBlocked('invalid_request','expectations requires exactly width,height,fps,duration')
    for key in ('width','height'):
        if type(expectations[key]) is not int or expectations[key]<=0:
            raise JobBlocked('invalid_request',f'expectations.{key} must be a positive integer')
    for key in ('fps','duration'):number(expectations[key],'expectations.'+key,0,True)
    assets=object_value(request.get('inputs_sha256'),'inputs_sha256')
    for path,digest in assets.items():
        if not Path(path).is_absolute():
            raise JobBlocked('invalid_request','inputs_sha256 paths must be absolute')
        digest_value(digest,'inputs_sha256 digest')
    if 'attempt_nonce' in request:required_text(request['attempt_nonce'],'attempt_nonce')
    return job_id


def absolute_input(item,label,root=None):
    path=Path(item['path']).expanduser()
    if root is None and not path.is_absolute():
        raise JobBlocked('invalid_request',f'{label}.path must be absolute')
    path=(root/path).resolve() if root is not None and not path.is_absolute() else path.resolve()
    if root is not None and not path.is_relative_to(root.resolve()):
        raise JobBlocked('invalid_profile','Profile must be inside the voice repository')
    if not path.is_file():
        raise JobBlocked('missing_input',f'{label} does not exist: {path}')
    if sha256(path)!=item['sha256']:
        raise JobBlocked('input_hash_mismatch',f'{label} changed; regenerate the request from current inputs')
    return path


def validate_inputs(request):
    video=absolute_input(request['video'],'video')
    sidecar=absolute_input(request['sidecar'],'sidecar')
    profile=absolute_input(request['profile'],'profile',VOICE_ROOT)
    config=load_config(profile)
    plan=build_plan(video,config,sidecar)
    expected_assets={str(Path(path).resolve()):digest for path,digest in request['inputs_sha256'].items()}
    if len(expected_assets)!=len(request['inputs_sha256']):
        raise JobBlocked('invalid_request','inputs_sha256 contains duplicate resolved paths')
    actual_assets={path:digest for path,digest in plan.inputs.items() if Path(path).resolve()!=sidecar}
    if expected_assets!=actual_assets:
        raise JobBlocked('source_snapshot_mismatch','Every sidecar-referenced narration/SRT/timeline asset must match inputs_sha256 exactly')
    expectations=request['expectations']
    if plan.width!=expectations['width'] or plan.height!=expectations['height'] or abs(plan.fps-expectations['fps'])>.001 or abs(plan.duration-expectations['duration'])>.001:
        raise JobBlocked('expectation_mismatch','Sidecar geometry/fps/duration differs from request expectations')
    plan.inputs[str(profile)]=request['profile']['sha256']
    return plan,config,profile


def base_result(request,status):
    expected=request.get('expectations',{}) if isinstance(request.get('expectations',{}),dict) else {}
    video=request.get('video',{}) if isinstance(request.get('video',{}),dict) else {}
    profile=request.get('profile',{}) if isinstance(request.get('profile',{}),dict) else {}
    return {'schema_version':1,'job_id':request.get('job_id'),'status':status,
            'input_video_sha256':video.get('sha256'),
            'profile_sha256':profile.get('sha256'),'outputs':{},
            'summary':{**{key:expected.get(key) for key in ('width','height','fps','duration')},
                       'audio_present':False,'voice_generated':False}}


def write_json(path,value):
    temporary=path.with_name(path.name+'.partial')
    temporary.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    temporary.replace(path)


def artifact(path,digest=None):
    path=Path(path).resolve()
    if not path.is_file():raise JobBlocked('output_missing',f'Output is missing: {path}')
    actual=sha256(path)
    if digest is not None and actual!=digest:
        raise JobBlocked('output_hash_mismatch',f'Output changed: {path}')
    return {'path':str(path),'sha256':actual}


def verify_wave(path,expected_duration):
    try:
        with wave.open(str(path),'rb') as audio:
            frames=audio.getnframes();rate=audio.getframerate()
            if audio.getsampwidth()!=2 or audio.getnchannels()!=1 or frames<=0 or abs(frames/rate-expected_duration)>.05:
                raise JobBlocked('invalid_audio','Narration WAV format or duration is invalid')
            samples=array('h',audio.readframes(frames))
            if not samples or max(abs(value) for value in samples)==0:
                raise JobBlocked('silent_audio','Narration WAV contains no nonzero audio')
    except (OSError,wave.Error) as exc:
        raise JobBlocked('invalid_audio',f'Cannot read narration WAV: {exc}') from exc


def probe_audio(path,config):
    ffprobe=environment_status(config,audio_only=False)['ffprobe']
    if not ffprobe:raise JobBlocked('missing_runtime','ffprobe is required to verify delivery audio')
    result=subprocess.run([ffprobe,'-v','error','-select_streams','a:0','-show_entries','stream=codec_type,duration,channels,sample_rate','-of','json',str(path)],capture_output=True,text=True)
    try:
        data=json.loads(result.stdout);audio=data['streams'][0]
        valid=result.returncode==0 and audio['codec_type']=='audio' and int(audio['channels'])>0 and int(audio['sample_rate'])>0 and float(audio['duration'])>0
    except (ValueError,KeyError,IndexError,TypeError):valid=False
    if not valid:raise JobBlocked('missing_delivery_audio','The delivery video has no verified nonempty audio stream')
    return True


def run_pipeline(plan,config,args):
    from .cli import render
    return render(plan,config,args)


def verify_completed(result,plan,config,job_folder,audio_only=False):
    outputs=result['outputs']
    for name,item in outputs.items():
        path=Path(item['path']).resolve()
        if not path.is_relative_to(job_folder.resolve()):
            raise JobBlocked('output_location_changed','Stored output is outside this job; do not reuse an old relocated path')
        artifact(path,item['sha256'])
    verify_wave(Path(outputs['narration']['path']),plan.duration)
    summary={key:getattr(plan,key) for key in ('width','height','fps','duration')}
    summary.update(audio_present=False,voice_generated=True)
    if not audio_only:
        video=Path(outputs['video']['path'])
        from dataclasses import replace
        probe_video(replace(plan,video=video,digest=outputs['video']['sha256']),config)
        summary['audio_present']=probe_audio(video,config)
    return summary


def run_job(request_path,dry_run=False):
    request={};folder=None;created=False;phase='request'
    try:
        request=read_json(Path(request_path).expanduser().resolve())
        job_id=check_request(request)
        request_fingerprint=fingerprint(request)
        folder=VOICE_ROOT/'outputs/jobs'/job_id
        if folder.exists():
            if not (folder/'request.json').is_file() or not (folder/'request.sha256').is_file():
                raise JobBlocked('incomplete_job_record','Existing job record is incomplete; use an explicit new attempt nonce/job ID')
            if (folder/'request.sha256').read_text().strip()!=request_fingerprint or fingerprint(read_json(folder/'request.json'))!=request_fingerprint:
                raise JobBlocked('job_id_conflict','This job_id belongs to a different request; use a new deterministic job ID')
            plan,config,profile=validate_inputs(request)
            if not (folder/'result.json').is_file():
                raise JobBlocked('job_in_progress_or_incomplete','Job is running or interrupted; do not start duplicate synthesis')
            prior=read_json(folder/'result.json')
            if prior.get('status')!='succeeded':
                if dry_run:
                    result=base_result(request,'succeeded');result['dry_run']=True;return result
                raise JobBlocked('previous_attempt_not_completed','Existing job did not succeed; use an explicit new attempt nonce/job ID')
            summary=verify_completed(prior,plan,config,folder,request.get('options',{}).get('audio_only',False))
            if dry_run:
                result=base_result(request,'succeeded');result['dry_run']=True;return result
            return {**prior,'summary':summary,'reused':True}
        if not dry_run:
            folder.parent.mkdir(parents=True,exist_ok=True)
            try:folder.mkdir()
            except FileExistsError:
                raise JobBlocked('job_in_progress_or_incomplete','Another worker reserved this job; no duplicate synthesis started')
            created=True
            write_json(folder/'request.json',request)
            (folder/'request.sha256').write_text(request_fingerprint+'\n')
        phase='validate'
        plan,config,profile=validate_inputs(request)
        if dry_run:
            result=base_result(request,'succeeded');result['dry_run']=True;return result
        options=request.get('options',{})
        audio_only=options.get('audio_only',False)
        phase='preflight'
        runtime=environment_status(config,audio_only=audio_only)
        if runtime['blockers']:
            raise JobBlocked('missing_runtime','; '.join(runtime['blockers']))
        phase='render'
        args=argparse.Namespace(config=profile,output_dir=folder/'artifacts',audio_only=audio_only,allow_downloads=options.get('allow_downloads',False))
        with (folder/'run.log').open('w') as log,redirect_stdout(log),redirect_stderr(log):
            receipt=run_pipeline(plan,config,args)
        phase='verify'
        outputs={'narration':artifact(receipt['audio'],receipt['audio_sha256']),
                 'receipt':artifact(folder/'artifacts/receipt.json')}
        if not audio_only:outputs['video']=artifact(receipt['video'],receipt['video_sha256'])
        result=base_result(request,'succeeded');result['outputs']=outputs
        result['summary']=verify_completed(result,plan,config,folder,audio_only)
        write_json(folder/'result.json',result)
        return result
    except TimingOverflow as exc:
        result=base_result(request,'needs_retime');result['error']={'code':'timing_overflow','message':str(exc)}
    except JobBlocked as exc:
        result=base_result(request,'blocked');result['error']={'code':exc.code,'message':str(exc)}
    except StudioError as exc:
        blocked=phase in ('request','validate','preflight') or (phase=='render' and folder is not None and not (folder/'artifacts').exists())
        result=base_result(request,'blocked' if blocked else 'failed')
        result['error']={'code':'invalid_inputs_or_runtime' if blocked else 'production_failed','message':str(exc)}
    except Exception as exc:
        result=base_result(request,'failed');result['error']={'code':'unexpected_failure','message':str(exc)[:800]}
        if created:(folder/'error.log').write_text(traceback.format_exc())
    if created:
        receipt_path=folder/'artifacts/receipt.json'
        if receipt_path.is_file():result['outputs']['receipt']=artifact(receipt_path)
        write_json(folder/'result.json',result)
    return result


def exit_code(result):
    return {'succeeded':0,'blocked':2,'failed':1,'needs_retime':3}[result['status']]
