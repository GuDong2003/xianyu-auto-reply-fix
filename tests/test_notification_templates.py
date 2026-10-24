import unittest
from unittest import mock

from utils.notification_dispatcher import dispatch_notifications
from utils.notification_templates import render_channel_template, validate_channel_templates


class ChannelTemplateTests(unittest.TestCase):
    def test_single_and_double_brace_templates(self):
        for template in ('账号 {account_id}: {message}', '账号 {{ account_id }}: {{message}}'):
            self.assertEqual(render_channel_template(
                {'message_template': template}, 'message', {'account_id': 'a', 'message': 'hello'}, 'fallback',
            ), '账号 a: hello')

    def test_missing_values_are_explicit_and_substitution_is_not_recursive(self):
        self.assertEqual(render_channel_template(
            {'message_template': '{message}/{buyer_name}'}, 'message', {'message': '{account_id}'}, 'fallback',
        ), '{account_id}/未知')

    def test_invalid_or_oversized_templates_rejected_on_save(self):
        for value in ('{unsupported}', '{account_id.__class__}', '{{account_id}', 'a' * 4001, 123):
            with self.assertRaises(ValueError):
                validate_channel_templates({'message_template': value})

    def test_empty_and_invalid_stored_template_use_fallback(self):
        for value in (None, '', '   ', '{unsupported}', '{{account_id}'):
            self.assertEqual(render_channel_template(
                {'message_template': value}, 'message', {}, 'global message',
            ), 'global message')

    def test_event_specific_selection_and_upstream_chat_alias(self):
        config = {'chat_template': 'chat', 'delivery_template': '{result}', 'account_template': '{title}'}
        self.assertEqual(render_channel_template(config, 'message', {}, 'fallback'), 'chat')
        self.assertEqual(render_channel_template(config, 'delivery', {'result': 'failure'}, 'fallback'), 'failure')
        self.assertEqual(render_channel_template(config, 'face_verify', {'title': 'verify'}, 'fallback'), 'verify')


class ChannelDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_channels_render_independently_and_preserve_attachment(self):
        notifications = [
            {'type': 'email', 'config': {'message_template': '{account_id}: {message}'}},
            {'type': 'webhook', 'config': {'message_template': '{{buyer_nick}} / {{message}}'}},
            {'type': 'telegram', 'config': {}},
            {'type': 'webhook', 'enabled': False, 'config': {'message_template': 'disabled'}},
        ]
        with mock.patch('utils.notification_dispatcher.send_channel_notification', new=mock.AsyncMock(return_value=True)) as send:
            result = await dispatch_notifications(
                notifications, 'global message', account_id='a', notification_type='message',
                attachment_path='synthetic-image.png', template_context={'message': 'hello', 'buyer_name': 'buyer'},
            )
        self.assertTrue(result)
        self.assertEqual([call.args[2] for call in send.await_args_list], ['a: hello', 'buyer / hello', 'global message'])
        for call in send.await_args_list:
            self.assertEqual(call.kwargs['attachment_path'], 'synthetic-image.png')

    async def test_bad_stored_template_does_not_prevent_other_channels(self):
        with mock.patch('utils.notification_dispatcher.send_channel_notification', new=mock.AsyncMock(return_value=True)) as send:
            await dispatch_notifications([
                {'type': 'webhook', 'config': {'account_template': '{unknown}'}},
                {'type': 'email', 'config': {'account_template': '{title} {detail}'}},
            ], 'details', title='Warning', account_id='a')
        self.assertEqual([call.args[2] for call in send.await_args_list], ['details', 'Warning details'])


if __name__ == '__main__':
    unittest.main()
