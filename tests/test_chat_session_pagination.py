import ast
import logging
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest import mock

from fastapi import HTTPException


def load_pagination_functions():
    """Exercise the endpoint without starting database or browser runtimes."""
    path = Path(__file__).resolve().parents[1] / 'reply_server.py'
    functions = []
    for node in ast.parse(path.read_text(encoding='utf-8')).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in {
            'get_chat_sessions', '_merge_chat_session_sources', '_clean_goofish_id',
        }:
            node.decorator_list = []
            if node.name == 'get_chat_sessions':
                node.args.defaults[-1] = ast.Constant(value=None)
            functions.append(node)
    module = ModuleType('chat_pagination_under_test')
    module.__dict__.update(
        Any=Any, Dict=Dict, List=List, Optional=Optional,
        HTTPException=HTTPException, time=time, logger=logging.getLogger(__name__),
        mask_sensitive_text=str, safe_client_error=str,
        db_manager=SimpleNamespace(get_chat_sessions=lambda *args, **kwargs: []),
    )
    tree = ast.fix_missing_locations(ast.Module(body=functions, type_ignores=[]))
    exec(compile(tree, str(path), 'exec'), module.__dict__)
    return module


class ChatSessionPaginationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.server = load_pagination_functions()
        self.invalid_first_session = False
        live = SimpleNamespace(myid='seller', list_newest_conversations=self.platform_page)

        async def run_on_manager(cookie_id, factory, timeout):
            return await factory()

        async def enrich(cookie_id, sessions, limit):
            return sessions

        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.multiple(
            self.server,
            create=True,
            CHAT_SESSION_REMOTE_CACHE={},
            CHAT_SESSION_REMOTE_COOLDOWN={},
            CHAT_SESSION_REMOTE_FAILURES={},
            _ensure_cookie_access=lambda cookie_id, user: cookie_id,
            _build_live_runtime_status=lambda cookie_id: {},
            _get_chat_live_instance=lambda cookie_id: live,
            _run_live_instance_on_manager_loop=run_on_manager,
            _chat_session_cache_get=lambda *args: None,
            _chat_session_cache_put=lambda *args: None,
            _normalize_remote_conversation_session=(
                lambda item, owner_user_id: None if item['invalid'] else item
            ),
            _annotate_chat_sessions=lambda cookie_id, sessions: sessions,
            _enrich_chat_sessions=enrich,
        ))
        stack.enter_context(mock.patch.object(
            self.server.db_manager, 'get_chat_sessions', return_value=[],
        ))

    async def platform_page(self, start_timestamp, limit):
        offset = int(start_timestamp or 0)
        end = min(offset + limit, 300)
        return {
            'userConvs': [
                {
                    'chat_id': f'chat-{index}',
                    'created_at': f'{300 - index:05d}',
                    'invalid': self.invalid_first_session and index == 1,
                }
                for index in range(offset + 1, end + 1)
            ],
            'hasMore': end < 300,
            'nextCursor': end,
        }

    async def fetch_every_session(self, limit):
        sessions = []
        cursor = None
        for _ in range(10):
            result = await self.server.get_chat_sessions(
                cookie_id='synthetic-pagination-account',
                include_order_fallback=False,
                limit=limit,
                cursor=cursor,
                remote=True,
                current_user={'user_id': 1},
            )
            self.assertIsNone(result['remote_error'])
            self.assertLessEqual(len(result['sessions']), limit)
            sessions.extend(item['chat_id'] for item in result['sessions'])
            if not result['has_more']:
                return sessions
            self.assertIsNotNone(result['next_cursor'])
            self.assertNotEqual(result['next_cursor'], cursor)
            cursor = result['next_cursor']
        self.fail('Conversation pagination did not finish')

    async def test_supported_page_sizes_keep_all_conversations(self):
        expected = [f'chat-{index}' for index in range(1, 301)]
        for limit in (60, 120, 200):
            with self.subTest(limit=limit):
                self.assertEqual(await self.fetch_every_session(limit), expected)

    async def test_filtered_conversation_does_not_skip_later_conversations(self):
        self.invalid_first_session = True
        expected = [f'chat-{index}' for index in range(2, 301)]
        self.assertEqual(await self.fetch_every_session(60), expected)


if __name__ == '__main__':
    unittest.main()
