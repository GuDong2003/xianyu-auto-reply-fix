"""评价成功后的可选消息；统一各评价入口并持久化防重复状态。"""

import asyncio

from loguru import logger


async def _send_on_account_loop(cookie_id, factory):
    from reply_server import _run_live_instance_on_manager_loop
    return await _run_live_instance_on_manager_loop(cookie_id, factory, timeout=15)


async def send_rate_thanks(cookie_id, order_id, *, live_instance=None, database=None):
    if database is None:
        from db_manager import db_manager
        database = db_manager
    attempt = None
    try:
        config = database.get_rate_thanks_config(cookie_id)
        content = str(config.get('content') or '').strip()
        if not config.get('enabled') or not content:
            return {'success': False, 'status': 'disabled'}
        order = database.get_order_by_id(order_id) or {}
        if order.get('cookie_id') != cookie_id or not order.get('is_rated'):
            return {'success': False, 'status': 'invalid_order'}
        buyer_id, chat_id = str(order.get('buyer_id') or '').strip(), str(order.get('sid') or '').strip()
        buyer_id = buyer_id.removesuffix('@goofish')
        chat_id = chat_id.removesuffix('@goofish')
        if not buyer_id or not chat_id:
            return {'success': False, 'status': 'missing_recipient'}
        from XianyuAutoAsync import XianyuLive, ConnectionState
        live = live_instance or XianyuLive.get_instance(cookie_id)
        if not live or live.connection_state != ConnectionState.CONNECTED or not live.ws:
            return {'success': False, 'status': 'not_connected'}
        if live.cookie_id != cookie_id or buyer_id == str(getattr(live, 'myid', '')):
            return {'success': False, 'status': 'invalid_recipient'}
        if getattr(live, 'user_id', None) is not None and live.user_id != config.get('user_id'):
            return {'success': False, 'status': 'invalid_owner'}
        if database.is_buyer_blacklisted(config['user_id'], buyer_id, cookie_id=cookie_id, item_id=order.get('item_id')):
            return {'success': False, 'status': 'blocked'}
        from message_filter_service import MessageFilterService
        filters = MessageFilterService(database).match_rules(
            config['user_id'], content, cookie_id=cookie_id, item_id=order.get('item_id'), message_source='system',
        )
        if filters.get('skip_auto_reply'):
            return {'success': False, 'status': 'filtered'}
        attempt = database.claim_rate_thanks(cookie_id, order_id)
        if not attempt:
            return {'success': False, 'status': 'already_claimed'}
        await _send_on_account_loop(cookie_id, lambda: live.send_msg(live.ws, chat_id, buyer_id, content))
        if not database.finish_rate_thanks(cookie_id, order_id, attempt, 'sent'):
            raise RuntimeError('发送完成状态未保存')
        database.add_scheduled_task_log(
            task_type='other_task', cookie_id=cookie_id, order_id=order_id,
            object_id=f'rate_thanks:{order_id}', status='success', message='好评后消息已提交',
        )
        return {'success': True, 'status': 'sent'}
    except asyncio.CancelledError:
        if attempt:
            try:
                database.finish_rate_thanks(cookie_id, order_id, attempt, 'unknown', '任务取消，发送结果待确认')
            except Exception:
                pass
        raise
    except Exception as exc:
        if attempt:
            try:
                database.finish_rate_thanks(cookie_id, order_id, attempt, 'unknown', type(exc).__name__)
                database.add_scheduled_task_log(
                    task_type='other_task', cookie_id=cookie_id, order_id=order_id,
                    object_id=f'rate_thanks:{order_id}', status='unknown', message='好评后消息发送结果待确认，未重复发送',
                )
            except Exception:
                pass
        logger.warning('好评后消息未完成: {}', type(exc).__name__)
        return {'success': False, 'status': 'unknown' if attempt else 'failed'}
