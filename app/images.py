"""输入图片统一为可随讲解包保存的栅格 data URL。"""
import base64
import io
import re

from PIL import Image

MAX_IMAGE_BYTES=8*1024*1024


def raster_data_url(raw):
    if len(raw)>MAX_IMAGE_BYTES:
        raise ValueError('单图不能超过 8 MB')
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {'PNG','JPEG','WEBP','GIF'}:
                raise ValueError('题目附件不是支持的图片格式')
            if source.width*source.height>20_000_000:
                raise ValueError('图片像素过大，请压缩后上传')
            if getattr(source,'n_frames',1)>1:
                raise ValueError('动态图片请先转换为完整的静态题图')
            source.load()
            source.thumbnail((2200,2200))
            # 透明题图铺白底，避免黑色线条被转成黑底。
            rgba=source.convert('RGBA')
            canvas=Image.new('RGB',rgba.size,'white')
            canvas.paste(rgba,mask=rgba.getchannel('A'))
            output=io.BytesIO();canvas.save(output,format='JPEG',quality=88)
        return 'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode('ascii')
    except (OSError,Image.DecompressionBombError) as error:
        raise ValueError('图片内容无法解码') from error


def normalize_image(value):
    if not re.match(r'^data:image/(png|jpeg|webp);base64,',value):
        raise ValueError('图片须为上传接口返回的 data URL，支持 PNG、JPEG 和 WebP')
    return raster_data_url(base64.b64decode(value.split(',',1)[1],validate=True))
