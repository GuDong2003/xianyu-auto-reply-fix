"""Message filter rule helpers for auto-reply routing."""

import json
import re
from typing import Any, Dict, List, Optional

from loguru import logger

from db_manager import db_manager


class MessageFilterService:
    VALID_MATCH_TYPES = {'contains', 'exact', 'regex'}
    VALID_MESSAGE_SOURCES = {'all', 'user', 'system', 'ai'}

    def __init__(self, db=db_manager):
        self.db = db

    def _clean_text(self, value: Any) -> str:
        if value is None:
            return ''
        return str(value).strip()

    def _normalize_bool(self, value: Any, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in {'1', 'true', 'yes', 'y', 'on', '启用', '是', '开启'}:
            return True
        if text in {'0', 'false', 'no', 'n', 'off', '禁用', '否', '关闭'}:
            return False
        return default

    def _normalize_patterns(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_values = re.split(r'[\n\r]+', value)
        else:
            raw_values = list(value)

        patterns = []
        seen = set()
        for raw_value in raw_values:
            pattern = self._clean_text(raw_value)
            if not pattern or pattern in seen:
                continue
            patterns.append(pattern)
            seen.add(pattern)
        return patterns

    def _patterns_to_json(self, patterns: List[str]) -> str:
        return json.dumps(patterns, ensure_ascii=False)

    def _validate_regex_patterns(self, patterns: List[str]):
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"正则表达式无效: {pattern} ({exc})") from exc

    def _normalize_rule_payload(self, payload: Dict[str, Any], *, partial: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {}

        if not partial or 'name' in payload:
            name = self._clean_text(payload.get('name'))
            if not name:
                raise ValueError('规则名称不能为空')
            data['name'] = name

        if not partial or 'cookie_id' in payload:
            data['cookie_id'] = self._clean_text(payload.get('cookie_id')) or None

        if not partial or 'item_id' in payload:
            data['item_id'] = self._clean_text(payload.get('item_id')) or None

        if not partial or 'match_type' in payload:
            match_type = self._clean_text(payload.get('match_type') or 'contains').lower()
            if match_type not in self.VALID_MATCH_TYPES:
                raise ValueError('匹配方式无效')
            data['match_type'] = match_type

        if not partial or 'message_source' in payload:
            message_source = self._clean_text(payload.get('message_source') or 'user').lower()
            if message_source not in self.VALID_MESSAGE_SOURCES:
                raise ValueError('消息来源无效')
            data['message_source'] = message_source

        if not partial or 'patterns' in payload:
            patterns = self._normalize_patterns(payload.get('patterns'))
            if not patterns:
                raise ValueError('匹配内容不能为空')
            match_type = data.get('match_type') or self._clean_text(payload.get('match_type') or 'contains').lower()
            if match_type == 'regex':
                self._validate_regex_patterns(patterns)
            data['patterns'] = self._patterns_to_json(patterns)

        bool_fields = {
            'is_enabled': True,
            'action_skip_auto_reply': True,
            'action_skip_ai_reply': False,
            'action_notify': False,
        }
        for field_name, default in bool_fields.items():
            if not partial or field_name in payload:
                data[field_name] = self._normalize_bool(payload.get(field_name), default)

        if not partial or 'action_pause_minutes' in payload:
            try:
                pause_minutes = int(payload.get('action_pause_minutes') or 0)
            except (TypeError, ValueError):
                pause_minutes = 0
            data['action_pause_minutes'] = max(0, min(pause_minutes, 1440))

        return data

    def list_rules(self, user_id: int, keyword: Optional[str] = None, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        return self.db.list_message_filter_rules(user_id=user_id, keyword=keyword, page=page, page_size=page_size)

    def create_rule(self, user_id: int, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = self._normalize_rule_payload(payload)
        return self.db.create_message_filter_rule(user_id=user_id, **data)

    def update_rule(self, rule_id: int, user_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        data = self._normalize_rule_payload(payload)
        return self.db.update_message_filter_rule(rule_id=rule_id, user_id=user_id, **data)

    def delete_rule(self, rule_id: int, user_id: int) -> bool:
        return self.db.delete_message_filter_rule(rule_id, user_id)

    def toggle_rule(self, rule_id: int, user_id: int, is_enabled: bool) -> bool:
        return self.db.toggle_message_filter_rule(rule_id, user_id, is_enabled)

    def _matches_source(self, rule: Dict[str, Any], message_source: str) -> bool:
        rule_source = self._clean_text(rule.get('message_source') or 'user').lower()
        if rule_source == 'all':
            return True
        return rule_source == message_source

    def _matches_pattern(self, rule: Dict[str, Any], message: str) -> bool:
        text = self._clean_text(message)
        if not text:
            return False

        match_type = self._clean_text(rule.get('match_type') or 'contains').lower()
        patterns = rule.get('patterns') or []
        if isinstance(patterns, str):
            try:
                patterns = json.loads(patterns)
            except Exception:
                patterns = self._normalize_patterns(patterns)

        for pattern in patterns:
            pattern_text = self._clean_text(pattern)
            if not pattern_text:
                continue
            if match_type == 'exact' and text == pattern_text:
                return True
            if match_type == 'contains' and pattern_text in text:
                return True
            if match_type == 'regex':
                try:
                    if re.search(pattern_text, text):
                        return True
                except re.error as exc:
                    logger.warning(f"消息过滤规则正则无效，已跳过: {pattern_text} ({exc})")
        return False

    def match_rules(
        self,
        user_id: int,
        message: str,
        cookie_id: Optional[str] = None,
        item_id: Optional[str] = None,
        message_source: str = 'user',
    ) -> Dict[str, Any]:
        source = self._clean_text(message_source or 'user').lower()
        if source not in self.VALID_MESSAGE_SOURCES:
            source = 'user'

        result = {
            'matched': False,
            'rules': [],
            'skip_auto_reply': False,
            'skip_ai_reply': False,
            'pause_minutes': 0,
            'notify_enabled': False,
        }

        if not user_id or not self._clean_text(message):
            return result

        rules = self.db.get_message_filter_rules_for_context(
            user_id=user_id,
            cookie_id=cookie_id,
            item_id=item_id,
        )
        for rule in rules:
            if not self._matches_source(rule, source):
                continue
            if not self._matches_pattern(rule, message):
                continue

            result['matched'] = True
            result['rules'].append(rule)
            result['skip_auto_reply'] = result['skip_auto_reply'] or bool(rule.get('action_skip_auto_reply'))
            result['skip_ai_reply'] = result['skip_ai_reply'] or bool(rule.get('action_skip_ai_reply'))
            result['notify_enabled'] = result['notify_enabled'] or bool(rule.get('action_notify'))
            try:
                pause_minutes = int(rule.get('action_pause_minutes') or 0)
            except (TypeError, ValueError):
                pause_minutes = 0
            result['pause_minutes'] = max(result['pause_minutes'], pause_minutes)

        return result

    def match_by_cookie(
        self,
        cookie_id: str,
        message: str,
        item_id: Optional[str] = None,
        message_source: str = 'user',
    ) -> Dict[str, Any]:
        cookie_details = self.db.get_cookie_details(cookie_id) if cookie_id else None
        user_id = cookie_details.get('user_id') if cookie_details else None
        if not user_id:
            return {
                'matched': False,
                'rules': [],
                'skip_auto_reply': False,
                'skip_ai_reply': False,
                'pause_minutes': 0,
                'notify_enabled': False,
            }
        return self.match_rules(
            user_id=user_id,
            message=message,
            cookie_id=cookie_id,
            item_id=item_id,
            message_source=message_source,
        )


message_filter_service = MessageFilterService()
