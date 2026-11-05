import asyncio
import base64
import hashlib
import hmac
import json
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import ROOT, Settings
from .engine import Engine, package_links, signed_token
from .exporters import html_player
from .exam import Exam, apply_public_grades
from .exam_cache import ExamCache
from .grades import normalize_grades
from .images import normalize_image
from .kb import KnowledgeBase, document_chunks
from .models import CoursewareRequest, CoursewareUpdate, JobRequest, ResourceImport, Question
from .question_layout import question_layout
from .providers import GenerationError
from .samples import SAMPLES, seed_resources
from .store import Conflict, Store, digest
from .storage import object_urls as storage_object_urls
from .scope import TeachingScope, validate_document_metadata
from .target_analysis import CurriculumAnalysis, analyze_targets, while_connected


def create_app(settings=None):
    cfg=settings or Settings()
    cfg.validate_runtime()
    store=Store(cfg.data_dir/'app.sqlite3')
    store.import_resources(seed_resources(),seed=True)
    engine=Engine(store,cfg)
    kb=KnowledgeBase(cfg)
    background=set()
    kb_state=dict(status='idle',processed=0,error=None)

    @asynccontextmanager
    async def lifespan(app):
        from .process_lock import acquire
        lock=acquire(cfg.data_dir/'.service.lock')
        # 单进程运行；服务重启后按持久化队列恢复，不重放已完成题目。
        if cfg.vector_database_url:
            await asyncio.to_thread(kb.initialize)
        engine.start()
        yield
        for task in background:
            task.cancel()
        await asyncio.gather(*background,return_exceptions=True)
        await engine.close()
        Exam.close_connections()
        lock.close()

    app=FastAPI(title='题解工坊 · 课件生成服务',version='0.1.0',lifespan=lifespan)
    app.state.store,app.state.engine=store,engine

    def authorized(request):
        if not cfg.api_key:
            return True
        header=request.headers.get('X-API-Key','')
        if header and hmac.compare_digest(header,cfg.api_key):
            return True
        cookie=request.cookies.get('explanation_session','')
        try:
            stamp,signature=cookie.split(':')
            expected=hmac.new(cfg.api_key.encode(),('session:'+stamp).encode(),hashlib.sha256).hexdigest()
            return int(stamp)>time.time() and hmac.compare_digest(signature,expected)
        except ValueError:
            return False

    @app.middleware('http')
    async def auth(request,call_next):
        if int(request.headers.get('content-length','0') or 0)>30*1024*1024:
            return JSONResponse({'detail':'请求不能超过 30 MB'},status_code=413)
        if request.url.path.startswith('/api/') and request.url.path not in {'/api/config','/api/session'} and not authorized(request):
            return JSONResponse({'detail':'请先登录或提供 X-API-Key'},status_code=401)
        if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('origin'):
            # Cookie 登录的写操作只接受同源请求；作业 API 无 Origin。
            from urllib.parse import urlsplit
            if urlsplit(request.headers['origin']).netloc != request.headers.get('host'):
                return JSONResponse({'detail':'不接受跨域写入'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        if request.url.path.startswith('/api/'):
            response.headers['Cache-Control']='no-store'
        elif request.url.path=='/' or (request.url.path.startswith('/static/') and '/vendor/' not in request.url.path):
            # Revalidate app HTML/modules on navigation after an update.
            response.headers['Cache-Control']='no-cache'
        return response

    @app.exception_handler(GenerationError)
    async def generation_error(request,error):
        return JSONResponse({'detail':str(error),'code':error.code},status_code=503)

    @app.exception_handler(Conflict)
    async def conflict(request,error):
        return JSONResponse({'detail':str(error)},status_code=409)

    @app.get('/health')
    def health():
        return dict(status='ok')

    @app.get('/api/config')
    def config(request:Request):
        return dict(qwen_configured=bool(cfg.dashscope_api_key),model=cfg.qwen_model,tts_mode=cfg.tts_mode,
            independent_review=cfg.qwen_independent_review,
            auth_required=bool(cfg.api_key),authenticated=authorized(request),region='上海',
            exam_configured=bool(cfg.exam_db_host),knowledge_configured=bool(cfg.vector_database_url))

    class Login(BaseModel):
        key:str=Field(max_length=500)

    @app.post('/api/session')
    def login(body:Login):
        if cfg.api_key and not hmac.compare_digest(body.key,cfg.api_key):
            raise HTTPException(401,'访问密钥不正确')
        expires=str(int(time.time())+8*3600)
        signature=hmac.new(cfg.api_key.encode(),('session:'+expires).encode(),hashlib.sha256).hexdigest()
        response=JSONResponse(dict(ok=True))
        response.set_cookie('explanation_session',expires+':'+signature,httponly=True,samesite='strict',
                            secure=cfg.public_base_url.startswith('https:'),max_age=8*3600)
        return response

    @app.get('/api/samples')
    def samples():
        return [dict(question=s['question'],title=s['lesson']['title']) for s in SAMPLES]

    def project_job(job):
        for item in job['items']:
            if item['package_id']:
                manifest=store.package(item['package_id'])
                item['result']={**package_links(cfg,item['package_id']),
                    'slide_count':manifest['slide_count'],'title':manifest['title'],'voice_mode':manifest['voice_mode'],
                    'variants':manifest['variants'],'mode':manifest['mode'],
                    'storage':manifest.get('storage'),
                    'teaching_target':manifest.get('teaching_target'),
                    'review_type':manifest.get('review',{}).get('type')}
        return job

    def project_courseware(job, reused=False):
        job=project_job(job);item=job['items'][0]
        return {**job,'courseware_id':item['id'],'job_id':job['id'],
            'status_url':f'/api/courseware/{item["id"]}',
            'stage':item['stage'],'error':item['error'],
            'generation_requirements':job['request']['options'].get('generation_requirements',''),
            'result':item.get('result') if item['status']=='completed' else None,
            'previous_result':item.get('result') if item['status']!='completed' else None,
            'idempotent_replay':reused}

    @app.post('/api/courseware',status_code=202)
    def create_courseware(body:CoursewareRequest,idempotency_key:str|None=Header(default=None,max_length=200)):
        request_hash=digest(dict(endpoint='courseware',request=body.model_dump()))
        replay=store.lesson_replay(idempotency_key,request_hash)
        if replay:return project_courseware(store.job(replay),True)
        try:
            question=Exam(cfg).question(body.question_id)
            question.target_grade=body.target_grade
            question.target_semester=body.target_semester
            question.target_curriculum=body.target_curriculum
            from .teaching import validate_target_curriculum
            validate_target_curriculum(question)
            if question.requires_images:
                question.images=Exam(cfg).images(question.question_id)['images']
                if not question.images:
                    raise HTTPException(422,'这道题缺少原图，请先补齐题库附件再生成。')
            question.images=[normalize_image(image) for image in question.images]
        except GenerationError as error:
            if error.code=='exam_question_missing':raise HTTPException(404,str(error)) from None
            if error.code in {'exam_image_missing','exam_image_count'}:raise HTTPException(422,str(error)) from None
            raise
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
        title=body.title or ((body.target_curriculum[0].knowledge_point if body.target_curriculum else
                            next(iter(question.knowledge_points),question.subject))[:140]+' · 题目讲解')
        if 'generation_requirements' in body.model_fields_set:
            body.options.generation_requirements=body.generation_requirements
        request=JobRequest(assignment_id=body.assignment_id or 'courseware-'+body.question_id.removeprefix('exam:public:'),
                          title=title,questions=[question],options=body.options)
        job_id,reused=store.create_lesson(request,idempotency_key,request_hash=request_hash)
        return project_courseware(store.job(job_id),reused)

    @app.get('/api/courseware')
    def courseware_list(page:int=Query(default=1,ge=1),page_size:int=Query(default=20,ge=1,le=100),
                        query:str=Query(default='',max_length=500),grade:str=Query(default='',max_length=30),
                        knowledge_point:str=Query(default='',max_length=500),
                        status:str=Query(default='all',pattern='^(all|active|pending|generating|completed|failed)$')):
        result=store.lesson_page(page,page_size,query=query,grade=grade,knowledge_point=knowledge_point,status=status)
        result['items']=[project_job(job) for job in result['items']]
        return result

    @app.get('/api/courseware/{courseware_id}')
    def get_courseware(courseware_id:str):
        job=store.courseware_job(courseware_id)
        if not job:raise HTTPException(404,'课件不存在')
        return project_courseware(job)

    @app.patch('/api/courseware/{courseware_id}',status_code=202)
    def update_courseware(courseware_id:str,body:CoursewareUpdate,idempotency_key:str|None=Header(default=None,max_length=200)):
        try:
            job_id,reused=store.update_courseware(courseware_id,body.model_dump(exclude_unset=True),idempotency_key)
        except KeyError:raise HTTPException(404,'课件不存在') from None
        except Conflict:raise
        except ValueError as error:raise HTTPException(422,str(error)) from None
        return project_courseware(store.job(job_id),reused)

    @app.post('/api/jobs',status_code=202)
    def create_job(body:JobRequest,idempotency_key:str|None=Header(default=None,max_length=200),single_question:bool=False):
        if body.callback_url and (not cfg.callback_secret or body.callback_url not in cfg.callback_allowed_urls.split(',')):
            raise HTTPException(422,'回调地址须由服务端配置到 CALLBACK_ALLOWED_URLS，且须配置签名密钥')
        try:
            source_questions=[q for q in body.questions if q.question_id.startswith('exam:public:')
                and q.source=='exam/public/question/'+q.question_id.removeprefix('exam:public:')]
            if cfg.exam_db_host and source_questions:
                metadata=Exam(cfg).question_metadata([q.question_id for q in source_questions])
                for question in source_questions:
                    if question.question_id in metadata:
                        apply_public_grades(question,metadata[question.question_id]['grades'],
                            preserve_selection='grades' in question.model_fields_set)
            for question in body.questions:
                if (question.requires_images and not question.images and question.question_id.startswith('exam:public:')
                        and question.source=='exam/public/question/'+question.question_id.removeprefix('exam:public:')):
                    question.images=Exam(cfg).images(question.question_id)['images']
                question.images=[normalize_image(i) for i in question.images]
                if question.requires_images and not question.images:
                    raise ValueError('这道题含图片附件，请先上传原题图片，避免缺图生成错误讲解。')
                if not question.target_grade:
                    raise ValueError('请填写目标年级；原题年级仅作为来源信息，不会自动用于讲解。')
                from .teaching import validate_target_curriculum
                validate_target_curriculum(question)
            job_id,reused=(store.create_lesson if single_question else store.create_job)(body,idempotency_key)
        except ValueError as error:
            if isinstance(error,Conflict):
                raise
            raise HTTPException(422,str(error)) from error
        if single_question:return project_courseware(store.job(job_id),reused)
        return {**project_job(store.job(job_id)),'idempotent_replay':reused}

    @app.get('/api/jobs')
    def jobs(assignment_id:str|None=None,unique_questions:bool=False):
        return [project_job(j) for j in (store.list_lessons() if unique_questions else store.list_jobs(assignment_id))]

    @app.get('/api/jobs/{job_id}')
    def job(job_id:str):
        result=store.job(job_id)
        if not result:
            raise HTTPException(404,'任务不存在')
        return project_job(result)

    @app.post('/api/jobs/{job_id}/retry',status_code=202)
    def retry(job_id:str):
        previous=store.job(job_id)
        if previous and any(not i['question'].get('target_grade') for i in previous['items'] if i['status']=='failed'):
            raise HTTPException(422,'请先补充目标年级，再重新生成本题。')
        try:
            store.retry(job_id)
        except KeyError:
            raise HTTPException(404,'任务不存在')
        return project_job(store.job(job_id))

    @app.get('/api/assignments/{assignment_id}')
    def assignment(assignment_id:str):
        result=[project_job(j) for j in store.list_jobs(assignment_id)]
        return dict(assignment_id=assignment_id,status='not_generated' if not result else result[0]['status'],jobs=result)

    @app.post('/api/callbacks/{event_id}/retry',status_code=202)
    def retry_callback(event_id:str):
        with store.connect() as db:
            row=db.execute('SELECT status FROM callbacks WHERE id=?',(event_id,)).fetchone()
            if not row:
                raise HTTPException(404,'通知不存在')
            if row['status']!='failed':
                raise Conflict('仅失败通知可重试')
            db.execute("UPDATE callbacks SET status='pending',attempts=0,next_at=0,error=NULL WHERE id=?",(event_id,))
        return dict(ok=True)

    @app.get('/api/resources')
    def resources():
        return store.resources()

    @app.post('/api/resources/import')
    def import_resources(body:ResourceImport):
        store.import_resources([r.model_dump() for r in body.resources])
        return dict(imported=len(body.resources))

    @app.post('/api/images')
    async def upload_image(file:UploadFile=File(...)):
        raw=await file.read(8*1024*1024+1)
        try:
            return dict(data_url=normalize_image('data:'+str(file.content_type)+';base64,'+base64.b64encode(raw).decode()))
        except ValueError as error:
            raise HTTPException(422,str(error))

    @app.post('/api/questions/layout')
    def preview_question(body:Question):
        return question_layout(body.model_dump())

    @app.get('/api/exam/questions')
    async def exam_questions(subject:str='数学',grade:str='',query:str='',region:str='上海',grades:list[str]|None=Query(default=None)):
        if len(query)>200:
            raise HTTPException(422,'搜索词过长')
        rows = await asyncio.to_thread(Exam(cfg).search,subject,grade,query,region=region,grades=grades)
        return [{**row, 'layout':question_layout(row['question'])} for row in rows]

    @app.get('/api/exam/questions/{question_id}/images')
    async def exam_images(question_id:UUID):
        return await asyncio.to_thread(Exam(cfg).images,str(question_id))

    @app.get('/api/exam/questions/random')
    async def random_questions(subject:str=Query(default='数学',max_length=30),
            grade:str=Query(default='',max_length=30),region:str=Query(default='上海',max_length=40),
            limit:int=Query(default=3,ge=1,le=6),exclude:str=Query(default='',max_length=2000),grades:list[str]|None=Query(default=None)):
        excluded=[value.strip() for value in exclude.split(',') if value.strip()]
        if len(excluded)>30:
            raise HTTPException(422,'最多排除 30 道题')
        def cached():
            return ExamCache(cfg).random_questions(subject,normalize_grades(grades if grades is not None else [grade]),region,limit,excluded)
        task=asyncio.create_task(asyncio.to_thread(Exam(cfg).random_questions,subject,grade,region,limit,excluded,grades=grades))
        background.add(task)
        def completed(done):
            background.discard(done)
            if not done.cancelled():done.exception()
        task.add_done_callback(completed)
        try:
            try:return await asyncio.wait_for(asyncio.shield(task),timeout=4)
            except asyncio.TimeoutError:
                saved=cached()
                if saved:return saved
                return await task
        except GenerationError as error:
            if error.code=='exam_unavailable':
                saved=cached()
                if saved:return saved
            raise
        except Exception:
            raise HTTPException(503,'暂时无法读取题库，请稍后点击“换一批”重试。') from None

    @app.get('/api/kb/stats')
    async def kb_stats():
        if not cfg.vector_database_url:
            return dict(configured=False,task=kb_state)
        return dict(configured=True,**await asyncio.to_thread(kb.stats),task=kb_state)

    @app.get('/api/kb/graph')
    async def graph(subject:str='',query:str=''):
        return await asyncio.to_thread(kb.graph,subject,query[:200])

    @app.get('/api/teaching-targets')
    async def target_catalog(request:Request,subject:str='数学',target_grade:str='',query:str=Query('',max_length=200),region:str='上海'):
        if not target_grade:
            return dict(items=[],notice='请选择目标年级后查看知识点。')
        if not cfg.vector_database_url:
            return dict(items=[],notice='知识库尚未配置，暂时无法获取库内课标。')
        try:
            points=await asyncio.to_thread(kb.target_curriculum,subject,target_grade,query.strip(),region)
            points=[p for p in points if p.get('grade')==target_grade]
            stages=list(dict.fromkeys(s['stage'] for p in points for s in p.get('curriculum_sources',[]) if s.get('stage')))
            notice=('已按目标年级匹配'+ '、'.join(stages)+'课标原文；学段要求不代表单一年级或学期的教材进度。') if stages else ''
            return dict(items=target_response_points(points,request),notice=notice,
                curriculum_sources=[dict(knowledge_point=p['knowledge_point'],grade=p['grade'],
                    curriculum_requirements=p['curriculum_requirements'],sources=p.get('curriculum_sources',[])) for p in points])
        except Exception:
            return dict(items=[],notice='知识点列表暂时无法读取，请稍后重试。')

    def target_response_points(points,request):
        # One knowledge point / one grade / multiple requirements on every client.
        fields={'knowledge_point','grade','grade_source','curriculum_requirements'}
        return [{k:v for k,v in point.items() if k in fields} for point in points]

    @app.post('/api/teaching-targets/analyze')
    async def analyze_target(question:Question,request:Request):
        if not question.target_grade:
            raise HTTPException(422,'请先选择目标年级，再分析目标课标。')
        async def run():
            references=[]
            if cfg.vector_database_url:
                try:
                    rows=await asyncio.wait_for(asyncio.gather(*[
                        asyncio.to_thread(kb.target_curriculum,question.subject,question.target_grade,name,question.region,24)
                        for name in (question.knowledge_points[:6] or [''])]),timeout=5)
                    references=list({(p['knowledge_point'],p.get('grade','')):p for group in rows for p in group}.values())
                    if not any(p.get('grade') and p.get('curriculum_requirements') for p in references):
                        references=await asyncio.wait_for(asyncio.to_thread(kb.target_curriculum,
                            question.subject,question.target_grade,'',question.region,200),timeout=5)
                except Exception:
                    return dict(**CurriculumAnalysis(note='暂时无法读取本地课标库，未自动填写要求，请稍后重试。').model_dump(),source='knowledge_base')
            result=await analyze_targets(question,cfg,references)
            data=result.model_dump()
            data['target_curriculum']=target_response_points(data['target_curriculum'],request)
            return dict(**data,source='knowledge_base')
        return await while_connected(request,run())

    @app.get('/api/kb/search')
    async def search_kb(query:str,subject:str='数学',grade:str='九年级',region:str='上海',
            curriculum_version:str='',school_system:str='',publisher:str='',textbook_version:str='',volume:str=''):
        if not query.strip() or len(query)>2000:
            raise HTTPException(422,'搜索词须为 1—2000 字符')
        scope=TeachingScope(curriculum_version=curriculum_version,school_system=school_system,publisher=publisher,textbook_version=textbook_version,volume=volume)
        return await kb.search(query,subject,grade,region,include_graph=True,scope=scope.model_dump())

    async def run_kb_task(action):
        kb_state.update(status='running',processed=0,error=None)
        try:
            if action=='sync':
                kb_state['result']=await asyncio.to_thread(Exam(cfg).import_graph,kb)
            else:
                kb_state['result']=await kb.index_pending(100000,lambda count:kb_state.update(processed=count))
            kb_state['status']='completed'
        except asyncio.CancelledError:
            raise
        except Exception as error:
            kb_state.update(status='failed',error=str(error) if isinstance(error,GenerationError) else '知识库任务未完成，请检查连接；已完成批次会保留。')

    @app.post('/api/kb/{action}',status_code=202)
    async def kb_action(action:str):
        if action not in {'sync','index'}:
            raise HTTPException(404,'操作不存在')
        if kb_state['status']=='running':
            raise Conflict('知识库任务正在运行')
        kb_state['status']='running'
        task=asyncio.create_task(run_kb_task(action)); background.add(task); task.add_done_callback(background.discard)
        return dict(status='accepted')

    @app.post('/api/kb/documents/import')
    async def import_document(file:UploadFile=File(...),metadata:str=Form(...)):
        try:
            meta=validate_document_metadata(json.loads(metadata))
            raw=await file.read(25*1024*1024+1)
            if len(raw)>25*1024*1024:
                raise ValueError('文档不能超过 25 MB')
            chunks=await asyncio.to_thread(document_chunks,raw,Path(file.filename or '').suffix.lower())
            document_id='doc-'+hashlib.sha256(raw+json.dumps(meta,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
            await asyncio.to_thread(kb.put_document,document_id,meta['title'],meta['source_type'],meta,chunks)
            return dict(document_id=document_id,chunks=len(chunks),status='pending_embedding')
        except (ValueError,TypeError) as error:
            raise HTTPException(422,str(error))

    def check_package_access(package_id,request,token,expires):
        if not re.fullmatch(r'[a-f0-9]{32}',package_id) or not store.package(package_id):
            raise HTTPException(404,'讲解包不存在或尚未完成')
        if cfg.api_key and not authorized(request):
            if expires<time.time() or not hmac.compare_digest(token,signed_token(cfg,package_id,expires)):
                raise HTTPException(403,'访问链接已过期，请重新查询生成结果')

    def local_content_config(package_id):
        links=package_links(cfg,package_id)
        online=(cfg.data_dir/'packages'/package_id/'online.html').is_file()
        audio={}
        if online:
            narration=json.loads((cfg.data_dir/'packages'/package_id/'narration.json').read_text(encoding='utf-8'))
            audio={s['page_id']:links['html_url'].replace('/index.html','/'+s['audio_file']) for s in narration if s.get('audio_file')}
        return dict(contentUrl=links['content_url'] if online else links['html_url'],audio=audio,motion=True)

    def minio_content_config(package_id):
        storage=store.package(package_id).get('storage')
        if not cfg.s3_bucket or not storage:
            raise HTTPException(409,'课件尚未上传到对象存储，请上传完成后重新打开')
        # The browser loads the remote object itself; the application only signs
        # its URL and provides the independent player interface.
        directory=cfg.data_dir/'packages'/package_id
        filename='online.html' if 'online.html' in store.package(package_id).get('files',[]) else 'index.html'
        narration=json.loads((directory/'narration.json').read_text(encoding='utf-8'))
        urls=storage_object_urls(cfg,storage,[filename,*[s['audio_file'] for s in narration if s.get('audio_file')]])
        audio={s['page_id']:urls[s['audio_file']] for s in narration if s.get('audio_file')}
        return dict(contentUrl=urls[filename],audio=audio,motion=True,fallback=local_content_config(package_id))

    @app.get('/embed/{package_id}/config')
    def embed_config(package_id:str,request:Request,token:str='',expires:int=0,source:str=Query(default='local',pattern='^(local|minio)$')):
        check_package_access(package_id,request,token,expires)
        config=minio_content_config(package_id) if source=='minio' else local_content_config(package_id)
        return JSONResponse(config,headers={'Cache-Control':'no-store'})

    @app.get('/microapp/{package_id}/data')
    def microapp_data(package_id:str,request:Request,token:str='',expires:int=0):
        check_package_access(package_id,request,token,expires)
        from .exporters import presentation_slides
        from .question_layout import question_layout, variant_layout
        manifest=store.package(package_id)
        slides=json.loads((cfg.data_dir/'packages'/package_id/'slides.json').read_text(encoding='utf-8'))
        return JSONResponse(dict(manifest=manifest,slides=presentation_slides(slides),
            questionLayout=question_layout(manifest.get('question',{})),
            variantLayouts=[variant_layout(v) for v in manifest.get('variants',[])],
            audio=local_content_config(package_id)['audio']),headers={'Cache-Control':'no-store'})

    @app.get('/static/microapp-runtime.js')
    def microapp_runtime():
        from .microapp import runtime_module
        return Response(runtime_module(),media_type='text/javascript')

    @app.get('/static/microapp-assets.json')
    def microapp_assets():
        from .microapp import styles, shell_markup
        return dict(shellCss=styles('shell'),contentCss=styles('content'),shellHtml=shell_markup())

    @app.get('/player/{package_id}',response_class=HTMLResponse)
    def player(package_id:str,request:Request,token:str='',expires:int=0):
        check_package_access(package_id,request,token,expires)
        config=local_content_config(package_id)
        return HTMLResponse(html_player(config['contentUrl'],config['audio']),headers={'Cache-Control':'no-store'})

    @app.get('/minio-player/{package_id}',response_class=HTMLResponse)
    def minio_player(package_id:str,request:Request,token:str='',expires:int=0):
        check_package_access(package_id,request,token,expires)
        config=minio_content_config(package_id)
        return HTMLResponse(html_player(config['contentUrl'],config['audio'],fallback=config['fallback']),headers={'Cache-Control':'no-store'})

    @app.get('/minio-package/{package_id}/player')
    def minio_package_player(package_id:str,request:Request,token:str='',expires:int=0):
        check_package_access(package_id,request,token,expires)
        manifest=store.package(package_id)
        if not cfg.s3_bucket or not manifest.get('storage') or 'player.html' not in manifest.get('files',[]):
            raise HTTPException(409,'课件播放文件尚未上传，请上传完成后重新打开')
        url=storage_object_urls(cfg,manifest['storage'],['player.html'])['player.html']
        return RedirectResponse(url,status_code=307,headers={'Cache-Control':'no-store'})

    @app.get('/packages/{package_id}/{filename:path}')
    def package(package_id:str,filename:str,request:Request,token:str='',expires:int=0):
        check_package_access(package_id,request,token,expires)
        allowed={'index.html','online.html','player.html','manifest.json','slides.json','narration.json','courseware.pptx','package.zip'}
        if filename not in allowed and not re.fullmatch(r'audio/page-\d{3}(?:-[a-f0-9]{12})?\.wav',filename):
            raise HTTPException(404,'文件不存在')
        path=cfg.data_dir/'packages'/package_id/filename
        if not path.is_file():
            raise HTTPException(404,'文件不存在')
        download=filename.endswith(('.zip','.pptx'))
        return FileResponse(path,filename=filename if download else None,media_type=mimetypes.guess_type(filename)[0])

    @app.get('/')
    def index():
        return FileResponse(ROOT/'web/index.html')

    app.mount('/static',StaticFiles(directory=ROOT/'web'),name='static')
    return app


app=create_app()
