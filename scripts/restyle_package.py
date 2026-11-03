"""Rebuild a package with the current presentation, retaining reviewed content/audio."""
import argparse

from app.config import Settings
from app.engine import Engine,package_links
from app.store import Store


def main():
    parser=argparse.ArgumentParser(description='更新已有课件排版，保存新版本，不重新调用模型或语音服务')
    parser.add_argument('package_id')
    args=parser.parse_args()
    cfg=Settings();cfg.validate_runtime()
    store=Store(cfg.data_dir/'app.sqlite3')
    previous=store.package(args.package_id)
    if not previous:
        parser.error('讲解包不存在')
    result=Engine(store,cfg).restyle(previous)
    print(result['package_id'])
    print(package_links(cfg,result['package_id'])['html_url'])


if __name__=='__main__':
    main()
