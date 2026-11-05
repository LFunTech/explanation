import asyncio
import base64
import hashlib
import hmac
import json
import logging
import shutil
import time
import weakref
import wave
import zipfile
from pathlib import Path
from uuid import uuid4

import httpx

from .exam import Exam
from .exporters import PRESENTATION_VERSION, make_slides, write_package
from .models import GenerationOptions, Question, Resource
from .providers import GenerationError, Qwen, merge_wav
from .samples import get_sample
from .store import digest, dump, now
from .scope import scope_matches, generation_question
from .teaching import teaching_question, teaching_target, package_prefix

log=logging.getLogger(__name__)


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(dump(value),encoding='utf-8')
    temporary.replace(path)


def signed_token(cfg,package_id,expires):
    return hmac.new(cfg.api_key.encode(),f'{package_id}:{expires}'.encode(),hashlib.sha256).hexdigest()


def package_links(cfg,package_id):
    suffix=''
    if cfg.api_key:
        expires=int(time.time())+86400*7
        suffix=f'?expires={expires}&token={signed_token(cfg,package_id,expires)}'
    base=cfg.public_base_url.rstrip('/')+'/packages/'+package_id+'/'
    links={**{key:base+name+suffix for key,name in [('html_url','index.html'),('content_url','online.html'),('package_player_url','player.html'),('pptx_url','courseware.pptx'),('zip_url','package.zip'),('manifest_url','manifest.json')]},
           'player_url':cfg.public_base_url.rstrip('/')+'/player/'+package_id+suffix,
           'embed_config_url':cfg.public_base_url.rstrip('/')+'/embed/'+package_id+'/config'+suffix,
           'microapp_data_url':cfg.public_base_url.rstrip('/')+'/microapp/'+package_id+'/data'+suffix}
    if cfg.s3_bucket:
        links['minio_player_url']=cfg.public_base_url.rstrip('/')+'/minio-player/'+package_id+suffix
        links['minio_package_player_url']=cfg.public_base_url.rstrip('/')+'/minio-package/'+package_id+'/player'+suffix
    return links


def retryable_storage_error(error):
    from botocore.exceptions import ClientError,HTTPClientError,ConnectionError
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error,(HTTPClientError,ConnectionError)):
            return True
        if isinstance(error,ClientError):
            response=error.response
            return response.get('ResponseMetadata',{}).get('HTTPStatusCode') in {408,429,500,502,503,504} or response.get('Error',{}).get('Code') in {'RequestTimeout','SlowDown','InternalError','ServiceUnavailable'}
        error=error.__cause__ or error.__context__
    return False


