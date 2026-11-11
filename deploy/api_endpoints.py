"""Shared cloud API origin; usable before application dependencies are loaded."""

API_BASE_URL = 'https://api.azurpilot.com'
MAINTENANCE_API_URL = f'{API_BASE_URL}/api/v2/get/maintenance'
# 两个状态接口要求此请求头。所有安装发送同一个值，避免携带本机标识。
ANONYMOUS_STATUS_HEADERS = {'X-AzurPilot-ID': '0' * 32}
