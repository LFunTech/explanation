"""Short-lived copies of successfully read public questions for network outages."""
import copy
import hashlib
import json
import random
import sqlite3
import time


class ExamCache:
    def __init__(self,cfg):
        self.path=cfg.data_dir/'exam-cache.sqlite3'
        self.namespace=hashlib.sha256(f'{cfg.exam_db_host}:{cfg.exam_db_port}/{cfg.exam_db_name}'.encode()).hexdigest()

    def put(self,rows):
        if not rows:return
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS questions (namespace TEXT,id TEXT,subject TEXT,seen REAL,payload TEXT,PRIMARY KEY(namespace,id))')
            db.executemany('INSERT OR REPLACE INTO questions VALUES (?,?,?,?,?)',[
                (self.namespace,row['id'],row['question']['subject'],time.time(),json.dumps(row,ensure_ascii=False)) for row in rows])
            db.execute('DELETE FROM questions WHERE seen<?',(time.time()-86400,))

    def random_questions(self,subject,grades,region,limit,excluded):
        if not self.path.exists():return []
        with sqlite3.connect(self.path) as db:
            records=db.execute('SELECT payload FROM questions WHERE namespace=? AND subject=? AND seen>=?',
                (self.namespace,subject,time.time()-86400)).fetchall()
        values=[json.loads(row[0]) for row in records]
        values=[row for row in values if not grades or set(row['grades']).intersection(grades)]
        excluded={v.removeprefix('exam:public:') for v in excluded}
        random.shuffle(values)
        available=[row for row in values if row['id'] not in excluded]
        if len(available)<limit:available.extend(row for row in values if row['id'] in excluded)
        result=[]
        for row in available[:limit]:
            row=copy.deepcopy(row);row['cached']=True;row['question']['region']=region;result.append(row)
        return result
