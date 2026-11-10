"""DrissionPage 兜底引擎的浏览器必须被回收。

只 new 不 close 会让残留的无头 Chrome 一直占着调试端口，下一次启动就连错端口报
`Handshake status 404`。这里用回归测试把「无论成功、失败还是 close 本身抛错，
都要调用 handler.close()」钉住。
"""

import unittest
from unittest import mock

from utils.slider_orchestrator import DRISSIONPAGE_ENGINE, _run_drissionpage_fallback


class DrissionPageHandlerCloseTests(unittest.TestCase):
    def run_fallback(self, handler):
        factory = mock.Mock(return_value=handler)
        result = _run_drissionpage_fallback(
            'https://www.goofish.com/',
            user_id='test-user',
            handler_factory=factory,
        )
        return result, factory

    def test_closes_handler_when_verification_succeeds(self):
        handler = mock.Mock()
        handler.get_cookies.return_value = {'unb': '1', 'x5sec': 'granted'}

        result, _ = self.run_fallback(handler)

        self.assertTrue(result.success)
        handler.close.assert_called_once_with()

    def test_closes_handler_when_verification_raises(self):
        handler = mock.Mock()
        handler.get_cookies.side_effect = RuntimeError('browser launch failed')

        result, _ = self.run_fallback(handler)

        self.assertFalse(result.success)
        self.assertIn('browser launch failed', result.message)
        handler.close.assert_called_once_with()

    def test_closes_handler_when_no_x5_cookie(self):
        handler = mock.Mock()
        handler.get_cookies.return_value = {'unb': '1'}

        result, _ = self.run_fallback(handler)

        self.assertFalse(result.success)
        self.assertEqual(result.engine, DRISSIONPAGE_ENGINE)
        handler.close.assert_called_once_with()

    def test_close_error_does_not_mask_result(self):
        handler = mock.Mock()
        handler.get_cookies.return_value = {'unb': '1', 'x5sec': 'granted'}
        handler.close.side_effect = RuntimeError('close failed')

        result, _ = self.run_fallback(handler)

        self.assertTrue(result.success)
        handler.close.assert_called_once_with()

    def test_no_close_when_factory_raises(self):
        factory = mock.Mock(side_effect=RuntimeError('cannot construct handler'))

        result = _run_drissionpage_fallback(
            'https://www.goofish.com/',
            user_id='test-user',
            handler_factory=factory,
        )

        self.assertFalse(result.success)
        self.assertIn('cannot construct handler', result.message)


if __name__ == '__main__':
    unittest.main()
