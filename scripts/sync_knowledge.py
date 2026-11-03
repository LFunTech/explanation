"""python -m scripts.sync_knowledge [--embed] [--limit 20000]"""
import argparse
import asyncio
import sys
from app.config import Settings
from app.exam import Exam
from app.kb import KnowledgeBase

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser()
parser.add_argument('--embed',action='store_true')
parser.add_argument('--limit',type=int,default=20000)
args=parser.parse_args()
cfg=Settings(); kb=KnowledgeBase(cfg); kb.initialize()
print(Exam(cfg).import_graph(kb),flush=True)
if args.embed:
    print('Embedded:',asyncio.run(kb.index_pending(args.limit,lambda n:print('Indexed',n,flush=True))))
print(kb.stats())
