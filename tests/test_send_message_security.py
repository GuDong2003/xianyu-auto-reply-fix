"""发送接口鉴权回归：仅使用临时数据库和模拟连接，不启动账号或定时任务。"""

from contextlib import ExitStack, chdir
from enum import Enum
import importlib
import os
from pathlib import Path
import secrets
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from loguru import logger


PUBLIC_KEYS = (
    'xianyu_qq_reply_2024',
    'xianyu_api_secret_2024',
    'zhinina_test_key',
)


class ConnectionState(Enum):
    CONNECTED = 'connected'
    DISCONNECTED = 'disconnected'


class SendMessageSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.resources = ExitStack()
        cls.addClassCleanup(cls.resources.close)
        cls.temp_dir = cls.resources.enter_context(tempfile.TemporaryDirectory())
        repo_root = str(Path(__file__).resolve().parents[1])
        cls.resources.enter_context(mock.patch.object(sys, 'path', [repo_root, *sys.path]))
        cls.resources.enter_context(chdir(cls.temp_dir))
        cls.resources.enter_context(mock.patch.dict(os.environ, {
            'DB_PATH': str(Path(cls.temp_dir) / 'security-tests.db'),
            'SECRET_ENCRYPTION_KEY': Fernet.generate_key().decode(),
            'SQL_LOG_ENABLED': 'false',
        }))
        # 禁止应用日志和监控线程；浏览器运行时不属于此测试的验证范围。
        cls.resources.enter_context(mock.patch.object(logger, '_log'))
        cls.resources.enter_context(mock.patch('file_log_collector.setup_file_logging'))
        cls.resources.enter_context(mock.patch('utils.node_runtime.configure_playwright_node'))
        db_was_loaded = 'db_manager' in sys.modules
        cls.db_module = importlib.import_module('db_manager')
        if not db_was_loaded:
            cls.resources.callback(cls.db_module.db_manager.close)
        # 即使其他测试提前导入了模块，也不复用或关闭它们的全局数据库。
        cls.db = cls.db_module.DBManager(str(Path(cls.temp_dir) / 'send-api-fixtures.db'))
        cls.resources.callback(cls.db.close)
        cls.resources.enter_context(mock.patch.object(cls.db_module, 'db_manager', cls.db))
        cls.server = importlib.import_module('reply_server')
        cls.resources.enter_context(mock.patch.object(cls.server, 'db_manager', cls.db))
        # 不使用 TestClient 的 lifespan 上下文，避免启动后台任务。
        cls.client = TestClient(cls.server.app)
        cls.resources.callback(cls.client.close)
        for username in ('send_owner', 'send_other'):
            if not cls.db.create_user(username, f'{username}@example.invalid', secrets.token_urlsafe(24)):
                raise RuntimeError('创建隔离测试用户失败')
        cls.owner_id = cls.db.get_user_by_username('send_owner')['id']
        cls.other_id = cls.db.get_user_by_username('send_other')['id']
        cls.admin_id = cls.db.get_user_by_username('admin')['id']
        if not cls.db.save_cookie('owned-account', 'synthetic-cookie', cls.owner_id):
            raise RuntimeError('创建隔离测试账号失败')

    def setUp(self):
        self.api_key = secrets.token_urlsafe(32)
        self.db.set_system_setting('qq_reply_secret_key', self.api_key)
        self.tokens = {name: secrets.token_urlsafe(32) for name in ('owner', 'other', 'admin')}
        sessions = {
            self.tokens[name]: {
                'user_id': user_id,
                'username': username,
                'is_admin': name == 'admin',
                'timestamp': time.time(),
            }
            for name, user_id, username in (
                ('owner', self.owner_id, 'send_owner'),
                ('other', self.other_id, 'send_other'),
                ('admin', self.admin_id, 'admin'),
            )
        }
        self.enterContext(mock.patch.object(self.server, 'SESSION_TOKENS', sessions))
        self.live = SimpleNamespace(
            connection_state=ConnectionState.CONNECTED,
            ws=object(),
            send_msg=mock.AsyncMock(),
        )
        fake_live_module = ModuleType('XianyuAutoAsync')
        fake_live_module.ConnectionState = ConnectionState
        fake_live_module.XianyuLive = mock.Mock()
        fake_live_module.XianyuLive.get_instance.return_value = self.live
        self.get_instance = fake_live_module.XianyuLive.get_instance
        self.enterContext(mock.patch.dict(sys.modules, {'XianyuAutoAsync': fake_live_module}))
        self.blacklist = self.enterContext(mock.patch.object(
            self.server, '_get_blacklist_block_by_cookie', return_value=None,
        ))

        async def run_on_manager_loop(cookie_id, factory, timeout):
            return await factory()

        self.manager_send = self.enterContext(mock.patch.object(
            self.server, '_run_live_instance_on_manager_loop',
            new=mock.AsyncMock(side_effect=run_on_manager_loop),
        ))

    def headers(self, user='owner'):
        return {'Authorization': f'Bearer {self.tokens[user]}'}

    def send(self, user='owner', **overrides):
        body = {
            'api_key': self.api_key,
            'cookie_id': 'owned-account',
            'chat_id': 'synthetic-chat',
            'to_user_id': 'synthetic-recipient',
            'message': 'synthetic-message',
        }
        body.update(overrides)
        return self.client.post('/send-message', json=body, headers=self.headers(user) if user else {})

    def assert_no_account_access(self):
        self.blacklist.assert_not_called()
        self.get_instance.assert_not_called()
        self.manager_send.assert_not_awaited()
        self.live.send_msg.assert_not_awaited()

    def test_anonymous_requests_rejected_with_custom_or_public_keys(self):
        for key in (self.api_key, *PUBLIC_KEYS):
            with self.subTest(key_kind='custom' if key == self.api_key else 'public'):
                self.assertEqual(self.send(user=None, api_key=key).status_code, 401)
        self.assert_no_account_access()

    def test_invalid_or_expired_login_rejected(self):
        self.tokens['owner'] = secrets.token_urlsafe(32)
        self.assertEqual(self.send().status_code, 401)
        self.server.SESSION_TOKENS[self.tokens['owner']] = {
            'user_id': self.owner_id,
            'username': 'send_owner',
            'timestamp': time.time() - self.server.TOKEN_EXPIRE_TIME - 1,
        }
        self.assertEqual(self.send().status_code, 401)
        self.assert_no_account_access()

    def test_other_users_and_admin_cannot_send_from_owner_account(self):
        for user in ('other', 'admin'):
            with self.subTest(user=user):
                self.assertEqual(self.send(user=user).status_code, 403)
        self.assert_no_account_access()

    def test_item_sync_requires_login(self):
        for endpoint in ('/items/get-all-from-account', '/items/get-by-page'):
            response = self.client.post(endpoint, json={'cookie_id': 'owned-account'})
            self.assertEqual(response.status_code, 401)
        sys.modules['XianyuAutoAsync'].XianyuLive.assert_not_called()

    def test_item_sync_denies_foreign_and_missing_accounts_before_cookie_lookup(self):
        constructor = sys.modules['XianyuAutoAsync'].XianyuLive
        with mock.patch.object(self.db, 'get_cookie_by_id', wraps=self.db.get_cookie_by_id) as lookup:
            for endpoint in ('/items/get-all-from-account', '/items/get-by-page'):
                for user, cookie_id in (
                    ('other', 'owned-account'),
                    ('admin', 'owned-account'),
                    ('owner', 'missing-account'),
                ):
                    with self.subTest(endpoint=endpoint, user=user, cookie_id=cookie_id):
                        response = self.client.post(
                            endpoint, headers=self.headers(user), json={'cookie_id': cookie_id},
                        )
                        self.assertEqual(response.status_code, 403)
            lookup.assert_not_called()
        constructor.assert_not_called()

    def test_owner_can_sync_own_account_with_normalized_id(self):
        self.live.get_all_items = mock.AsyncMock(return_value={
            'success': True, 'total_count': 1, 'total_pages': 1,
        })
        self.live.get_item_list_info = mock.AsyncMock(return_value={
            'success': True, 'current_count': 1,
        })
        self.live.close_session = mock.AsyncMock()
        constructor = sys.modules['XianyuAutoAsync'].XianyuLive
        constructor.return_value = self.live
        for endpoint in ('/items/get-all-from-account', '/items/get-by-page'):
            response = self.client.post(
                endpoint, headers=self.headers(), json={'cookie_id': ' owned-account '},
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()['success'])
            self.assertEqual(constructor.call_args.args[1], 'owned-account')
        self.live.get_all_items.assert_awaited_once_with(sync_item_details=True)
        self.live.get_item_list_info.assert_awaited_once_with(1, 20, sync_item_details=True)
        self.assertEqual(self.live.close_session.await_count, 2)

    def test_item_sync_failure_closes_session_and_does_not_report_success(self):
        self.live.get_all_items = mock.AsyncMock(return_value={
            'success': False, 'partial': True, 'total_count': 20, 'error': 'Synthetic failure',
        })
        self.live.get_item_list_info = mock.AsyncMock(side_effect=RuntimeError('Synthetic failure'))
        self.live.close_session = mock.AsyncMock()
        sys.modules['XianyuAutoAsync'].XianyuLive.return_value = self.live
        for endpoint in ('/items/get-all-from-account', '/items/get-by-page'):
            response = self.client.post(endpoint, headers=self.headers(), json={'cookie_id': 'owned-account'})
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['success'])
        self.assertEqual(self.live.close_session.await_count, 2)

    def test_item_lists_hide_unlisted_by_default_and_keep_details_accessible(self):
        self.db.conn.execute('''INSERT OR REPLACE INTO item_info
            (cookie_id, item_id, item_title, is_listed) VALUES ('owned-account', 'old-item', 'Old item', 0)''')
        self.db.conn.commit()
        for endpoint in ('/items', '/items/cookie/owned-account'):
            response = self.client.get(endpoint, headers=self.headers())
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('old-item', [item['item_id'] for item in response.json()['items']])
            response = self.client.get(endpoint + '?include_unlisted=true', headers=self.headers())
            self.assertEqual(response.status_code, 200)
            self.assertIn('old-item', [item['item_id'] for item in response.json()['items']])
        response = self.client.get('/items/owned-account/old-item', headers=self.headers())
        self.assertEqual(response.status_code, 200)

    def test_missing_and_foreign_accounts_have_same_denial(self):
        foreign = self.send(user='other')
        missing = self.send(cookie_id='missing-account')
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.json(), foreign.json())
        self.assert_no_account_access()

    def test_owner_with_configured_key_sends_on_manager_loop(self):
        response = self.send(cookie_id=' owned-account ')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['success'])
        self.get_instance.assert_called_once_with('owned-account')
        self.live.send_msg.assert_awaited_once_with(
            self.live.ws, 'synthetic-chat', 'synthetic-recipient', 'synthetic-message',
        )
        self.manager_send.assert_awaited_once()
        self.assertEqual(self.manager_send.call_args.args[0], 'owned-account')

    def test_incorrect_and_modified_keys_rejected(self):
        for key in ('', ' ', 'incorrect', *PUBLIC_KEYS, self.api_key + '\n', self.api_key + '\\n'):
            response = self.send(api_key=key)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['success'])
        self.assert_no_account_access()

    def test_public_keys_in_existing_database_are_never_accepted(self):
        for key in PUBLIC_KEYS:
            self.db.set_system_setting('qq_reply_secret_key', key)
            self.assertFalse(self.send(api_key=key).json()['success'])
        self.assert_no_account_access()

    def test_empty_or_missing_configuration_has_no_fallback(self):
        for configured in ('', '   ', None):
            with mock.patch.object(self.db, 'get_system_setting', return_value=configured):
                for key in (self.api_key, *PUBLIC_KEYS):
                    self.assertFalse(self.send(api_key=key).json()['success'])
        self.assert_no_account_access()

    def test_database_error_denies_sending(self):
        with mock.patch.object(self.db, 'get_system_setting', side_effect=RuntimeError('unavailable')):
            self.assertFalse(self.send().json()['success'])
        self.assert_no_account_access()

    def test_configured_key_compared_exactly_in_constant_time(self):
        key = secrets.token_urlsafe(24) + '中文'
        self.db.set_system_setting('qq_reply_secret_key', key)
        compare_digest = self.server.hmac.compare_digest
        with mock.patch.object(self.server.hmac, 'compare_digest', wraps=compare_digest) as compare:
            self.assertTrue(self.server.verify_api_key(key))
            compare.assert_called_once_with(key.encode('utf-8'), key.encode('utf-8'))
        self.assertFalse(self.server.verify_api_key(key + ' '))

    def test_blacklist_still_blocks_owner(self):
        self.blacklist.return_value = {'scope': 'personal'}
        with mock.patch.object(self.server, '_format_blacklist_block_message', return_value='blocked'):
            self.assertFalse(self.send().json()['success'])
        self.get_instance.assert_not_called()
        self.live.send_msg.assert_not_awaited()

    def test_disconnected_owned_account_does_not_send(self):
        self.live.connection_state = ConnectionState.DISCONNECTED
        self.assertFalse(self.send().json()['success'])
        self.live.send_msg.assert_not_awaited()
        self.manager_send.assert_not_awaited()

    def test_non_admin_cannot_read_or_change_send_key(self):
        response = self.client.get('/system-settings', headers=self.headers())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('qq_reply_secret_key', response.json())
        response = self.client.put(
            '/system-settings/qq_reply_secret_key',
            json={'value': secrets.token_urlsafe(32)}, headers=self.headers(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.db.get_system_setting('qq_reply_secret_key'), self.api_key)

    def test_admin_can_configure_and_disable_send_key(self):
        response = self.client.get('/system-settings', headers=self.headers('admin'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('qq_reply_secret_key', response.json())
        for value in (secrets.token_urlsafe(32), ''):
            response = self.client.put(
                '/system-settings/qq_reply_secret_key',
                json={'value': value}, headers=self.headers('admin'),
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.db.get_system_setting('qq_reply_secret_key'), value)
        self.assertFalse(self.send().json()['success'])

    def test_admin_cannot_restore_public_or_short_keys(self):
        for value in (*PUBLIC_KEYS, 'short'):
            response = self.client.put(
                '/system-settings/qq_reply_secret_key',
                json={'value': value}, headers=self.headers('admin'),
            )
            self.assertEqual(response.status_code, 400)
        self.assertEqual(self.db.get_system_setting('qq_reply_secret_key'), self.api_key)

    def test_new_database_disables_send_by_default(self):
        database = self.db_module.DBManager(str(Path(self.temp_dir) / 'fresh.db'))
        try:
            self.assertEqual(database.get_system_setting('qq_reply_secret_key'), '')
        finally:
            database.close()

    def test_upgrade_revokes_public_keys_and_preserves_custom_key(self):
        for key in (*PUBLIC_KEYS, self.api_key):
            self.db.set_system_setting('qq_reply_secret_key', key)
            self.db.close()
            self.db.init_db()
            expected = self.api_key if key == self.api_key else ''
            self.assertEqual(self.db.get_system_setting('qq_reply_secret_key'), expected)
        # 重复升级也应保留自定义秘钥。
        self.db.close()
        self.db.init_db()
        self.assertEqual(self.db.get_system_setting('qq_reply_secret_key'), self.api_key)


if __name__ == '__main__':
    unittest.main()
