import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.models import IllustratedLesson
from app.providers import Qwen, GenerationError, post_with_retry
from tests.test_illustrated_lessons import lesson


@pytest.mark.asyncio
async def test_llm_keeps_retrying_past_old_limit_with_capped_backoff(monkeypatch):
    calls=[];delays=[];stages=[];client=httpx.AsyncClient
    async def sleep(seconds):delays.append(seconds)
    monkeypatch.setattr('app.providers.asyncio.sleep',sleep)
    def handler(request):
        calls.append(request)
        if len(calls)<=4:raise httpx.ReadTimeout('connection interrupted')
        if len(calls)<=6:return httpx.Response(503)
        if len(calls)==7:return httpx.Response(429)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(lesson())}}]})
    monkeypatch.setattr('app.providers.httpx.AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    result=await Qwen(Settings(_env_file=None,dashscope_api_key='test')).json('生成课件',{},IllustratedLesson,progress=stages.append)
    assert result.answer=='C' and len(calls)==8
    assert delays==[1,2,4,8,16,30,30]
    assert len(stages)==7 and '第 7 次' in stages[-1]


@pytest.mark.asyncio
async def test_llm_does_not_retry_invalid_credentials(monkeypatch):
    calls=[];client=httpx.AsyncClient
    def handler(request):calls.append(request);return httpx.Response(401)
    monkeypatch.setattr('app.providers.httpx.AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(GenerationError,match='401'):
        await Qwen(Settings(_env_file=None,dashscope_api_key='invalid')).json('JSON',{},IllustratedLesson)
    assert len(calls)==1


@pytest.mark.asyncio
async def test_unlimited_retry_still_cancels_cleanly_on_shutdown(monkeypatch):
    waiting=asyncio.Event();calls=[]
    async def sleep(seconds):
        waiting.set()
        await asyncio.Event().wait()
    monkeypatch.setattr('app.providers.asyncio.sleep',sleep)
    def handler(request):
        calls.append(request)
        raise httpx.ConnectError('offline')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task=asyncio.create_task(post_with_retry(client,'https://example.test',max_attempts=None))
        await asyncio.wait_for(waiting.wait(),1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert len(calls)==1
