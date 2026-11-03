"""显式运行的真实服务检查，不打印凭证或学生数据。"""
import asyncio
import json
import sys
from pathlib import Path
from app.config import Settings
from app.kb import KnowledgeBase
from app.exam import Exam
from app.providers import Qwen

sys.stdout.reconfigure(encoding='utf-8')

async def main():
    cfg=Settings();kb=KnowledgeBase(cfg)
    vectors=await kb.embeddings(['上海初中数学：一元二次方程，因式分解法求根。'])
    print('Qwen embedding verified, dimensions:',len(vectors[0]),flush=True)
    rows=await asyncio.to_thread(Exam(cfg).search,'数学','九年级','一元二次方程',3)
    print('Exam question search verified, returned:',len(rows),flush=True)
    audio=await Qwen(cfg).speech('我们先观察题目条件，再选择合适的方法，完成解答并检验。')
    path=cfg.data_dir/'verification.wav';path.write_bytes(audio)
    print('Qwen TTS verified, WAV bytes:',len(audio),flush=True)

asyncio.run(main())
