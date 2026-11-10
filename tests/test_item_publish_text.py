"""发布载荷的标题/描述组装。

闲鱼没有独立标题输入框，详情页只渲染 desc；同时 titleDescSeparate 必须恒为
True，否则平台会把 title 当成 desc 前缀，读回来标题变成「描述前 30 字」。
"""

import unittest
from unittest import mock

from utils.item_publisher import ItemPublisher


class BuildItemDescTests(unittest.TestCase):
    def test_prepends_title_on_first_line(self):
        self.assertEqual(ItemPublisher.build_item_desc('标题', '正文'), '标题\n\n正文')

    def test_is_idempotent(self):
        once = ItemPublisher.build_item_desc('标题', '正文')
        self.assertEqual(ItemPublisher.build_item_desc('标题', once), once)

    def test_missing_title_or_description(self):
        self.assertEqual(ItemPublisher.build_item_desc('', '正文'), '正文')
        self.assertEqual(ItemPublisher.build_item_desc('标题', ''), '标题')
        self.assertEqual(ItemPublisher.build_item_desc('', ''), '')

    def test_strips_whitespace(self):
        self.assertEqual(ItemPublisher.build_item_desc('  标题  ', '  正文  '), '标题\n\n正文')

    def test_short_title_prefix_is_not_treated_as_present(self):
        # 用 startswith 判断会把「描述恰好以标题同字开头」误当成已包含标题，
        # 结果详情页第一行根本不是标题。
        self.assertEqual(ItemPublisher.build_item_desc('AI', 'AI绘画教程合集'), 'AI\n\nAI绘画教程合集')
        self.assertEqual(ItemPublisher.build_item_desc('英语', '英语四级资料包'), '英语\n\n英语四级资料包')

    def test_title_in_first_line_only_is_idempotent(self):
        self.assertEqual(ItemPublisher.build_item_desc('AI', 'AI\n\n正文'), 'AI\n\n正文')


class PublishPayloadTextTests(unittest.TestCase):
    def make_publisher(self):
        publisher = ItemPublisher.__new__(ItemPublisher)
        publisher.cookie_id = 'test-cookie'
        publisher._apply_delivery_settings = mock.Mock()
        publisher._apply_price_settings = mock.Mock()
        return publisher

    def build_payload(self, title, description):
        return self.make_publisher()._build_publish_payload(
            title=title,
            description=description,
            uploaded_images=[],
            channel_res={},
            location={},
            current_price=9.9,
            original_price=None,
            delivery_choice='无需邮寄',
            post_price=None,
            can_self_pickup=False,
        )

    def test_desc_starts_with_title(self):
        payload = self.build_payload('测试标题', '测试描述')
        self.assertEqual(payload['itemTextDTO']['desc'], '测试标题\n\n测试描述')
        self.assertEqual(payload['itemTextDTO']['title'], '测试标题')

    def test_desc_keeps_title_when_description_missing(self):
        # publish_item 的描述回退成标题时，两者相等，旧写法会让
        # titleDescSeparate 变成 False。
        payload = self.build_payload('测试标题', '测试标题')
        self.assertEqual(payload['itemTextDTO']['desc'], '测试标题')
        self.assertIs(payload['itemTextDTO']['titleDescSeparate'], True)

    def test_title_desc_separate_always_true(self):
        for description in ('测试描述', '测试标题', ''):
            with self.subTest(description=description):
                payload = self.build_payload('测试标题', description)
                self.assertIs(payload['itemTextDTO']['titleDescSeparate'], True)


if __name__ == '__main__':
    unittest.main()
