"""Dependency-free regression tests against the actual parser methods."""
import ast
import asyncio
import copy
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Dict
import unittest

source = Path(__file__).parents[1] / 'utils' / 'order_detail_fetcher.py'
tree = ast.parse(source.read_text(encoding='utf-8'))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'OrderDetailFetcher')
names = {'_extract_order_status_from_response', '_extract_status_from_text',
         '_extract_status_matches_from_text', '_get_status_priority', '_reset_amount_capture',
         '_get_order_status', '_wait_for_response_capture_tasks'}
cls.body = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
namespace = {'re': re, 'Dict': Dict, 'asyncio': asyncio,
             'logger': SimpleNamespace(info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None)}
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[])), str(source), 'exec'), namespace)
Parser = namespace['OrderDetailFetcher']


class StructuredOrderStatusTests(unittest.TestCase):
    def setUp(self):
        self.parser = Parser()
        self.payload = {'api': 'mtop.idle.web.trade.order.detail', 'ret': ['SUCCESS::调用成功'],
                        'data': {'orderId': '123', 'components': [
                            {'render': 'orderStatusVO', 'data': {
                                'orderStatusInfo': {'title': '买家已付款，请尽快发货'},
                                'orderStatusNodeList': [{'title': '待收货'}, {'title': '交易成功'}]}}]}}

    def parse(self):
        return self.parser._extract_order_status_from_response(self.payload, '123')

    def test_paid_order_ignores_future_timeline(self):
        self.assertEqual(self.parse(), 'pending_ship')

    def test_wrong_order_rejected(self):
        self.payload['data']['orderId'] = '456'
        self.assertEqual(self.parse(), 'unknown')

    def test_failed_response_rejected(self):
        self.payload['ret'] = ['FAIL_SYS_SESSION_EXPIRED']
        self.assertEqual(self.parse(), 'unknown')

    def test_unpaid_shipped_refund_cancelled(self):
        for title, status in [('等待买家付款', 'pending_payment'), ('等待买家收货', 'shipped'),
                              ('交易成功', 'completed'), ('退款中', 'refunding'), ('交易关闭', 'cancelled')]:
            with self.subTest(title=title):
                self.payload['data']['components'][0]['data']['orderStatusInfo']['title'] = title
                self.assertEqual(self.parse(), status)

    def test_conflicting_components_rejected(self):
        other = copy.deepcopy(self.payload['data']['components'][0])
        other['data']['orderStatusInfo']['title'] = '交易关闭'
        self.payload['data']['components'].append(other)
        self.assertEqual(self.parse(), 'unknown')

    def test_missing_and_malformed_rejected(self):
        for value in (None, [], {}, {'data': {}}, {'api': 'other'}):
            self.assertEqual(self.parser._extract_order_status_from_response(value, '123'), 'unknown')

    def test_numeric_status_alone_not_trusted(self):
        self.payload['data']['components'] = []
        self.payload['data']['status'] = '2'
        self.assertEqual(self.parse(), 'unknown')

    def test_reset_prevents_cross_order_reuse(self):
        self.parser._captured_order_status = 'pending_ship'
        self.parser._reset_amount_capture('456')
        self.assertEqual(self.parser._captured_order_status, 'unknown')

    def test_interface_status_used_without_dom(self):
        self.parser._reset_amount_capture('123')
        self.parser._captured_order_status = self.parse()
        self.assertEqual(asyncio.run(self.parser._get_order_status()), 'pending_ship')
        self.assertEqual(self.parser._last_order_status_source, 'structured_response')


if __name__ == '__main__':
    unittest.main()
