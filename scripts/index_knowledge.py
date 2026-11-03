import argparse
import asyncio
import sys
from app.config import Settings
from app.kb import KnowledgeBase

sys.stdout.reconfigure(encoding='utf-8')
parser=argparse.ArgumentParser();parser.add_argument('--limit',type=int,default=100000);args=parser.parse_args()
kb=KnowledgeBase(Settings())
print('Indexing completed:',asyncio.run(kb.index_pending(args.limit,lambda n:print('Indexed',n,flush=True))),flush=True)
print(kb.stats(),flush=True)
