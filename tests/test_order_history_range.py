"""订单历史区间过滤：两个边界各自独立生效。

原实现要求 utc_start 与 utc_end_exclusive 同时存在，否则直接返回 'in_range'，
只传起点时过滤完全失效 —— 表现为「明明给了开始时间，还是把整段历史都拉回来」。
"""

import unittest

from utils.order_history_sync import classify_order_history_range


START = '2026-11-01 00:00:00'
END = '2026-11-05 00:00:00'


class ClassifyOrderHistoryRangeTests(unittest.TestCase):
    def test_no_bounds_keeps_everything(self):
        self.assertEqual(classify_order_history_range('2026-11-03 12:00:00'), 'in_range')
        self.assertEqual(classify_order_history_range(None), 'in_range')

    def test_both_bounds(self):
        self.assertEqual(classify_order_history_range('2026-10-31 23:59:59', START, END), 'before')
        self.assertEqual(classify_order_history_range(START, START, END), 'in_range')
        self.assertEqual(classify_order_history_range('2026-11-04 23:59:59', START, END), 'in_range')
        self.assertEqual(classify_order_history_range(END, START, END), 'after')

    def test_start_bound_only(self):
        # 只给起点：早于起点的必须被滤掉，晚于起点的保留。
        self.assertEqual(classify_order_history_range('2026-10-31 23:59:59', START), 'before')
        self.assertEqual(classify_order_history_range(START, START), 'in_range')
        self.assertEqual(classify_order_history_range('2026-11-30 00:00:00', START), 'in_range')

    def test_end_bound_only(self):
        # 只给终点：晚于终点（独占）的必须被滤掉，早于终点的保留。
        self.assertEqual(classify_order_history_range('2026-11-09 00:00:00', None, END), 'after')
        self.assertEqual(classify_order_history_range(END, None, END), 'after')
        self.assertEqual(classify_order_history_range('2026-10-01 00:00:00', None, END), 'in_range')

    def test_unparsable_anchor_is_unknown(self):
        self.assertEqual(classify_order_history_range(None, START, END), 'unknown')
        self.assertEqual(classify_order_history_range('not-a-time', START, END), 'unknown')

    def test_invalid_bound_is_unknown(self):
        self.assertEqual(classify_order_history_range('2026-11-03 12:00:00', 'bad-start'), 'unknown')
        self.assertEqual(classify_order_history_range('2026-11-03 12:00:00', None, 'bad-end'), 'unknown')

    def test_timezone_suffix_is_accepted(self):
        self.assertEqual(
            classify_order_history_range('2026-10-31T23:00:00+08:00', START),
            'before',
        )


if __name__ == '__main__':
    unittest.main()
