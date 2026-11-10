"""订单详情抓取器不应伪造 client hints。

手写 sec-ch-ua / sec-ch-ua-platform 会与 Playwright 实际运行的浏览器版本脱节，
平台判定客户端异常时返回未渲染的空壳 SPA（body 0 字、XHR 不发），表现是
「订单详情获取成功」但状态永远 unknown。这里用回归测试把「不要手写」钉住。
"""

import inspect
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


class BrowserLocaleTests(unittest.TestCase):
    """清掉自定义头之后，语言只能靠 context 的 locale 保证。

    不显式指定时，Docker / 英文系统上浏览器会发 en-US，平台返回英文页面，
    下游按中文关键词匹配订单状态就全部落空 —— 又回到「状态永远 unknown」。
    """

    def test_browser_context_pins_chinese_locale(self):
        source = inspect.getsource(OrderDetailFetcher.init_browser)
        self.assertIn("locale='zh-CN'", source)


if __name__ == '__main__':
    unittest.main()
