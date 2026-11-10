"""发布载荷的单品库存回归：不再固定为 1，且可配置、可裁剪。"""

import os
import unittest
from unittest import mock

from utils.item_publisher import (
    DEFAULT_ITEM_QUANTITY,
    DEFAULT_ITEM_QUANTITY_FALLBACK,
    MAX_ITEM_QUANTITY,
    ItemPublisher,
    _parse_default_quantity,
    normalize_item_quantity,
)


class ParseDefaultQuantityTests(unittest.TestCase):
    def test_reads_configured_value(self):
        with mock.patch.dict(os.environ, {'PUBLISH_ITEM_QUANTITY': '120'}):
            self.assertEqual(_parse_default_quantity(), 120)

    def test_invalid_value_falls_back_instead_of_raising(self):
        # 模块级 int(os.getenv(...)) 会在 import 阶段抛 ValueError，整个服务起不来。
        for raw in ('abc', '', ' ', '8.5', '0', '-5'):
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {'PUBLISH_ITEM_QUANTITY': raw}):
                self.assertEqual(_parse_default_quantity(), DEFAULT_ITEM_QUANTITY_FALLBACK)

    def test_clips_over_limit(self):
        with mock.patch.dict(os.environ, {'PUBLISH_ITEM_QUANTITY': str(MAX_ITEM_QUANTITY + 100)}):
            self.assertEqual(_parse_default_quantity(), MAX_ITEM_QUANTITY)

    def test_missing_variable_uses_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(_parse_default_quantity(), DEFAULT_ITEM_QUANTITY_FALLBACK)


class NormalizeItemQuantityTests(unittest.TestCase):
    def test_keeps_positive_quantity(self):
        self.assertEqual(normalize_item_quantity(200), 200)
        self.assertEqual(normalize_item_quantity('200'), 200)

    def test_clips_over_limit(self):
        self.assertEqual(normalize_item_quantity(MAX_ITEM_QUANTITY + 1), MAX_ITEM_QUANTITY)

    def test_falls_back_on_invalid_value(self):
        for value in (None, '', 'abc', 0, -3, [], {}):
            with self.subTest(value=value):
                self.assertEqual(normalize_item_quantity(value), DEFAULT_ITEM_QUANTITY)

    def test_default_is_not_single_unit(self):
        # 硬编码 1 会让商品成交一次就库存归零并被平台自动下架。
        self.assertGreater(DEFAULT_ITEM_QUANTITY, 1)


class PublishPayloadQuantityTests(unittest.TestCase):
    def make_publisher(self):
        publisher = ItemPublisher.__new__(ItemPublisher)
        publisher.cookie_id = 'test-cookie'
        publisher._apply_delivery_settings = mock.Mock()
        publisher._apply_price_settings = mock.Mock()
        return publisher

    def build_payload(self, **overrides):
        kwargs = {
            'title': '测试标题',
            'description': '测试描述',
            'uploaded_images': [],
            'channel_res': {},
            'location': {},
            'current_price': 9.9,
            'original_price': None,
            'delivery_choice': '无需邮寄',
            'post_price': None,
            'can_self_pickup': False,
        }
        kwargs.update(overrides)
        return self.make_publisher()._build_publish_payload(**kwargs)

    def test_payload_uses_configured_quantity(self):
        self.assertEqual(self.build_payload(quantity=200)['quantity'], '200')

    def test_payload_clips_and_falls_back(self):
        self.assertEqual(self.build_payload(quantity=MAX_ITEM_QUANTITY + 5)['quantity'], str(MAX_ITEM_QUANTITY))
        self.assertEqual(self.build_payload(quantity=None)['quantity'], str(DEFAULT_ITEM_QUANTITY))

    def test_payload_default_is_not_single_unit(self):
        self.assertNotEqual(self.build_payload()['quantity'], '1')


if __name__ == '__main__':
    unittest.main()
