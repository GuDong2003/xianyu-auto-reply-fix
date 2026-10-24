"""通知渠道正文模板；兼容单/双花括号，只替换白名单变量。"""

import re


CHANNEL_TEMPLATE_FIELDS = ('message_template', 'chat_template', 'delivery_template', 'account_template')
TEMPLATE_VARIABLES = {
    'account', 'account_id', 'buyer_name', 'buyer_nick', 'buyer_id', 'item_id', 'chat_id',
    'message', 'title', 'time', 'notification_type', 'result', 'order_id', 'amount',
    'quantity', 'error_message', 'verification_url', 'detail',
}
_PLACEHOLDER = re.compile(r'\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}|\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}')


def validate_channel_templates(config):
    if not isinstance(config, dict):
        return
    for key in CHANNEL_TEMPLATE_FIELDS:
        value = config.get(key)
        if value is None:
            continue
        if not isinstance(value, str):
            raise ValueError('通知模板必须是文本')
        if len(value) > 4000:
            raise ValueError('通知模板最多4000字')
        for match in _PLACEHOLDER.finditer(value):
            if (match.group(1) or match.group(2)) not in TEMPLATE_VARIABLES:
                raise ValueError('通知模板包含不支持的占位符')
        remaining = _PLACEHOLDER.sub('', value)
        if '{' in remaining or '}' in remaining:
            raise ValueError('通知模板占位符格式无效，请使用 {account_id} 或 {{account_id}} 格式')


def render_channel_template(config, notification_type, context, fallback):
    field = 'message_template' if notification_type == 'message' else (
        'delivery_template' if notification_type == 'delivery' else 'account_template'
    )
    template = config.get(field)
    if template is None and notification_type == 'message':
        template = config.get('chat_template')
    if not isinstance(template, str) or not template.strip():
        return fallback
    try:
        validate_channel_templates({field: template})
    except ValueError:
        return fallback

    def substitute(match):
        value = context.get(match.group(1) or match.group(2))
        return '未知' if value is None else str(value)

    return _PLACEHOLDER.sub(substitute, template)
