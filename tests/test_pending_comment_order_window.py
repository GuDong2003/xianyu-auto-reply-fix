"""补评价取单时间窗的口径回归。

``get_pending_auto_comment_orders(days=N)`` 的语义是「最近 N 天内完成的订单」。
它原先用 ``COALESCE(platform_completed_at, updated_at, created_at)`` 做时间判断，
但 ``updated_at`` 是「记录最后修改时间」—— 任何一次订单同步或状态更新都会把它刷成当前时间
（``update_order_*`` 里就有 ``update_fields.append("updated_at = CURRENT_TIMESTAMP")``）。

结果是：只要订单被同步过一次，无论实际完成多久以前，都会被判定成「最近 N 天内完成」，
``days`` 参数形同虚设。而闲鱼的主评价期只有交易成功后 15 天，超期后接口必然失败，
这些无谓调用既浪费时间，也增加账号被判定异常操作的风险。

本回归锁住：时间窗按订单完成/创建时间计算，不随 ``updated_at`` 刷新而失效。
"""

from datetime import datetime, timedelta
import unittest

from support import IsolatedDatabaseMixin


def _ts(**kwargs) -> str:
    return (datetime.now() - timedelta(**kwargs)).strftime('%Y-%m-%d %H:%M:%S')


class PendingAutoCommentWindowTests(IsolatedDatabaseMixin, unittest.TestCase):

    def setUp(self):
        with self.db.conn:
            self.db.conn.execute('DELETE FROM orders')

    def _insert_order(self, order_id: str, *, created_at: str,
                      updated_at: str, completed_at=None) -> None:
        with self.db.conn:
            self.db.conn.execute(
                """
                INSERT INTO orders (
                    order_id, item_id, buyer_id, cookie_id, order_status,
                    is_rated, created_at, updated_at, platform_completed_at
                ) VALUES (?, ?, ?, ?, 'completed', 0, ?, ?, ?)
                """,
                (order_id, 'item-1', 'buyer-1', 'seller-account',
                 created_at, updated_at, completed_at),
            )

    def test_stale_order_is_not_resurrected_by_updated_at(self):
        """30 天前创建的订单，即使 updated_at 刚被同步刷新，也不该进入 7 天窗口。"""
        self._insert_order(
            'stale-order',
            created_at=_ts(days=30),
            updated_at=_ts(minutes=1),   # 被订单同步刷新过
        )
        pending = self.db.get_pending_auto_comment_orders('seller-account', limit=10, days=7)
        self.assertEqual([], pending, 'updated_at 被刷新后，老订单不该重新进入取单窗口')

    def test_recent_order_is_included(self):
        """真正 2 天前创建的订单要正常进入窗口。"""
        self._insert_order(
            'fresh-order',
            created_at=_ts(days=2),
            updated_at=_ts(minutes=1),
        )
        pending = self.db.get_pending_auto_comment_orders('seller-account', limit=10, days=7)
        self.assertEqual(['fresh-order'], [row['order_id'] for row in pending])

    def test_platform_completed_at_takes_priority(self):
        """有平台完成时间时，以完成时间为准，而不是创建时间。"""
        self._insert_order(
            'old-created-recently-completed',
            created_at=_ts(days=30),
            updated_at=_ts(minutes=1),
            completed_at=_ts(days=2),
        )
        pending = self.db.get_pending_auto_comment_orders('seller-account', limit=10, days=7)
        self.assertEqual(
            ['old-created-recently-completed'],
            [row['order_id'] for row in pending],
            '应优先以 platform_completed_at 判断，订单实际是 2 天前完成的',
        )
