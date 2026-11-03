import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
import weakref
import wave
from pathlib import Path
from uuid import uuid4

import httpx

from .exam import Exam
from .exporters import make_slides, write_package
from .kb import KnowledgeBase
from .models import GenerationOptions, Question, Resource
from .providers import GenerationError, Qwen, merge_wav
from .samples import get_sample
from .store import digest, dump, now

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
    return {key:base+name+suffix for key,name in [('html_url','index.html'),('pptx_url','courseware.pptx'),('zip_url','package.zip'),('manifest_url','manifest.json')]}


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
            except Exception as error:
                # 不把上游响应、连接串或凭证放入公开错误。
                log.error('generation_failed item=%s type=%s',item['id'],type(error).__name__)
                self.store.finish(item['id'],error=dict(code='generation_failed',message='生成未完成，请检查服务配置后重试；已通过复核的内容会从检查点恢复。'))

    async def resources(self,question,options):
        resources=[]
        for row in self.store.resources():
            if row['subject']==question.subject and question.grade in row['grades'] and row['region'] in {question.region,'全国'}:
                if any(k in question.stem or k in question.knowledge_points for k in row['knowledge_points']):
                    resources.append(row)
        if self.cfg.knowledge_api_url:
            async with httpx.AsyncClient(timeout=30,trust_env=False) as client:
                response=await client.post(self.cfg.knowledge_api_url,json=dict(question=question.model_dump(),limit=8),
                    headers={'Authorization':'Bearer '+self.cfg.knowledge_api_key} if self.cfg.knowledge_api_key else {})
                response.raise_for_status()
                for raw in response.json()['resources']:
                    row=Resource.model_validate(raw).model_dump()
                    if row['subject']==question.subject and question.grade in row['grades'] and row['region'] in {question.region,'全国'}:
                        resources.append(row)
        if self.cfg.vector_database_url:
            kb=KnowledgeBase(self.cfg)
            stats=await asyncio.to_thread(kb.stats)
            if any(r['embedded'] and r['source_type']!='graph' for r in stats['by_source']):
                hits=await kb.search(question.stem+' '+' '.join(question.knowledge_points),question.subject,question.grade,question.region)
                for hit in hits:
                    meta=hit['metadata']
                    resources.append(dict(id=hit['id'],title=meta['title'],kind='curriculum' if hit['source_type']=='curriculum' else 'knowledge',
                        subject=question.subject,grades=hit['grades'],region=meta.get('region','全国'),content=hit['content'],
                        knowledge_points=hit['labels'],source=meta.get('source','本地知识库'),version=meta.get('version','1'),
                        page=meta.get('page'),match_scope=hit['match_scope'],score=hit['score'],is_sample=False,
                        extraction=meta.get('extraction','text'),review_status=meta.get('review_status','provided')))
        if self.cfg.exam_db_host and options.variant_count:
            resources+=await asyncio.to_thread(Exam(self.cfg).variants,question,options.variant_count)
        return sorted(resources,key=lambda r:r.get('is_sample',False))[:16]

    async def generate(self,item):
        job=self.store.job(item['job_id'])
        question=Question.model_validate(json.loads(item['question_json']))
        options=GenerationOptions.model_validate(job['request']['options'])
        context=json.loads(item['context_json']) if item['context_json'] else None
        if context is None or context.get('version')!=2:
            resources=[] if options.mode=='sample' else await self.resources(question,options)
            context=dict(version=2,resources=resources)
            self.store.context(item['id'],context)
        fingerprint=digest(dict(question=question.model_dump(),options=options.model_dump(exclude={'force'}),
            resources=context['resources'],identity=self.cfg.generation_identity()))
        lock=self.locks.setdefault(fingerprint,asyncio.Lock())
        async with lock:
            previous=self.store.find_package(fingerprint)
            if previous and not options.force and (self.cfg.data_dir/'packages'/previous['package_id']/'index.html').exists():
                self.store.finish(item['id'],package_id=previous['package_id'],reused=True)
                return
            checkpoint=self.cfg.data_dir/'work'/item['id']/'checkpoint.json'
            saved=json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else None
            if not saved or saved.get('fingerprint')!=fingerprint:
                if options.mode=='sample':
                    lesson=get_sample(question)
                    if options.variant_count>len(lesson['variants']):
                        raise GenerationError('sample_variants','内置样例最多包含两道变式题。')
                    lesson['variants']=lesson['variants'][:options.variant_count]
                    review=dict(type='curated_sample',passed=True,note='内置人工编写样例，未调用模型复核')
                else:
                    lesson,review=await self.qwen.lesson(question,context['resources'],options,
                        lambda stage,value:self.store.progress(item['id'],stage,value))
                saved=dict(fingerprint=fingerprint,lesson=lesson,review=review,package_id=uuid4().hex)
                atomic_json(checkpoint,saved)
            directory=self.cfg.data_dir/'packages'/saved['package_id']
            directory.mkdir(parents=True,exist_ok=True)
            voice_mode=self.cfg.tts_mode if options.voice else 'none'
            if options.mode=='sample' and voice_mode=='qwen':
                voice_mode='browser'
            for timing_attempt in range(3):
                lesson=saved['lesson']
                slides=make_slides(question.model_dump(),lesson)
                if voice_mode=='qwen':
                    for index,slide in enumerate(slides):
                        self.store.progress(item['id'],f'合成语音 · {index+1}/{len(slides)}',78+int(index/len(slides)*12))
                        # Changing a script must never reuse the previous page's audio.
                        speech_identity=dict(text=slide['narration'],segments=[s['narration'] for s in slide.get('flow',[])])
                        audio_path=directory/'audio'/(slide['id']+'-'+digest(speech_identity)[:12]+'.wav')
                        if slide.get('flow'):
                            fragments=[];cues=[];elapsed=0
                            speech_dir=checkpoint.parent/'speech';speech_dir.mkdir(exist_ok=True)
                            for node,step in enumerate(slide['flow']):
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
                duration=sum(s.get('duration_seconds',0) for s in slides)
                if voice_mode!='qwen' or lesson.get('complexity')!='simple' or duration<=120:
                    break
                if timing_attempt==2:
                    raise GenerationError('lecture_too_long',f'简单题语音仍为 {duration:.0f} 秒，超过两分钟；已保留检查点，未发布超长课件。')
                self.store.progress(item['id'],'精简讲稿并重新复核 · 控制在两分钟内',72)
                target=max(160,int(sum(len(p['narration']) for p in lesson['pages'])*100/duration))
                revised,review=await self.qwen.lesson(question,context['resources'],options,
                    lambda stage,value:self.store.progress(item['id'],stage,value),
                    feedback=f'此题必须保持 simple。上一版实际语音 {duration:.0f} 秒超过120秒。台词总计压缩到 {target} 字符以内，保留推理、图文对应和完整阅读区。')
                if revised.get('complexity')!='simple':
                    raise GenerationError('lecture_complexity_changed','精简讲稿时错误改变题目复杂度，未发布结果。')
                saved.update(lesson=revised,review=review)
                atomic_json(checkpoint,saved)
            # Include only the final script's audio in the downloadable package.
            used_audio={s.get('audio_file') for s in slides}
            for old in (directory/'audio').glob('*.wav'):
                if 'audio/'+old.name not in used_audio:
                    old.unlink()
            self.store.progress(item['id'],'装配 HTML、PPTX 与讲解包',92)
            manifest=dict(schema_version='1.1',package_id=saved['package_id'],fingerprint=fingerprint,
                created_at=now(),question=question.model_dump(),title=lesson['title'],objective=lesson['objective'],
                slide_count=len(slides),mode=options.mode,review=saved['review'],voice_mode=voice_mode,
                reading={key:lesson[key] for key in ('knowledge_points','solution_steps','answer','pitfalls','summary')},
                quality=dict(complexity=lesson.get('complexity','sample'),page_limit=5 if lesson.get('complexity')=='simple' else 10,
                    page_count=len(slides),diagram_count=sum(bool(s.get('diagram')) for s in slides),
                    audio_duration_seconds=round(duration,2) if voice_mode=='qwen' else None,
                    duration_limit_seconds=120 if lesson.get('complexity')=='simple' else None),
                resources=[{k:v for k,v in r.items() if k not in {'content','answer','analysis'}} for r in context['resources']],
                variants=lesson['variants'],files=['index.html','courseware.pptx','slides.json','narration.json','manifest.json','package.zip'])
            if self.cfg.s3_bucket:
                manifest['object_prefix']=self.cfg.s3_prefix.strip('/')+'/'+digest(question.question_id)[:16]+'/'+manifest['package_id']
            await asyncio.to_thread(write_package,directory,manifest,slides)
            if self.cfg.s3_bucket:
                self.store.progress(item['id'],'保存到对象存储',97)
                await asyncio.to_thread(self.upload,directory,manifest['object_prefix'])
            self.store.save_package(manifest)
            self.store.finish(item['id'],package_id=manifest['package_id'])

    def upload(self,directory,prefix):
        import boto3
        client=boto3.client('s3',endpoint_url=self.cfg.s3_endpoint_url or None,region_name=self.cfg.s3_region,
            aws_access_key_id=self.cfg.s3_access_key_id,aws_secret_access_key=self.cfg.s3_secret_access_key)
        import mimetypes
        for path in directory.rglob('*'):
            if path.is_file():
                client.upload_file(str(path),self.cfg.s3_bucket,prefix+'/'+path.relative_to(directory).as_posix(),
                    ExtraArgs={'ContentType':mimetypes.guess_type(path.name)[0] or 'application/octet-stream'})

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
