"""只读资源表结构诊断，不读取学生、作业提交、凭证等数据。"""
import json
from pathlib import Path

import psycopg
from dotenv import dotenv_values
from psycopg.rows import dict_row

cfg = dotenv_values(Path(__file__).resolve().parent.parent / '.env')
try:
    with psycopg.connect(host=cfg['EXAM_DB_HOST'], port=cfg.get('EXAM_DB_PORT', 5432),
                         dbname=cfg['EXAM_DB_NAME'], user=cfg['EXAM_DB_USER'],
                         password=cfg['EXAM_DB_PASSWORD'], connect_timeout=10,
                         options='-c default_transaction_read_only=on -c statement_timeout=10000',
                         row_factory=dict_row) as conn:
        rows = conn.execute("""SELECT table_schema, table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_name IN ('knowledge_point','grade','subject','textbook_chapter','textbook_version',
                'curriculum_standard','knowledge_point_grade','question_taxonomy_relation','question_grade')
              AND table_schema IN ('public','tenant_kg','tenant_kg_primary')
            ORDER BY table_schema,table_name,ordinal_position""").fetchall()
        print(json.dumps(rows, ensure_ascii=False, indent=2))
except psycopg.OperationalError:
    raise SystemExit('无法连接 exam 数据库；请检查内网连通性、端口和提供的账号配置。')
