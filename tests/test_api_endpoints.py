"""Read-only announcement and maintenance request contracts."""
import json
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from module.base.api_client import ApiClient
from module.handover.handover import HANDOVER_MAINTAIN_API, OperationHandover
from module.api.announcement_service import AnnouncementService
from module.server_checker import SERVER_API_BASE


class ApiEndpointsTest(unittest.TestCase):
    def test_identical_fallback_origin_does_not_send_twice(self):
        response = Mock(status_code=400)
        with patch.multiple(ApiClient, PRIMARY_DOMAIN='https://notice.example.invalid',
                            FALLBACK_DOMAIN='https://notice.example.invalid'), patch(
            'module.base.api_client.http.get', return_value=response,
        ) as request:
            success, status, _ = ApiClient._get_with_fallback('/bad')
        self.assertFalse(success)
        self.assertEqual(status, 400)
        request.assert_called_once()

    def test_announcement_keeps_root_id_contract(self):
        notice = {'id': 'notice-test', 'title': 'test', 'content': 'body', 'url': ''}
        response = Mock(status_code=200, text=json.dumps({'schema_version': 2, 'announcement': notice}))
        with patch.multiple(ApiClient, PRIMARY_DOMAIN='https://notice.example.invalid',
                            FALLBACK_DOMAIN='https://notice.example.invalid'), patch(
            'module.base.api_client.http.get', return_value=response,
        ) as request:
            service = AnnouncementService()
            self.assertEqual(service.get(force=True)['announcementId'], 'notice-test')
            self.assertEqual(request.call_args.args[0], 'https://notice.example.invalid/api/v2/get/notice/data.json')
            response.text = json.dumps({'schema_version': 2, 'announcement': None})
            self.assertIsNone(service.get(force=True))

    def test_default_has_no_external_notice_or_maintenance_origin(self):
        self.assertEqual(SERVER_API_BASE, '')
        self.assertEqual(HANDOVER_MAINTAIN_API, '')
        self.assertIsNone(AnnouncementService().get(force=True))

    def test_maintenance_never_falls_back_from_jp_to_cn(self):
        target = SimpleNamespace(config=SimpleNamespace(SERVER='jp'))
        response = Mock()
        response.json.return_value = {'schema_version': 2, 'server': 'jp', 'maintenance': None}
        with patch('module.handover.handover.HANDOVER_MAINTAIN_API', 'https://notice.example.invalid/maintenance'), patch(
            'module.handover.handover.http.get', return_value=response,
        ) as request:
            result, _, error = OperationHandover.handover_maintain_query(target)
            self.assertEqual(result, {})
            self.assertEqual(error, '')
            self.assertEqual(request.call_args.kwargs['params'], {'server': 'jp'})
            self.assertEqual(request.call_args.kwargs['headers'], {'X-AzurPilot-ID': '0' * 32})
            response.json.return_value = {'schema_version': 2, 'server': 'cn', 'maintenance': {'starts_at': '2026-10-22T02:00:00Z'}}
            result, _, error = OperationHandover.handover_maintain_query(target)
            self.assertIsNone(result)
            self.assertTrue(error)

    def test_unavailable_maintenance_is_an_explicit_query_error(self):
        import requests
        target = SimpleNamespace(config=SimpleNamespace(SERVER='cn'))
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError('503 Service Unavailable')
        with patch('module.handover.handover.HANDOVER_MAINTAIN_API', 'https://notice.example.invalid/maintenance'), patch(
            'module.handover.handover.http.get', return_value=response,
        ):
            result, _, error = OperationHandover.handover_maintain_query(target)
        self.assertIsNone(result)
        self.assertTrue(error)


if __name__ == '__main__':
    unittest.main()
