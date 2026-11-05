import json
from types import SimpleNamespace

import httpx
import pytest

from app.config import Settings
from app.models import IllustratedLesson
from app.providers import Qwen
from tests.test_illustrated_lessons import lesson


def event(value):
    return ('data: '+json.dumps(value,ensure_ascii=False)+'\r\n\r\n').encode()


def completion(text):
    midpoint=len(text)//2
    return b': heartbeat\r\n\r\n'+event({'choices':[{'index':0,'delta':{'content':text[:midpoint]},'finish_reason':None}]})+event({'choices':[{'index':0,'delta':{'content':text[midpoint:]},'finish_reason':None}]})+event({'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]})+event({'choices':[],'usage':{}})+b'data: [DONE]\r\n\r\n'


class Bytes(httpx.AsyncByteStream):
    def __init__(self,raw,broken=False):self.raw,self.broken=raw,broken
    async def __aiter__(self):
        for offset in range(0,len(self.raw),7):yield self.raw[offset:offset+7]
        if self.broken:raise httpx.ReadError('interrupted')


@pytest.mark.asyncio
async def test_streaming_keeps_long_generation_and_utf8_chunks(monkeypatch):
    client=httpx.AsyncClient;calls=[];stages=[]
    clock=[0]
    def tick():clock[0]+=65;return clock[0]
    monkeypatch.setattr('app.providers.time',SimpleNamespace(monotonic=tick))
    def handler(request):
        payload=json.loads(request.content);calls.append(payload)
        assert payload['stream'] is True and payload['response_format']=={'type':'json_object'}
        assert request.extensions['timeout']['connect']==15
        assert request.extensions['timeout']['read']==180
        return httpx.Response(200,headers={'Content-Type':'text/event-stream; charset=utf-8'},stream=Bytes(completion(json.dumps(lesson(),ensure_ascii=False))))
    monkeypatch.setattr('app.providers.httpx.AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    result=await Qwen(Settings(_env_file=None,dashscope_api_key='test')).json('JSON',{},IllustratedLesson,progress=stages.append)
    assert result.answer=='C' and len(calls)==1 and clock[0]>180
    assert any('已收到' in s for s in stages) and '接收完成' in stages[-1]


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect',[True,False])
async def test_partial_stream_retries_without_concatenating_broken_content(monkeypatch,disconnect):
    client=httpx.AsyncClient;calls=[];stages=[];delays=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr('app.providers.asyncio.sleep',sleep)
    def handler(request):
        calls.append(request)
        raw=event({'choices':[{'delta':{'content':'INVALID PARTIAL'},'finish_reason':None}]}) if len(calls)==1 else completion(json.dumps(lesson()))
        return httpx.Response(200,headers={'Content-Type':'text/event-stream'},stream=Bytes(raw,broken=disconnect and len(calls)==1))
    monkeypatch.setattr('app.providers.httpx.AsyncClient',lambda **kw:client(transport=httpx.MockTransport(handler),**kw))
    result=await Qwen(Settings(_env_file=None,dashscope_api_key='test')).json('JSON',{},IllustratedLesson,progress=stages.append)
    assert result.answer=='C' and len(calls)==2 and delays==[1]
    assert any('中断（已收到 15 字）' in s for s in stages)
