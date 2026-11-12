"""Shared cloud API origin; usable before application dependencies are loaded."""

import os
from urllib.parse import urlsplit


_base = os.environ.get('AZURPILOT_API_BASE', '').strip().rstrip('/')
try:
    _url = urlsplit(_base)
    _url.port
    API_BASE_URL = _base if _url.scheme == 'https' and _url.hostname and not (
        _url.username or _url.password or _url.query or _url.fragment
    ) else ''
except ValueError:
    API_BASE_URL = ''
MAINTENANCE_API_URL = f'{API_BASE_URL}/api/v2/get/maintenance' if API_BASE_URL else ''
# 两个状态接口要求此请求头。所有安装发送同一个值，避免携带本机标识。
ANONYMOUS_STATUS_HEADERS = {'X-AzurPilot-ID': '0' * 32}
