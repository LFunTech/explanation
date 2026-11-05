import httpx
import pytest

from app.main import create_app
from tests.test_core import setup, request


@pytest.mark.asyncio
async def test_pagination_filters_all_records_and_does_not_merge_same_question(tmp_path):
    _,_,cfg=setup(tmp_path);app=create_app(cfg);store=app.state.store
    ids=[]
    for n in range(107):
        body=request();body.title=f'课件-{n:03d}'
        body.questions[0].target_grade='七年级' if n%2==0 else '八年级'
        body.questions[0].knowledge_points=['方程' if n%3==0 else '几何']
        body.questions[0].target_curriculum=[]
        id,_=store.create_lesson(body);ids.append(id)
        if n<100:store.finish(store.job(id)['items'][0]['id'],error=dict(code='test',message='测试'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        pages=[]
        for page in range(1,7):
            response=await client.get('/api/courseware',params=dict(page=page,page_size=20))
            assert response.status_code==200,response.text
            data=response.json();pages.append(data)
            assert data['pagination']==dict(page=page,page_size=20,total=107,pages=6)
            assert data['stats']==dict(total=107,completed=0,active=7,failed=100)
            assert set(data['filters']['grades'])=={'七年级','八年级'}
            assert set(data['filters']['knowledge_points'])=={'方程','几何'}
        assert [j['id'] for data in pages for j in data['items']]==ids[::-1]
        assert len(pages[-1]['items'])==7
        # The oldest record is past the former 100-item cap, but remains searchable.
        data=(await client.get('/api/courseware',params=dict(query='课件-000',page=99))).json()
        assert data['pagination']==dict(page=1,page_size=20,total=1,pages=1)
        assert data['items'][0]['id']==ids[0]
        data=(await client.get('/api/courseware',params=dict(grade='八年级',knowledge_point='方程',status='active',page_size=10))).json()
        assert [j['title'] for j in data['items']]==['课件-105']
        assert data['pagination']['total']==1 and data['stats']['total']==107
        data=(await client.get('/api/courseware',params=dict(query='不存在',page=7))).json()
        assert not data['items'] and data['pagination']==dict(page=1,page_size=20,total=0,pages=1)
        for params in [dict(page=0),dict(page_size=0),dict(page_size=101),dict(status='invalid')]:
            assert (await client.get('/api/courseware',params=params)).status_code==422


@pytest.mark.asyncio
async def test_listing_uses_target_knowledge_and_exposes_independent_ids(tmp_path):
    _,_,cfg=setup(tmp_path);app=create_app(cfg);store=app.state.store
    body=request();q=body.questions[0];q.grade='九年级';q.grades=['九年级'];q.target_grade='七年级'
    from app.models import TargetKnowledgePoint
    q.knowledge_points=['原题知识点'];q.target_curriculum=[TargetKnowledgePoint(knowledge_point='目标知识点',grade='七年级')]
    a,_=store.create_lesson(body);b,_=store.create_lesson(body)
    store.save_package(dict(package_id='old-result',fingerprint='test',slide_count=1,title='旧课件',voice_mode='none',variants=[],mode='sample'))
    store.finish(store.job(a)['items'][0]['id'],package_id='old-result')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        data=(await client.get('/api/courseware',params=dict(grade='七年级',knowledge_point='目标知识点'))).json()
        assert {j['id'] for j in data['items']}=={a,b}
        assert data['filters']==dict(grades=['七年级'],knowledge_points=['目标知识点'])
        assert data['items'][0]['items'][0]['package_id'] is None
        cid=store.job(a)['items'][0]['id']
        data=(await client.get('/api/courseware',params=dict(query=cid))).json()
        assert data['pagination']['total']==1 and data['items'][0]['id']==a
