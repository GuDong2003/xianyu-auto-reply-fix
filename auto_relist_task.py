"""SQLite 自动续售：仅已成交并完成发货、原商品不在售且仍有库存时发布。"""

import asyncio
import time

from loguru import logger

from utils.relist_store import RelistStore


class AutoRelistService:
    def __init__(self, database):
        self.db = database
        self.store = RelistStore(database)

    async def _snapshot(self, rule):
        from XianyuAutoAsync import XianyuLive, ConnectionState
        from reply_server import _run_live_instance_on_manager_loop
        live = XianyuLive.get_instance(rule['cookie_id'])
        if not live or live.connection_state != ConnectionState.CONNECTED:
            raise ValueError('账号尚未连接，等待恢复后检查')
        if live.user_id is not None and live.user_id != rule['user_id']:
            raise ValueError('账号实例归属不一致')
        return await _run_live_instance_on_manager_loop(
            rule['cookie_id'], lambda: live.get_all_items(sync_item_details=False), timeout=180,
        )

    async def _publish(self, rule, material, event):
        from reply_server import _publish_product_to_account
        owner = self.db.get_user_by_id(rule['user_id'])
        if not owner:
            raise ValueError('规则用户不存在')
        return await _publish_product_to_account(
            current_user={'user_id': rule['user_id'], 'username': owner['username'], 'is_admin': bool(owner.get('is_admin'))},
            account_id=rule['cookie_id'], title=material['title'], description=material['description'],
            images=material.get('images') or [], current_price=material.get('price'), original_price=material.get('original_price'),
            delivery_choice=material.get('delivery_method') or '包邮', post_price=material.get('postage'),
            can_self_pickup=bool(material.get('can_self_pickup')), category_hint=material.get('category'),
            sku_config=material.get('sku_config'), material_id=material['id'], log_id=event['publish_log_id'],
            batch_id=f"relist_{event['id']}",
        )

    def _migrate(self, event):
        try:
            self.store.migrate(event['id'])
            return 'completed'
        except Exception as exc:
            self.store.event_error(event['id'], 'migration_retry', str(exc) if isinstance(exc, ValueError) else '关联迁移未完成')
            self.store.status(event['rule_id'], 'migration_retry', '新商品已发布，等待关联迁移；不会重复发布')
            return 'migration_retry'

    async def process(self, rule):
        event = self.store.event_for_item(rule['id'], rule['current_item_id'])
        if event:
            if event['state'] in {'publishing', 'unknown'}:
                recovered = self.store.recover_result(event)
                if recovered:
                    self.store.published(event['id'], recovered)
                    event = self.store.event(event['id'])
                elif event['state'] == 'publishing' and time.time() - event['started_at'] > 900:
                    self.store.event_error(event['id'], 'unknown', '发布任务未确认结果，请核对发布记录')
                    self.store.status(rule['id'], 'unknown', '发布结果待确认，不自动重复发布')
                    return 'unknown'
                else:
                    return event['state']
            if event['state'] in {'published', 'migration_retry'}:
                return self._migrate(event)
            return event['state']
        if not rule['enabled'] or rule['next_run'] > time.time():
            return 'skipped'
        try:
            material, _ = self.store.validate_resources(rule)
            if self.db.get_all_cookie_status().get(rule['cookie_id'], True) is False:
                raise ValueError('账号已停用')
            order = self.store.eligible_order(rule)
            if not order:
                return 'waiting'
            if not order.get('delivered_at') or time.time() < order['delivered_at'] + rule['delay_seconds']:
                return 'waiting'
            snapshot = await self._snapshot(rule)
            if snapshot.get('success') is not True or snapshot.get('complete') is not True:
                raise ValueError('未获得完整在售列表，本轮不发布')
            item_ids = {str(item.get('id')) for item in snapshot.get('items', [])}
            if rule['current_item_id'] in item_ids:
                self.store.status(rule['id'], 'watching', '原商品仍在售，无需重新发布')
                return 'still_listed'
            existing_ids = {str(row['item_id']) for row in self.store.recent_publish_candidates(rule, order['delivered_at'])} & item_ids
            # 重新检查订单，避免等待平台查询期间已退款或取消。
            current_order = self.db.get_order_by_id(order['order_id']) or {}
            if current_order.get('cookie_id') != rule['cookie_id'] or current_order.get('order_status') not in {'shipped', 'completed'}:
                return 'waiting'
            event, created = self.store.stage(rule, order, material)
            if not created:
                return event['state']
            if len(existing_ids) > 1:
                raise ValueError('找到多个可能已发布的商品，请人工确认关联')
            if existing_ids:
                adopted_id = existing_ids.pop()
                self.db.update_publish_log(event['publish_log_id'],status='success',item_id=adopted_id,
                                           sync_status='success',sync_message='复用已确认的发布结果，未重复发布')
                self.store.published(event['id'], adopted_id)
                return self._migrate(self.store.event(event['id']))
            result = await self._publish(rule, material, event)
            new_id = str(result.get('published_item_id') or '').strip()
            if not result.get('success') or not new_id:
                raise RuntimeError('发布未返回可确认的新商品ID')
            self.store.published(event['id'], new_id)
            return self._migrate(self.store.event(event['id']))
        except asyncio.CancelledError:
            if event:
                self.store.event_error(event['id'], 'unknown', '发布任务取消，结果待确认')
                self.store.status(rule['id'], 'unknown', '发布结果待确认，不自动重复发布')
            raise
        except Exception as exc:
            if event:
                self.store.event_error(event['id'], 'unknown', '发布结果待确认，请核对发布记录')
                self.store.status(rule['id'], 'unknown', '发布结果待确认，不自动重复发布')
                return 'unknown'
            self.store.status(rule['id'], 'paused', str(exc) if isinstance(exc, ValueError) else '续售检查失败，稍后重试')
            return 'paused'

    async def run_once(self, user_id=None, rule_id=None):
        results = []
        for rule in self.store.rules(user_id):
            if rule_id is None or rule['id'] == rule_id:
                results.append({'rule_id': rule['id'], 'status': await self.process(rule)})
        return results

    async def associate_result(self, event_id, user_id, new_item_id):
        event = self.store.event(event_id, user_id)
        if not event or event['state'] not in {'unknown', 'publishing', 'published', 'migration_retry'}:
            raise ValueError('续售事件不存在或不需要确认')
        rule = self.store.rule(event['rule_id'], user_id)
        self.store.validate_resources(rule, stock=False)
        snapshot = await self._snapshot(rule)
        if snapshot.get('success') is not True or snapshot.get('complete') is not True:
            raise ValueError('未获得完整在售列表，无法核验新商品')
        ids = {str(item.get('id')) for item in snapshot.get('items', [])}
        if new_item_id not in ids or new_item_id == event['old_item_id']:
            raise ValueError('新商品不在该账号的在售列表中')
        self.store.published(event_id, new_item_id)
        return self._migrate(self.store.event(event_id))


async def auto_relist_task_loop(interval_seconds=60):
    from db_manager import db_manager
    service = AutoRelistService(db_manager)
    while True:
        try:
            await service.run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('自动续售检查未完成: {}', type(exc).__name__)
        await asyncio.sleep(max(30, interval_seconds))
