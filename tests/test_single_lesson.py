import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from app.main import create_app
from app.store import Conflict, Store
from tests.test_core import setup, request
from tests.test_illustrated_lessons import lesson


def test_identical_question_and_targets_create_independent_courseware(tmp_path):
    store,_,_=setup(tmp_path);body=request()
    a,_=store.create_lesson(body,'first');b,_=store.create_lesson(body,'second')
    assert a!=b
    first=store.job(a)['items'][0];second=store.job(b)['items'][0]
    assert first['id']!=second['id']
    assert {store.claim()['job_id'],store.claim()['job_id']}=={a,b}
    store.finish(first['id'],package_id='first-result')
    c,_=store.create_lesson(body)
    assert c not in {a,b}
    assert len(store.list_lessons())==3
    assert store.job(a)['items'][0]['package_id']=='first-result'
    assert store.job(b)['items'][0]['status']=='generating'
    assert store.job(c)['items'][0]['package_id'] is None


def test_parallel_creates_and_idempotent_retries(tmp_path):
    store,_,_=setup(tmp_path);body=request()
    with ThreadPoolExecutor(max_workers=6) as pool:
        replayed=list(pool.map(lambda _:store.create_lesson(body,'same-key'),range(6)))
        independent=list(pool.map(lambda _:store.create_lesson(body),range(6)))
    assert len({id for id,_ in replayed})==1
    assert sum(not replay for _,replay in replayed)==1
    assert len({id for id,_ in independent})==6
    assert len(store.list_lessons())==7
    changed=body.model_copy(deep=True);changed.title='不同请求'
    with pytest.raises(Conflict,match='Idempotency'):store.create_lesson(changed,'same-key')


def test_legacy_duplicate_records_and_results_remain_independent_after_restart(tmp_path):
    store,_,_=setup(tmp_path);body=request()
    old,_=store.create_job(body);old_item=store.job(old)['items'][0]
    store.finish(old_item['id'],package_id='legacy-result')
    with store.connect() as db:db.execute('INSERT INTO lessons VALUES(?,?)',(body.questions[0].question_id,old_item['id']))
    store=Store(store.path)
    new,_=store.create_lesson(body)
    visible={j['id']:j for j in store.list_lessons()}
    assert set(visible)=={old,new}
    assert visible[old]['items'][0]['package_id']=='legacy-result'
    assert visible[new]['items'][0]['package_id'] is None
    store.finish(store.job(new)['items'][0]['id'],error=dict(code='test',message='失败'))
    assert next(j for j in store.list_lessons() if j['id']==new)['items'][0]['package_id'] is None


def test_update_only_changes_selected_courseware_and_preserves_its_previous_result(tmp_path):
    store,_,_=setup(tmp_path);body=request()
    a,_=store.create_lesson(body);b,_=store.create_lesson(body)
    first=store.job(a)['items'][0];second=store.job(b)['items'][0]
    store.finish(first['id'],package_id='a-result');store.finish(second['id'],package_id='b-result')
    untouched=store.job(b)
    update=dict(generation_requirements='使用图示')
    assert store.update_courseware(first['id'],update,'update-key')==(a,False)
    assert store.update_courseware(first['id'],update,'update-key')==(a,True)
    current=store.job(a)
    assert current['revision']==2 and current['items'][0]['generation']==1
    assert current['items'][0]['package_id']=='a-result' and current['status']=='pending'
    with pytest.raises(Conflict,match='正在生成'):store.update_courseware(first['id'],{})
    store.finish(first['id'],error=dict(code='test',message='失败'))
    assert store.job(a)['items'][0]['package_id']=='a-result'
    assert store.job(b)==untouched
    assert store.job(b)['revision']==1


@pytest.mark.asyncio
async def test_same_input_generates_in_parallel_without_reusing_draft_or_result(tmp_path):
    store,engine,_=setup(tmp_path);body=request()
    body.options.mode='qwen';body.options.voice=False;body.options.variant_count=0
    both_started=asyncio.Event()
    class FakeQwen:
        calls=0
        async def lesson(self,*args,**kwargs):
            self.calls+=1;call=self.calls
            if call==2:both_started.set()
            await both_started.wait()
            result=copy.deepcopy(lesson());result['title']=f'第{call}次生成'
            return result,dict(type='not_reviewed',passed=None)
    engine.qwen=FakeQwen()
    async def resources(*args):return []
    engine.resources=resources
    a,_=store.create_lesson(body);b,_=store.create_lesson(body)
    await asyncio.wait_for(asyncio.gather(engine.generate(store.claim()),engine.generate(store.claim())),30)
    first=store.job(a)['items'][0];second=store.job(b)['items'][0]
    assert engine.qwen.calls==2 and first['package_id']!=second['package_id']
    assert not first['reused'] and not second['reused']
    for item in [first,second]:
        assert (tmp_path/'work'/item['id']/'checkpoint.json').exists()
        assert store.package(item['package_id'])['courseware_id']==item['id']
    store.update_courseware(first['id'],{})
    await engine.generate(store.claim())
    updated=store.job(a)['items'][0]
    assert engine.qwen.calls==3 and updated['package_id'] not in {first['package_id'],second['package_id']}
    assert (tmp_path/'work'/first['id']/'runs'/'1'/'checkpoint.json').exists()
    assert (tmp_path/'packages'/first['package_id']/'index.html').exists()
    assert store.job(b)['items'][0]==second


@pytest.mark.asyncio
async def test_workspace_api_creates_multiple_and_keeps_batch_jobs(tmp_path):
    _,_,cfg=setup(tmp_path);app=create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        body=request().model_dump()
        response=await client.post('/api/jobs?single_question=true',json=body,headers={'Idempotency-Key':'one'})
        assert response.status_code==202
        first=response.json()
        replay=await client.post('/api/jobs?single_question=true',json=body,headers={'Idempotency-Key':'one'})
        assert replay.json()['courseware_id']==first['courseware_id'] and replay.json()['idempotent_replay']
        second=await client.post('/api/jobs?single_question=true',json=body)
        assert second.status_code==202 and second.json()['courseware_id']!=first['courseware_id']
        legacy=await client.post('/api/jobs',json=body)
        assert legacy.status_code==202
        assert len((await client.get('/api/jobs')).json())==3
        assert len((await client.get('/api/jobs?unique_questions=true')).json())==3
