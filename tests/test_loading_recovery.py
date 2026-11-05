import base64
import copy
import io
import json
import re
from contextlib import contextmanager

import httpx
import psycopg
import pytest
from PIL import Image

from app.config import Settings
from app.exam import Exam
from app.exam_cache import ExamCache
from app.exporters import make_slides,write_package
from app.main import create_app
from app.providers import GenerationError
from tests.test_core import setup,request
from tests.test_illustrated_lessons import lesson


def image():
    raw=io.BytesIO();Image.new('RGB',(100,30),'white').save(raw,format='PNG')
    return 'data:image/png;base64,'+base64.b64encode(raw.getvalue()).decode()


def test_early_grade_figures_reuse_the_original_instead_of_number_lines():
    draft=lesson()
    question=dict(grades=['一年级'],images=[image()])
    slides=make_slides(question,draft)
    assert slides[1]['diagram']['kind']=='source_image'
    assert slides[1]['diagram']['png']==question['images'][0]
    assert slides[1]['diagram']['source_image_index']==0
    assert 'diagram' not in make_slides(dict(grades=['一年级']),draft)[1]
    assert make_slides(dict(grades=['九年级']),draft)[1]['diagram']['kind']=='number_line'


def test_source_image_reference_works_for_older_grades_and_first_reading_page():
    draft=lesson();draft['pages'][0]['kind']='知识';draft['pages'][0]['title']='读题与图像观察'
    draft['pages'][1]['source_image_index']=1;draft['pages'][1]['source_image_caption']='比较原图的形状。'
    question=dict(grades=['九年级'],images=[image(),image()])
    slides=make_slides(question,draft)
    assert slides[0]['original'] and slides[0]['kind']=='原题'
    assert slides[1]['diagram']['source_image_index']==1
    assert '比较原图的形状' in slides[1]['diagram']['caption']


def test_exam_reconnects_then_reuses_a_read_only_connection(monkeypatch):
    Exam.close_connections();calls=[]
    class DB:
        closed=False
        def execute(self,sql):assert sql=='SELECT 1'
        @contextmanager
        def transaction(self):yield
        def close(self):self.closed=True
    db=DB()
    def connect(**kw):
        calls.append(kw)
        if len(calls)==1:raise psycopg.OperationalError('connection timeout expired')
        return db
    monkeypatch.setattr('app.exam.psycopg.connect',connect)
    monkeypatch.setattr('app.exam.time.sleep',lambda _:None)
    cfg=Settings(_env_file=None,exam_db_host='test')
    with Exam(cfg).connect() as first:assert first is db
    with Exam(cfg).connect() as second:assert second is db
    assert len(calls)==2
    assert 'default_transaction_read_only=on' in calls[1]['options']
    Exam.close_connections();assert db.closed


@pytest.mark.asyncio
async def test_exam_failure_returns_actionable_json_instead_of_500(tmp_path,monkeypatch):
    Exam.close_connections()
    def unavailable(**kw):raise psycopg.OperationalError('sensitive connection internals')
    monkeypatch.setattr('app.exam.psycopg.connect',unavailable)
    monkeypatch.setattr('app.exam.time.sleep',lambda _:None)
    cfg=Settings(_env_file=None,data_dir=tmp_path,vector_database_url='',exam_db_host='test',api_key='')
    app=create_app(cfg)
    body=dict(assignment_id='test',questions=[dict(question_id='exam:public:3bc2e228-94c6-4a7f-8fee-5a16661f27c6',
        source='exam/public/question/3bc2e228-94c6-4a7f-8fee-5a16661f27c6',stem='观察原图。',grades=['七年级','八年级','九年级'])])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        for response in [await client.get('/api/exam/questions/random',params=[('grades','七年级'),('grades','八年级')]),
                         await client.post('/api/jobs?single_question=true',json=body)]:
            assert response.status_code==503
            assert response.json()['code']=='exam_unavailable'
            assert 'sensitive' not in response.text
    Exam.close_connections()


@pytest.mark.asyncio
async def test_online_page_loads_audio_only_on_play_and_serves_hashed_audio(tmp_path):
    _,_,cfg=setup(tmp_path);app=create_app(cfg);store=app.state.store;engine=app.state.engine
    job,_=store.create_job(request(voice=False));await engine.generate(store.claim())
    package_id=store.job(job)['items'][0]['package_id'];directory=cfg.data_dir/'packages'/package_id
    slides=json.loads((directory/'slides.json').read_text(encoding='utf-8'))
    name='audio/page-001-0123456789ab.wav';(directory/'audio').mkdir(exist_ok=True)
    (directory/name).write_bytes(b'RIFFtest-audio')
    sentinel='data:audio/wav;base64,'+'A'*50000
    slides[0].update(audio_file=name,audio=sentinel)
    write_package(directory,store.package(package_id),slides)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        online=await client.get(f'/packages/{package_id}/online.html')
        offline=await client.get(f'/packages/{package_id}/index.html')
        standalone=await client.get(f'/packages/{package_id}/player.html')
        player=await client.get('/player/'+package_id)
        config=json.loads(re.search(r'window.COURSEWARE_HOST=(.*?);',player.text).group(1))
        assert sentinel not in online.text and sentinel in offline.text
        assert sentinel in standalone.text
        assert config['contentUrl'].endswith('/online.html')
        assert config['audio'][slides[0]['id']].endswith(name)
        assert (await client.get(config['audio'][slides[0]['id']])).content==b'RIFFtest-audio'


@pytest.mark.asyncio
async def test_disconnected_random_picker_keeps_real_cached_questions_and_grade_filter(tmp_path,monkeypatch):
    cfg=Settings(_env_file=None,data_dir=tmp_path,vector_database_url='',exam_db_host='test',api_key='')
    rows=[dict(id=str(i),grades=[grade],question=dict(subject='数学',region='上海')) for i,grade in enumerate(['一年级','七年级','八年级','九年级'])]
    ExamCache(cfg).put(rows)
    def unavailable(*args,**kwargs):raise GenerationError('exam_unavailable','连接中断')
    monkeypatch.setattr(Exam,'random_questions',unavailable)
    app=create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.get('/api/exam/questions/random',params=[('grades','七年级'),('grades','八年级'),('region','北京')])
    assert response.status_code==200
    assert {r['grades'][0] for r in response.json()}=={'七年级','八年级'}
    assert all(r['cached'] and r['question']['region']=='北京' for r in response.json())
