"""仅从配置的 MinIO 读取数据库已关联的公共题目对象，不接受任意 URL。"""
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .images import MAX_IMAGE_BYTES, raster_data_url
from .providers import GenerationError


def object_location(uri,allowed_buckets):
    parsed=urlsplit(uri)
    key=parsed.path[1:]
    if (parsed.scheme!='minio' or parsed.netloc not in allowed_buckets
            or parsed.query or parsed.fragment or not key or key.startswith('/') or '\\' in key
            or any(p in {'.','..'} for p in key.split('/')) or any(ord(c)<32 or ord(c)==127 for c in key)):
        raise GenerationError('exam_image_location','题库图片的存储位置不在配置范围内。')
    return parsed.netloc,key


class ExamImageStorage:
    def __init__(self,cfg):
        self.cfg=cfg
        self.allowed={value.strip() for value in cfg.exam_minio_allowed_buckets.split(',') if value.strip()}
        self.client=None

    def read(self,uri):
        cfg=self.cfg
        if not (cfg.exam_minio_endpoint and cfg.exam_minio_access_key and cfg.exam_minio_secret_key and self.allowed):
            raise GenerationError('exam_image_not_configured','尚未配置 exam 题库图片存储，可先上传原图。')
        bucket,key=object_location(uri,self.allowed)
        try:
            if self.client is None:
                self.client=boto3.client('s3',endpoint_url=cfg.exam_minio_endpoint,region_name=cfg.exam_minio_region,
                    aws_access_key_id=cfg.exam_minio_access_key,aws_secret_access_key=cfg.exam_minio_secret_key,
                    config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                        connect_timeout=8,read_timeout=20,retries={'max_attempts':1},proxies={}))
            response=self.client.get_object(Bucket=bucket,Key=key)
            with response['Body'] as body:
                if response.get('ContentLength',0)>MAX_IMAGE_BYTES:
                    raise ValueError('单图不能超过 8 MB')
                raw=body.read(MAX_IMAGE_BYTES+1)
            return raster_data_url(raw)
        except (BotoCoreError,ClientError):
            raise GenerationError('exam_image_unavailable','题库原图读取失败，请检查存储访问或稍后重新选题。') from None
        except ValueError as error:
            raise GenerationError('exam_image_invalid',str(error)) from None
