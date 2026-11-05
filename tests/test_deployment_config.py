import pytest

from scripts.ci.prepare_config import parse_config


def configuration(**changes):
    values = dict(API_KEY='test-key', DASHSCOPE_API_KEY='test-model-key',
                  PUBLIC_BASE_URL='https://lessons.school.test', EXAM_DB_HOST='exam-db',
                  EXAM_DB_USER='readonly', EXAM_DB_PASSWORD='p@ss=$literal')
    values.update(changes)
    return '\n'.join(f'{key}={value}' for key, value in values.items())


def test_runtime_env_preserves_credentials_and_uses_persistent_data_path():
    values, host = parse_config('\ufeff# comment\r\n' + configuration(DATA_DIR='C:\\local\\data'))
    assert values['EXAM_DB_PASSWORD'] == 'p@ss=$literal'
    assert values['DATA_DIR'] == '/data'
    assert host == 'lessons.school.test'
    values, _ = parse_config(configuration(API_KEY='"space $literal #key"'))
    assert values['API_KEY'] == 'space $literal #key'


@pytest.mark.parametrize('base', ['http://lessons.school.test', 'https://localhost',
    'https://example.com', 'https://lessons.school.test/path', 'https://user:secret@lessons.school.test',
    'https://lessons.school.test:8080', 'https://lessons.school.test/?token=secret'])
def test_runtime_env_rejects_an_unusable_ingress_origin_without_leaking_input(base):
    with pytest.raises(ValueError) as caught:
        parse_config(configuration(PUBLIC_BASE_URL=base))
    assert base not in str(caught.value)
    assert 'secret' not in str(caught.value)


def test_runtime_env_requires_access_key_and_rejects_ambiguous_duplicates():
    with pytest.raises(ValueError, match='API_KEY must be configured'):
        parse_config(configuration(API_KEY=''))
    with pytest.raises(ValueError, match='Duplicate env key'):
        parse_config(configuration() + '\nAPI_KEY=second')
    with pytest.raises(ValueError, match='S3_SECRET_ACCESS_KEY must be configured'):
        parse_config(configuration(S3_BUCKET='lessons', S3_ACCESS_KEY_ID='test'))
