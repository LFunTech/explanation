"""Browser access to published courseware, with signing kept on the server."""

import boto3
from botocore.config import Config


def object_urls(settings, storage, filenames):
    client = boto3.client(
        's3',
        endpoint_url=settings.s3_public_endpoint_url or settings.s3_endpoint_url or None,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}),
    )
    result={}
    for name in filenames:
        params={
            'Bucket': storage['bucket'],
            'Key': storage['prefix'].rstrip('/') + '/' + name,
            'ResponseContentDisposition': 'inline',
        }
        if name.endswith('.html'):params['ResponseContentType']='text/html; charset=utf-8'
        result[name]=client.generate_presigned_url('get_object',Params=params,ExpiresIn=3600)
    client.close()
    return result


def content_url(settings, storage):
    return object_urls(settings,storage,['online.html'])['online.html']
