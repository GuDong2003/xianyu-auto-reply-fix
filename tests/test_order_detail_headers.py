"""订单详情抓取器不应伪造 client hints。

手写 sec-ch-ua / sec-ch-ua-platform 会与 Playwright 实际运行的浏览器版本脱节，
平台判定客户端异常时返回未渲染的空壳 SPA（body 0 字、XHR 不发），表现是
「订单详情获取成功」但状态永远 unknown。这里用回归测试把「不要手写」钉住。
"""

import unittest

from utils.order_detail_fetcher import OrderDetailFetcher


FORBIDDEN_HEADERS = (
    'sec-ch-ua',
    'sec-ch-ua-mobile',
    'sec-ch-ua-platform',
    'sec-fetch-dest',
    'sec-fetch-mode',
    'sec-fetch-site',
    'sec-fetch-user',
)


class OrderDetailFetcherHeadersTests(unittest.TestCase):
    def make_fetcher(self):
        return OrderDetailFetcher(cookie_string='unb=1; cookie2=2', headless=True, cookie_id_for_log='test')

    def test_default_headers_are_empty(self):
        fetcher = self.make_fetcher()
        self.assertEqual(fetcher.headers, {})

    def test_no_forged_client_hints(self):
        fetcher = self.make_fetcher()
        normalized = {str(key).lower() for key in fetcher.headers}
        for header in FORBIDDEN_HEADERS:
            with self.subTest(header=header):
                self.assertNotIn(header, normalized)


if __name__ == '__main__':
    unittest.main()
