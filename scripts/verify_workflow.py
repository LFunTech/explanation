import json
import sys
import time
from pathlib import Path
import httpx
from app.samples import SAMPLES

sys.stdout.reconfigure(encoding='utf-8')
base='http://127.0.0.1:18765'
with httpx.Client(timeout=30,trust_env=False) as client:
    request=dict(assignment_id='acceptance-science-samples',title='理科讲解样例 · 数学 / 物理 / 化学',
        questions=[s['question'] for s in SAMPLES],options=dict(mode='sample',variant_count=2,voice=True))
    sample=client.post(base+'/api/jobs',json=request,headers={'Idempotency-Key':'acceptance-samples-v1'})
    sample.raise_for_status(); sample_id=sample.json()['id']
    question={**SAMPLES[0]['question'],'question_id':'qwen-verification-math-001'}
    real=client.post(base+'/api/jobs',json=dict(assignment_id='acceptance-qwen',title='千问真实生成 · 因式分解法',
        questions=[question],options=dict(mode='qwen',variant_count=2,voice=True)),headers={'Idempotency-Key':'acceptance-qwen-v1'})
    real.raise_for_status(); real_id=real.json()['id']
    Path('.tmp/verification-jobs.json').write_text(json.dumps(dict(samples=sample_id,qwen=real_id)),encoding='utf-8')
    print('Created sample and Qwen jobs:',sample_id,real_id,flush=True)
    last={}; verified=set()
    existing=client.get(base+'/api/jobs/'+real_id).json()
    if existing['status'] in {'failed','partial_failed'}:
        client.post(base+'/api/jobs/'+real_id+'/retry').raise_for_status()
    for _ in range(600):
        done=True
        for job_id in [sample_id,real_id]:
            job=client.get(base+'/api/jobs/'+job_id).json()
            stages=[i['stage'] for i in job['items']]
            if last.get(job_id)!=stages:
                print(job['title'],job['status'],stages,flush=True);last[job_id]=stages
            if job['status'] in {'pending','generating'}:
                done=False
            elif job['status']!='completed':
                print('Errors:',[i['error'] for i in job['items'] if i['error']],flush=True)
                raise SystemExit(1)
            elif job_id not in verified:
                for item in job['items']:
                    for key in ['html_url','pptx_url','zip_url']:
                        response=client.get(base+item['result'][key] if item['result'][key].startswith('/') else item['result'][key])
                        response.raise_for_status()
                        assert len(response.content)>1000
                verified.add(job_id)
        if done:
            print('All 4 lesson packages and downloads verified.',flush=True)
            break
        time.sleep(2)
    else:
        raise SystemExit('Live generation still pending; inspect the persisted jobs.')
