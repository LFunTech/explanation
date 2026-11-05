"""Validate runtime env input without logging any credential values."""
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit


def parse_config(text):
    values = {}
    for number, raw in enumerate(text.lstrip('\ufeff').splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
            raise ValueError(f'Invalid env entry on line {number}; use KEY=value')
        if key in values:
            raise ValueError(f'Duplicate env key on line {number}')
        if value.startswith(('"', "'")):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f'Unclosed env quote on line {number}')
            value = value[1:-1]
        if '\x00' in value:
            raise ValueError(f'Invalid env value on line {number}')
        values[key] = value
    for key in ('API_KEY', 'DASHSCOPE_API_KEY', 'PUBLIC_BASE_URL', 'EXAM_DB_HOST', 'EXAM_DB_USER', 'EXAM_DB_PASSWORD'):
        if not values.get(key):
            raise ValueError(f'{key} must be configured')
    url = urlsplit(values['PUBLIC_BASE_URL'])
    host = url.hostname or ''
    if (url.scheme != 'https' or not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host)
            or '.' not in host or host.endswith(('.example.com', '.example.org'))
            or host in ('example.com', 'example.org', '127.0.0.1')
            or url.username or url.password or url.port not in (None, 443)
            or url.path not in ('', '/') or url.query or url.fragment):
        raise ValueError('PUBLIC_BASE_URL must be the real HTTPS root domain served by Ingress')
    if values.get('S3_BUCKET'):
        for key in ('S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY'):
            if not values.get(key):
                raise ValueError(f'{key} must be configured when S3_BUCKET is set')
    # Deployment owns the data location; a Windows/local path must never leak in.
    values['DATA_DIR'] = '/data'
    return values, host


def main():
    os.umask(0o077)
    destination = Path(sys.argv[1])
    try:
        values, host = parse_config(os.environ.get('APP_CONFIG', ''))
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'runtime.env').write_text(''.join(f'{key}={value}\n' for key, value in values.items()), encoding='utf-8')
        (destination / 'host').write_text(host, encoding='utf-8')
    except (ValueError, OSError) as error:
        # OSError paths and input parsing exceptions can contain secret input.
        message = str(error) if isinstance(error, ValueError) and str(error).startswith(('Invalid env', 'Duplicate env', 'Unclosed env', 'PUBLIC_BASE_URL', 'API_KEY', 'DASHSCOPE_API_KEY', 'EXAM_DB_', 'S3_')) else 'Invalid or unwritable runtime configuration'
        print(message, file=sys.stderr)
        return 1
    print('Runtime configuration validated; values are not logged.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
