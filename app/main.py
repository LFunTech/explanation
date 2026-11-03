import asyncio
import base64
import hashlib
import hmac
import io
import json
import mimetypes
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from .config import ROOT, Settings
from .engine import Engine, package_links, signed_token
from .exam import Exam
from .kb import KnowledgeBase, document_chunks
from .models import JobRequest, ResourceImport
from .providers import GenerationError
from .samples import SAMPLES, seed_resources
from .store import Conflict, Store, digest


def normalize_image(value):
    if not re.match(r'^data:image/(png|jpeg|webp);base64,',value):
        raise ValueError('图片须为上传接口返回的 data URL，支持 PNG、JPEG 和 WebP')
    try:
        raw=base64.b64decode(value.split(',',1)[1],validate=True)
        if len(raw)>8*1024*1024:
            raise ValueError('单图不能超过 8 MB')
        with Image.open(io.BytesIO(raw)) as image:
            if image.width*image.height>20_000_000:
                raise ValueError('图片像素过大，请压缩后上传')
            image.load()
            # 统一栅格编码，移除元数据，不接收 SVG 或脚本。
            image.thumbnail((2200,2200))
            output=io.BytesIO(); image.convert('RGB').save(output,format='JPEG',quality=88)
        return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
    except (OSError,Image.DecompressionBombError) as error:
        raise ValueError('图片内容无法解码') from error


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
                    'variants':manifest['variants'],'mode':manifest['mode']}
        return job

    @app.post('/api/jobs',status_code=202)
    def create_job(body:JobRequest,idempotency_key:str|None=Header(default=None,max_length=200)):
        if body.callback_url and (not cfg.callback_secret or body.callback_url not in cfg.callback_allowed_urls.split(',')):
            raise HTTPException(422,'回调地址须由服务端配置到 CALLBACK_ALLOWED_URLS，且须配置签名密钥')
        try:
            for question in body.questions:
                question.images=[normalize_image(i) for i in question.images]
                if question.requires_images and not question.images:
                    raise ValueError('这道题含图片附件，请先上传原题图片，避免缺图生成错误讲解。')
            job_id,reused=store.create_job(body,idempotency_key)
        except ValueError as error:
            if isinstance(error,Conflict):
                raise
            raise HTTPException(422,str(error)) from error
        return {**project_job(store.job(job_id)),'idempotent_replay':reused}

    @app.get('/api/jobs')
    def jobs(assignment_id:str|None=None):
        return [project_job(j) for j in store.list_jobs(assignment_id)]

    @app.get('/api/jobs/{job_id}')
    def job(job_id:str):
        result=store.job(job_id)
        if not result:
            raise HTTPException(404,'任务不存在')
        return project_job(result)

    @app.post('/api/jobs/{job_id}/retry',status_code=202)
    def retry(job_id:str):
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

    @app.get('/api/exam/questions')
    async def exam_questions(subject:str='数学',grade:str='九年级',query:str=''):
        if len(query)>200:
            raise HTTPException(422,'搜索词过长')
        return await asyncio.to_thread(Exam(cfg).search,subject,grade,query)

    @app.get('/api/kb/stats')
    async def kb_stats():
        if not cfg.vector_database_url:
            return dict(configured=False,task=kb_state)
        return dict(configured=True,**await asyncio.to_thread(kb.stats),task=kb_state)

    @app.get('/api/kb/graph')
    async def graph(subject:str='',query:str=''):
        return await asyncio.to_thread(kb.graph,subject,query[:200])

    @app.get('/api/kb/search')
    async def search_kb(query:str,subject:str='数学',grade:str='九年级',region:str='上海'):
        if not query.strip() or len(query)>2000:
            raise HTTPException(422,'搜索词须为 1—2000 字符')
        return await kb.search(query,subject,grade,region,include_graph=True)

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
            meta=json.loads(metadata)
            if not isinstance(meta,dict) or not all(isinstance(meta.get(k),str) and meta[k].strip() for k in ['title','subject','source','version']):
                raise ValueError('元数据须包含 title、subject、source、version')
            if meta.get('source_type') not in {'curriculum','textbook','reference'}:
                raise ValueError('source_type 须为 curriculum、textbook 或 reference')
            if not isinstance(meta.get('grades',[]),list) or not all(isinstance(x,str) for x in meta.get('grades',[])):
                raise ValueError('grades 须为年级字符串数组')
            raw=await file.read(25*1024*1024+1)
            if len(raw)>25*1024*1024:
                raise ValueError('文档不能超过 25 MB')
            chunks=await asyncio.to_thread(document_chunks,raw,Path(file.filename or '').suffix.lower())
            document_id='doc-'+hashlib.sha256(raw+json.dumps(meta,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:24]
            await asyncio.to_thread(kb.put_document,document_id,meta['title'],meta['source_type'],meta,chunks)
            return dict(document_id=document_id,chunks=len(chunks),status='pending_embedding')
        except (ValueError,TypeError) as error:
            raise HTTPException(422,str(error))

    @app.get('/packages/{package_id}/{filename:path}')
    def package(package_id:str,filename:str,request:Request,token:str='',expires:int=0):
        if not re.fullmatch(r'[a-f0-9]{32}',package_id) or not store.package(package_id):
            raise HTTPException(404,'讲解包不存在或尚未完成')
        if cfg.api_key and not authorized(request):
            if expires<time.time() or not hmac.compare_digest(token,signed_token(cfg,package_id,expires)):
                raise HTTPException(403,'访问链接已过期，请重新查询生成结果')
        allowed={'index.html','manifest.json','slides.json','narration.json','courseware.pptx','package.zip'}
        if filename not in allowed and not re.fullmatch(r'audio/page-\d{3}\.wav',filename):
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
