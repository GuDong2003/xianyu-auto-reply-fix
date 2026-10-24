import hashlib
from types import SimpleNamespace
import unittest
from unittest import mock

import aiohttp
from multidict import CIMultiDict

from utils.order_history_sync import OrderHistoryPageFetcher, OrderHistorySyncError


class HistoryRequestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.fetcher = OrderHistoryPageFetcher('_m_h5_tk=initial_1; unb=synthetic', 'synthetic-account')
        self.fetcher._persist_cookie_update = mock.AsyncMock()
        self.requests = []
        self.responses = []

        def post(url, **kwargs):
            self.requests.append({'url': url, **kwargs})
            payload, cookie = self.responses.pop(0)
            headers = CIMultiDict()
            if cookie:
                headers.add('Set-Cookie', f'_m_h5_tk={cookie}; Path=/; HttpOnly')
            response = SimpleNamespace(headers=headers, json=mock.AsyncMock(return_value=payload))
            context = mock.MagicMock()
            context.__aenter__ = mock.AsyncMock(return_value=response)
            return context

        self.fetcher.session = SimpleNamespace(closed=False, post=post)

    def success(self, cookie=None):
        self.responses.append(({'ret': ['SUCCESS::调用成功'], 'data': {'module': {'items': []}}}, cookie))

    def failure(self, code, cookie=None):
        self.responses.append(({'ret': [code]}, cookie))

    def assert_sign(self, request, token):
        data = request['data']['data']
        params = request['params']
        expected = hashlib.md5(f"{token}&{params['t']}&34839810&{data}".encode()).hexdigest()
        self.assertEqual(params['sign'], expected)

    async def test_seller_request_contract(self):
        self.success()
        await self.fetcher._request_order_page(2)
        request = self.requests[0]
        self.assertEqual(request['params']['type'], 'originaljson')
        self.assertEqual(request['params']['spm_cnt'], 'a21107h.42826273.0.0')
        self.assertEqual(request['headers']['origin'], 'https://seller.goofish.com')
        self.assertEqual(request['headers']['referer'], 'https://seller.goofish.com/')
        self.assertNotIn('idle_site_biz_code', request['headers'])
        self.assertNotIn('idle_user_group_member_id', request['headers'])
        self.assertIn('"pageNumber":2', request['data']['data'])
        self.assert_sign(request, 'initial')

    async def test_success_cookie_update_used_for_next_page_signature(self):
        self.success('renewed_2')
        self.success()
        await self.fetcher._request_order_page(1)
        await self.fetcher._request_order_page(2)
        self.assert_sign(self.requests[0], 'initial')
        self.assert_sign(self.requests[1], 'renewed')
        self.assertIn('_m_h5_tk=renewed_2', self.requests[1]['headers']['cookie'])
        self.fetcher._persist_cookie_update.assert_awaited_once()

    async def test_expired_token_retries_once_with_updated_cookie(self):
        self.failure('FAIL_SYS_TOKEN_EXPIRED::令牌过期', 'renewed_2')
        self.success()
        await self.fetcher._request_order_page(1)
        self.assertEqual(len(self.requests), 2)
        self.assert_sign(self.requests[1], 'renewed')

    async def test_expired_token_does_not_retry_without_new_cookie(self):
        self.failure('FAIL_SYS_SESSION_EXPIRED::Session过期')
        with self.assertRaises(OrderHistorySyncError) as caught:
            await self.fetcher._request_order_page(1)
        self.assertEqual(caught.exception.kind, 'session_expired')
        self.assertEqual(len(self.requests), 1)

    async def test_repeated_expiration_is_bounded(self):
        self.failure('FAIL_SYS_TOKEN_EXPIRED::令牌过期', 'renewed_2')
        self.failure('FAIL_SYS_TOKEN_EXPIRED::令牌过期', 'again_3')
        with self.assertRaises(OrderHistorySyncError):
            await self.fetcher._request_order_page(1)
        self.assertEqual(len(self.requests), 2)

    async def test_real_permission_failure_not_retried_as_expired_session(self):
        self.failure('PERMISSION_EXCEPTION::无权限访问', 'renewed_2')
        with self.assertRaises(OrderHistorySyncError) as caught:
            await self.fetcher._request_order_page(1)
        self.assertEqual(caught.exception.kind, 'permission_denied')
        self.assertEqual(len(self.requests), 1)

    async def test_cookie_header_has_no_line_breaks(self):
        self.fetcher.cookie_string = '_m_h5_tk=initial_1;\r\nunb=synthetic'
        headers = self.fetcher._build_request_headers()
        self.assertNotIn('\r', headers['cookie'])
        self.assertNotIn('\n', headers['cookie'])

    async def test_session_uses_explicit_cookie_state(self):
        self.fetcher.session = None
        await self.fetcher._ensure_session()
        try:
            self.assertIsInstance(self.fetcher.session.cookie_jar, aiohttp.DummyCookieJar)
        finally:
            await self.fetcher.session.close()


if __name__ == '__main__':
    unittest.main()
