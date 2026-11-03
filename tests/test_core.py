import asyncio
import copy
import json
import zipfile

import pytest

from app.config import Settings
from app.engine import Engine, signed_token
from app.exporters import paginate
from app.kb import document_chunks, phase_for
from app.models import JobRequest
from app.providers import GenerationError, verify_variants
from app.samples import SAMPLES
from app.store import Conflict, Store


def setup(tmp_path):
    cfg=Settings(_env_file=None,data_dir=tmp_path,dashscope_api_key='',vector_database_url='',exam_db_host='',tts_mode='browser')
    store=Store(tmp_path/'test.sqlite')
    return store,Engine(store,cfg),cfg


def request(question=None,**options):
    return JobRequest(assignment_id='assignment-1',questions=[question or SAMPLES[0]['question']],options=dict(mode='sample',**options))


def test_idempotency_conflict_and_assignment_scope(tmp_path):
    store,_,_=setup(tmp_path)
    first,replay=store.create_job(request(),'submission')
    assert not replay
    assert store.create_job(request(),'submission')==(first,True)
    changed=request(); changed.questions[0].stem='Different question'
    with pytest.raises(Conflict):store.create_job(changed,'submission')
    assert len(store.list_jobs('assignment-1'))==1
    assert store.list_jobs('different-assignment')==[]


@pytest.mark.asyncio
async def test_package_reuse_and_force_preserve_previous_version(tmp_path):
    store,engine,_=setup(tmp_path)
    first,_=store.create_job(request())
    await engine.generate(store.claim())
    item=store.job(first)['items'][0];assert item['status']=='completed'
    package=tmp_path/'packages'/item['package_id']
    with zipfile.ZipFile(package/'package.zip') as archive:
        assert {'index.html','courseware.pptx','manifest.json','slides.json','narration.json'}<=set(archive.namelist())
        html=archive.read('index.html').decode('utf-8')
        assert 'data:font/woff2;base64,' in html and '/*PLAYER_JS*/' not in html
    with zipfile.ZipFile(package/'courseware.pptx') as archive:
        assert 3<=len([n for n in archive.namelist() if n.startswith('ppt/slides/slide') and n.endswith('.xml')])<=5
        assert any(n.startswith('ppt/notesSlides/notesSlide') for n in archive.namelist())
    manifest=json.loads((package/'manifest.json').read_text(encoding='utf-8'))
    assert manifest['reading']['solution_steps']==SAMPLES[0]['lesson']['solution_steps']
    assert manifest['reading']['knowledge_points']==SAMPLES[0]['lesson']['knowledge_points']
    assert manifest['variants']==SAMPLES[0]['lesson']['variants']
    second,_=store.create_job(request())
    await engine.generate(store.claim())
    reused=store.job(second)['items'][0]
    assert reused['reused'] and reused['package_id']==item['package_id']
    third,_=store.create_job(request(force=True))
    await engine.generate(store.claim())
    assert store.job(third)['items'][0]['package_id']!=item['package_id']
    assert (package/'index.html').exists()


def test_partial_failure_retry_recovery_and_callback(tmp_path):
    store,_,_=setup(tmp_path)
    req=request(); req.questions.append(type(req.questions[0]).model_validate(SAMPLES[1]['question']))
    req.callback_url='https://callback.example/complete'
    job_id,_=store.create_job(req)
    first=store.claim();second=store.claim()
    store.finish(first['id'],package_id='valid-result')
    store.finish(second['id'],error={'code':'timeout','message':'timeout'})
    job=store.job(job_id);assert job['status']=='partial_failed' and len(job['callbacks'])==1
    store.retry(job_id)
    retried=store.claim();assert retried['id']==second['id']
    assert store.job(job_id)['items'][0]['status']=='completed'
    store.recover();assert store.claim()['id']==second['id']
    store.finish(second['id'],package_id='new-result')
    assert store.job(job_id)['status']=='completed'
    assert len(store.job(job_id)['callbacks'])==2


def test_chunking_math_and_shanghai_grade_boundaries():
    text='原题\n'+'这是长内容。'*200+'\n$$x=\\frac{-b+\\sqrt{b^2-4ac}}{2a}$$\n末尾'
    assert ''.join(paginate(text))==text
    chunks=document_chunks(('甲'*1200+'乙'*800).encode(),'.txt')
    assert chunks[0]['metadata']['char_end']>chunks[1]['metadata']['char_start']
    assert chunks[-1]['content'].endswith('乙'*10)
    assert phase_for('六年级','上海')=='junior'
    assert phase_for('六年级','北京')=='primary'


def test_exam_structured_stem_preserves_subquestions_and_inequalities():
    from app.exam import parse_stem,plain
    body=dict(schema='exam.question.stem.v2',stem='已知 $-1<x<2$。',options=[dict(label='A',content='$x<1$')],
              subQuestions=[dict(label='1',stem='求 $x$ 的范围。',answer='$-1<x<2$',analysis='保留不等号')])
    parsed=parse_stem(json.dumps(body,ensure_ascii=False))
    assert parsed['stem']=='已知 $-1<x<2$。'
    assert parsed['options']==['A. $x<1$']
    assert parsed['sub_questions'][0]['answer']=='$-1<x<2$'
    assert not parsed['requires_images']
    assert plain('求 $a<b<c$。')[0]=='求 $a<b<c$。'
    assert plain('<p>求 $a<b<c$。</p>')[0]=='求 $a<b<c$。'
    assert parse_stem('<p>看图回答</p><img src="https://example.com/a.png">')['requires_images']
    body['stem']=''
    assert parse_stem(json.dumps(body))['stem']=='以下为题库小题（原题无共用题干）：'


def test_signed_links_bound_to_package_and_expiration(tmp_path):
    _,_,cfg=setup(tmp_path);cfg.api_key='test-service-key'
    assert signed_token(cfg,'first',1)!=signed_token(cfg,'second',1)
    assert signed_token(cfg,'first',1)!=signed_token(cfg,'first',2)


@pytest.mark.asyncio
async def test_sample_cannot_silently_answer_changed_question(tmp_path):
    store,engine,_=setup(tmp_path)
    q=copy.deepcopy(SAMPLES[0]['question']);q['stem']='请计算另外一道题：x²=16。'
    store.create_job(request(q))
    with pytest.raises(ValueError,match='样例模式'):
        await engine.generate(store.claim())
    assert not list((tmp_path/'packages').glob('*')) if (tmp_path/'packages').exists() else True


@pytest.mark.asyncio
async def test_resource_variant_cannot_invent_link():
    from app.models import Lesson
    lesson=Lesson.model_validate(SAMPLES[0]['lesson'])
    lesson.variants[0].source='resource';lesson.variants[0].question_id='made-up-id'
    with pytest.raises(ValueError,match='实际提供'):
        verify_variants(lesson,[])


def test_html_embedded_question_cannot_expand_template_or_script():
    from app.exporters import html_package
    marker='/*PLAYER_JS*/ </script><script>alert(1)</script>'
    document=html_package({'title':marker},[])
    assert marker not in document
    assert '"title":"/*PLAYER_JS*/ \\u003c/script>' in document
    assert document.count('const {manifest,slides}=window.COURSEWARE;')==1
