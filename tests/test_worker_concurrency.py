import asyncio

import pytest

from app.models import JobRequest,Question
from tests.test_core import setup


@pytest.mark.asyncio
async def test_twenty_workers_allow_other_lessons_to_finish_during_one_stalled_request(tmp_path):
    store,engine,cfg=setup(tmp_path)
    assert cfg.worker_concurrency==20
    questions=[Question(question_id=f'parallel-{i}',stem=f'测试题 {i}') for i in range(21)]
    job,_=store.create_job(JobRequest(assignment_id='concurrency',questions=questions))
    twenty_started=asyncio.Event();release=asyncio.Event();others_done=asyncio.Event()
    started=set();finished=set();running=set();peak=[0]
    async def generate(item):
        id=item['question_id'];started.add(id);running.add(id);peak[0]=max(peak[0],len(running))
        if len(started)==20:twenty_started.set()
        if id=='parallel-0':await asyncio.Event().wait()
        else:
            await release.wait()
            store.finish(item['id'],package_id='test-package')
            running.remove(id);finished.add(id)
            if len(finished)==20:others_done.set()
    engine.generate=generate
    engine.start()
    try:
        await asyncio.wait_for(twenty_started.wait(),5)
        assert len(running)==20 and len(started)==20
        release.set()
        await asyncio.wait_for(others_done.wait(),5)
        assert len(started)==21 and peak[0]==20
        states={i['question_id']:i['status'] for i in store.job(job)['items']}
        assert states.pop('parallel-0')=='generating'
        assert set(states.values())=={'completed'}
    finally:await engine.close()