class Engine:
    def __init__(self,store,cfg):
        self.store,self.cfg=store,cfg
        self.qwen=Qwen(cfg)
        self.locks=weakref.WeakValueDictionary()
        self.tasks=[]

    def start(self):
        self.store.recover()
        self.tasks=[asyncio.create_task(self.worker()) for _ in range(self.cfg.worker_concurrency)]
        self.tasks.append(asyncio.create_task(self.callbacks()))

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)

    async def worker(self):
        while True:
            item=self.store.claim()
            if not item:
                await asyncio.sleep(.5)
                continue
            try:
                await self.generate(item)
            except asyncio.CancelledError:
                raise
            except GenerationError as error:
                self.store.finish(item['id'],error=dict(code=error.code,message=str(error)))
            except httpx.TimeoutException:
                self.store.finish(item['id'],error=dict(code='upstream_timeout',message='上游服务请求超时，已保留检查点，可重试当前题目。'))
            except httpx.RequestError:
                self.store.finish(item['id'],error=dict(code='upstream_connection',message='上游服务连接中断，已保留检查点，可重试当前题目。'))
            except Exception as error:
                # 不把上游响应、连接串或凭证放入公开错误。
                log.error('generation_failed item=%s type=%s',item['id'],type(error).__name__)
                self.store.finish(item['id'],error=dict(code='generation_failed',message='生成未完成，请检查服务配置后重试；已保存的内容会从检查点恢复。'))

    async def resources(self,question,options):
        question=teaching_question(question)
        # Local curriculum is selected/analyzed before submission. Generation
        # uses the submitted teaching target without another local lookup.
        resources=[]
        if self.cfg.knowledge_api_url:
            async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
                response=await client.post(self.cfg.knowledge_api_url,json=dict(question=question.model_dump(),limit=8),
                    headers={'Authorization':'Bearer '+self.cfg.knowledge_api_key} if self.cfg.knowledge_api_key else {})
                response.raise_for_status()
                for raw in response.json()['resources']:
                    row=Resource.model_validate(raw).model_dump()
                    if row['subject']==question.subject and set(question.grades).intersection(row['grades']) and row['region'] in {question.region,'全国'} and scope_matches(row,question.model_dump(),row.get('source_type',row['kind'])):
                        resources.append(row)
        if self.cfg.exam_db_host and options.variant_count:
            resources+=await asyncio.to_thread(Exam(self.cfg).variants,question,options.variant_count)
        return sorted(resources,key=lambda r:r.get('is_sample',False))[:16]

    async def generate(self,item):
        job=self.store.job(item['job_id'])
        question=Question.model_validate(json.loads(item['question_json']))
        if not question.target_grade:
            raise GenerationError('teaching_target_missing','请先填写目标年级，再重新生成课件；原题年级不会自动作为教学目标。')
        options=GenerationOptions.model_validate(job['request']['options'])
        from .teaching import validate_target_curriculum
        try:validate_target_curriculum(question)
        except ValueError as error:raise GenerationError('teaching_target_grade',str(error)) from None
        context=json.loads(item['context_json']) if item['context_json'] else None
        context_version=5  # Discard older snapshots containing local curriculum.
        context_key=digest(generation_question(question))
        if context is None or context.get('version')!=context_version or context.get('question_key')!=context_key:
            resources=[] if options.mode=='sample' else await self.resources(question,options)
            context=dict(version=context_version,question_key=context_key,resources=resources)
            self.store.context(item['id'],context)
        fingerprint=digest(dict(question=generation_question(question),options=options.model_dump(exclude={'force'}),
            resources=context['resources'],identity=self.cfg.generation_identity()))
        lock_key=('courseware',item['id']) if options.force else ('cache',fingerprint)
        lock=self.locks.setdefault(lock_key,asyncio.Lock())
        async with lock:
            previous=self.store.find_package(fingerprint)
            if previous and not options.force and (self.cfg.data_dir/'packages'/previous['package_id']/'index.html').exists():
                if previous.get('presentation',{}).get('version')!=PRESENTATION_VERSION:
                    self.store.progress(item['id'],'更新课件排版',90)
                    previous=await asyncio.to_thread(self.restyle,previous,publish=False)
                if self.cfg.s3_bucket:
                    prefix=package_prefix(self.cfg,question.model_dump(),previous['package_id'])
                    storage=dict(bucket=self.cfg.s3_bucket,prefix=prefix)
                    if previous.get('storage')!=storage:
                        self.store.progress(item['id'],'保存到对象存储',97)
                        await self.publish(self.cfg.data_dir/'packages'/previous['package_id'],prefix,item['id'])
                        self.store.record_storage(previous['package_id'],storage)
                self.store.finish(item['id'],package_id=previous['package_id'],reused=True)
                return
            checkpoint=self.cfg.data_dir/'work'/item['id']/'checkpoint.json'
            if item.get('generation',0):
                checkpoint=checkpoint.parent/'runs'/str(item['generation'])/'checkpoint.json'
            saved=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else None
            if not saved or saved.get('fingerprint')!=fingerprint:
                if options.mode=='sample':
                    lesson=get_sample(question)
                    lesson['variants']=lesson['variants'][:options.variant_count]
                    review=dict(type='curated_sample',passed=True,note='内置人工编写样例，未调用模型复核')
                else:
                    lesson,review=await self.qwen.lesson(teaching_question(question),context['resources'],options,
                        lambda stage,value:self.store.progress(item['id'],stage,value),checkpoint=checkpoint.with_name('draft.json'))
                saved=dict(fingerprint=fingerprint,lesson=lesson,review=review,package_id=uuid4().hex)
                atomic_json(checkpoint,saved)
            directory=self.cfg.data_dir/'packages'/saved['package_id']
            directory.mkdir(parents=True,exist_ok=True)
            # A closed ZIP is the export checkpoint. Storage retries and service
            # restarts must not repeat model generation or speech synthesis.
            ready=directory/'manifest.json'
            if ready.exists() and zipfile.is_zipfile(directory/'package.zip'):
                manifest=json.loads(ready.read_text(encoding='utf-8'))
                if manifest.get('fingerprint')==fingerprint and all((directory/name).is_file() for name in manifest['files']):
                    if manifest.get('presentation',{}).get('version')!=PRESENTATION_VERSION:
                        manifest['presentation']=dict(version=PRESENTATION_VERSION)
                        slides=json.loads((directory/'slides.json').read_text(encoding='utf-8'))
                        await asyncio.to_thread(write_package,directory,manifest,slides)
                    await self.complete_package(item,directory,manifest)
                    return
            voice_mode=self.cfg.tts_mode if options.voice else 'none'
            if options.mode=='sample' and voice_mode=='qwen':
                voice_mode='browser'
            lesson=saved['lesson']
            slides=make_slides(teaching_question(question).model_dump(),lesson)
            if voice_mode=='qwen':
                try:
                    for index,slide in enumerate(slides):
                        if not slide['narration'].strip():continue
                        self.store.progress(item['id'],f'合成语音 · {index+1}/{len(slides)}',78+int(index/len(slides)*12))
                        # Changing a script must never reuse the previous page's audio.
                        speech_identity=dict(text=slide['narration'],segments=[s['narration'] for s in slide.get('flow',[])])
                        audio_path=directory/'audio'/(slide['id']+'-'+digest(speech_identity)[:12]+'.wav')
                        if slide.get('flow'):
                            fragments=[];cues=[];elapsed=0
                            speech_dir=checkpoint.parent/'speech';speech_dir.mkdir(exist_ok=True)
                            for node,step in enumerate(slide['flow']):
                                if not step['narration'].strip():continue
                                clip=speech_dir/(digest(dict(text=step['narration'],identity=self.cfg.generation_identity()))+'.wav')
                                if not clip.exists():
                                    clip.write_bytes(await self.qwen.speech(step['narration']))
                                with wave.open(str(clip),'rb') as wav:
                                    seconds=wav.getnframes()/wav.getframerate()
                                cues.append(dict(node=node,start_seconds=round(elapsed,3),end_seconds=round(elapsed+seconds,3)))
                                elapsed+=seconds;fragments.append(clip.read_bytes())
                            slide['cues']=cues
                            if not audio_path.exists():
                                audio_path.parent.mkdir(exist_ok=True)
                                audio_path.write_bytes(merge_wav(fragments))
                        elif not audio_path.exists():
                            audio=await self.qwen.speech(slide['narration'])
                            audio_path.parent.mkdir(exist_ok=True)
                            audio_path.write_bytes(audio)
                        with wave.open(str(audio_path),'rb') as wav:
                            slide['duration_seconds']=round(wav.getnframes()/wav.getframerate(),2)
                        slide['audio_file']='audio/'+audio_path.name
                        slide['audio']='data:audio/wav;base64,'+base64.b64encode(audio_path.read_bytes()).decode('ascii')
                except (GenerationError,httpx.HTTPError,wave.Error):
                    voice_mode='browser'
                    self.store.progress(item['id'],'语音暂不可用，继续生成 PPT；课件使用设备朗读',90)
            duration=sum(s.get('duration_seconds',0) for s in slides)
            # Include only the final script's audio in the downloadable package.
            used_audio={s.get('audio_file') for s in slides}
            for old in (directory/'audio').glob('*.wav'):
                if 'audio/'+old.name not in used_audio:
                    old.unlink()
            self.store.progress(item['id'],'装配 HTML、PPTX 与讲解包',92)
            manifest=dict(schema_version='1.1',package_id=saved['package_id'],fingerprint=fingerprint,
                courseware_id=item['id'],generation_requirements=options.generation_requirements,
                presentation=dict(version=PRESENTATION_VERSION),
                created_at=now(),question=question.model_dump(),title=lesson['title'],objective=lesson['objective'],
                teaching_target=teaching_target(question),
                slide_count=len(slides),mode=options.mode,review=saved['review'],voice_mode=voice_mode,
                reading={key:lesson[key] for key in ('knowledge_points','solution_steps','answer','pitfalls','summary')},
                quality=dict(complexity=lesson.get('complexity','sample'),page_limit=None,
                    page_count=len(slides),diagram_count=sum(bool(s.get('diagram')) for s in slides),
                    audio_duration_seconds=round(duration,2) if voice_mode=='qwen' else None,
                    duration_limit_seconds=None),
                resources=[{k:v for k,v in r.items() if k not in {'content','answer','analysis'}} for r in context['resources']],
                variants=lesson['variants'],files=['index.html','courseware.pptx','slides.json','narration.json','manifest.json','package.zip'])
            if self.cfg.s3_bucket:
                manifest['object_prefix']=package_prefix(self.cfg,question.model_dump(),manifest['package_id'])
                manifest['storage']=dict(bucket=self.cfg.s3_bucket,prefix=manifest['object_prefix'])
            await asyncio.to_thread(write_package,directory,manifest,slides)
            await self.complete_package(item,directory,manifest)

    async def complete_package(self,item,directory,manifest):
        if self.cfg.s3_bucket:
            prefix=package_prefix(self.cfg,manifest['question'],manifest['package_id'])
            manifest.update(object_prefix=prefix,storage=dict(bucket=self.cfg.s3_bucket,prefix=prefix))
            self.store.progress(item['id'],'课件已生成，正在上传 MinIO',97)
            await self.publish(directory,prefix,item['id'])
        if not self.store.package(manifest['package_id']):
            self.store.save_package(manifest)
        self.store.finish(item['id'],package_id=manifest['package_id'])

    def restyle(self,previous,*,publish=True):
        """A new immutable presentation of the same reviewed content and audio."""
        source=self.cfg.data_dir/'packages'/previous['package_id']
        slides=json.loads((source/'slides.json').read_text(encoding='utf-8'))
        manifest={**previous,'package_id':uuid4().hex,'created_at':now(),
                  'presentation':dict(version=PRESENTATION_VERSION,derived_from=previous['package_id'])}
        directory=self.cfg.data_dir/'packages'/manifest['package_id']
        directory.mkdir(parents=True,exist_ok=True)
        if (source/'audio').is_dir():
            shutil.copytree(source/'audio',directory/'audio')
        if self.cfg.s3_bucket and publish:
            manifest['object_prefix']=package_prefix(self.cfg,manifest['question'],manifest['package_id'])
            manifest['storage']=dict(bucket=self.cfg.s3_bucket,prefix=manifest['object_prefix'])
        else:
            manifest.pop('object_prefix',None)
            manifest.pop('storage',None)
        write_package(directory,manifest,slides)
        if self.cfg.s3_bucket and publish:
            self.upload(directory,manifest['object_prefix'])
        self.store.save_package(manifest)
        return manifest

    def upload(self,directory,prefix):
        import boto3
        from boto3.s3.transfer import TransferConfig
        from botocore.config import Config
        from botocore.exceptions import ClientError
        client=boto3.client('s3',endpoint_url=self.cfg.s3_endpoint_url or None,region_name=self.cfg.s3_region,
            aws_access_key_id=self.cfg.s3_access_key_id,aws_secret_access_key=self.cfg.s3_secret_access_key,
            config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                connect_timeout=15,read_timeout=120,retries={'mode':'standard','max_attempts':2},proxies={}))
        chunk_size=8*1024*1024
        transfer=TransferConfig(max_concurrency=2,multipart_threshold=chunk_size,multipart_chunksize=chunk_size)
        import mimetypes
        # Upload manifest last, so its presence marks a complete folder.
        files=sorted((p for p in directory.rglob('*') if p.is_file() and not any(part.startswith('.') for part in p.relative_to(directory).parts)),
            key=lambda p:(p.name=='manifest.json',p.as_posix()))
        for path in files:
            key=prefix+'/'+path.relative_to(directory).as_posix()
            sha256=hashlib.sha256();md5=hashlib.md5();parts=[]
            with path.open('rb') as source:
                while chunk:=source.read(chunk_size):
                    sha256.update(chunk);md5.update(chunk);parts.append(hashlib.md5(chunk).digest())
            checksum=sha256.hexdigest()
            etag=(hashlib.md5(b''.join(parts)).hexdigest()+'-'+str(len(parts))) if path.stat().st_size>=chunk_size else md5.hexdigest()
            try:
                remote=client.head_object(Bucket=self.cfg.s3_bucket,Key=key)
                if remote.get('Metadata',{}).get('sha256')==checksum or remote.get('ETag','').strip('"')==etag:
                    continue
            except ClientError as error:
                if error.response.get('ResponseMetadata',{}).get('HTTPStatusCode')!=404 and error.response.get('Error',{}).get('Code') not in {'404','NoSuchKey','NotFound'}:
                    raise
            client.upload_file(str(path),self.cfg.s3_bucket,key,Config=transfer,
                ExtraArgs={'ContentType':mimetypes.guess_type(path.name)[0] or 'application/octet-stream','Metadata':{'sha256':checksum}})

    async def publish(self,directory,prefix,item_id=None):
        attempt=0
        while True:
            try:
                await asyncio.to_thread(self.upload,directory,prefix)
                return
            except Exception as error:
                if not retryable_storage_error(error):
                    raise
                delay=min(30,2**min(attempt,5));attempt+=1
                if item_id:
                    self.store.progress(item_id,f'MinIO 连接暂不可用，{delay} 秒后继续上传 · 第 {attempt} 次',97)
                await asyncio.sleep(delay)

    async def callbacks(self):
        while True:
            with self.store.connect() as db:
                row=db.execute("SELECT * FROM callbacks WHERE status='pending' AND next_at<=? ORDER BY rowid LIMIT 1",(time.time(),)).fetchone()
            if not row:
                await asyncio.sleep(1)
                continue
            job=self.store.job(row['job_id'])
            payload=json.loads(row['payload'])
            for item in payload['items']:
                if item['package_id']:
                    item['result']=package_links(self.cfg,item['package_id'])
                    manifest=self.store.package(item['package_id'])
                    item['variants']=manifest['variants']
            raw=dump(payload).encode('utf-8')
            timestamp=str(int(time.time()))
            signature=hmac.new(self.cfg.callback_secret.encode(),timestamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
            try:
                # 每次重试仍检查白名单，配置变更不能让旧任务绕过边界。
                url=job['request']['callback_url']
                if url not in self.cfg.callback_allowed_urls.split(','):
                    raise ValueError('callback_disabled')
                async with httpx.AsyncClient(timeout=15,follow_redirects=False,trust_env=False) as client:
                    response=await client.post(url,content=raw,headers={'Content-Type':'application/json',
                        'X-Explanation-Timestamp':timestamp,'X-Explanation-Signature':'sha256='+signature,'X-Explanation-Event':row['id']})
                if not 200<=response.status_code<300:
                    raise ValueError('callback_http_'+str(response.status_code))
                status,error='delivered',None
            except (httpx.HTTPError,ValueError) as exc:
                status='failed' if row['attempts']>=4 else 'pending'
                error=str(exc) if isinstance(exc,ValueError) else 'callback_connection_failed'
            with self.store.connect() as db:
                db.execute('UPDATE callbacks SET status=?,attempts=attempts+1,next_at=?,error=? WHERE id=?',
                    (status,time.time()+min(300,5*2**row['attempts']),error,row['id']))
