from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', env_file_encoding='utf-8', extra='ignore')
    dashscope_api_key: str = ''
    qwen_base_url: str = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
    qwen_model: str = 'qwen-plus'
    qwen_vision_model: str = 'qwen3-vl-plus'
    qwen_timeout: int = Field(default=180, ge=10, le=600)
    tts_mode: str = 'browser'
    qwen_tts_url: str = 'https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'
    qwen_tts_model: str = 'qwen3-tts-flash'
    qwen_tts_voice: str = 'Cherry'
    api_key: str = ''
    public_base_url: str = ''
    data_dir: Path = ROOT / 'data'
    worker_concurrency: int = Field(default=2, ge=1, le=8)
    callback_allowed_urls: str = ''
    callback_secret: str = ''
    knowledge_api_url: str = ''
    knowledge_api_key: str = ''
    s3_endpoint_url: str = ''
    s3_bucket: str = ''
    s3_access_key_id: str = ''
    s3_secret_access_key: str = ''
    s3_region: str = 'us-east-1'
    s3_prefix: str = 'explanations'
    vector_database_url: str = ''
    embedding_model: str = 'text-embedding-v4'
    embedding_dimensions: int = 1024
    exam_db_host: str = ''
    exam_db_port: int = 5432
    exam_db_name: str = 'exam'
    exam_db_user: str = ''
    exam_db_password: str = ''
    exam_minio_endpoint: str = ''
    exam_minio_region: str = 'us-east-1'
    exam_minio_access_key: str = ''
    exam_minio_secret_key: str = ''
    exam_minio_allowed_buckets: str = ''

    def validate_runtime(self):
        if self.tts_mode not in {'browser', 'qwen', 'none'}:
            raise ValueError('TTS_MODE 必须是 browser、qwen 或 none')
        if self.s3_bucket and not (self.s3_access_key_id and self.s3_secret_access_key):
            raise ValueError('S3 存储配置不完整')
        if self.callback_allowed_urls and not self.callback_secret:
            raise ValueError('启用回调须设置 CALLBACK_SECRET')
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def generation_identity(self):
        return dict(pipeline='illustrated-flow-v5', model=self.qwen_model,
                    vision_model=self.qwen_vision_model, endpoint=self.qwen_base_url,
                    tts_mode=self.tts_mode, tts_model=self.qwen_tts_model, voice=self.qwen_tts_voice)
