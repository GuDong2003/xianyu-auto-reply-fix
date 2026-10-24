import json
from types import SimpleNamespace
import unittest

from support import IsolatedDatabaseMixin
from utils.login_audit import LoginAuditStore, record_login_result


class LoginAuditTests(IsolatedDatabaseMixin,unittest.TestCase):
    def setUp(self):
        with self.db.conn:
            self.db.conn.execute('DELETE FROM user_login_audit')
            self.db.conn.execute('DELETE FROM risk_control_logs')
        self.store = LoginAuditStore(self.db)

    def test_audit_only_records_safe_result_fields(self):
        request = SimpleNamespace(client=SimpleNamespace(host='127.0.0.1'),headers={'X-Forwarded-For':'198.51.100.1'})
        credentials = SimpleNamespace(username='synthetic',email=None,password='synthetic-password',verification_code=None)
        response = SimpleNamespace(success=True,user_id=1,message='login',token='synthetic-token')
        record_login_result(self.db,credentials,request,response)
        row = self.store.list_users()['items'][0]
        self.assertEqual(row['client_ip'],'127.0.0.1')
        self.assertEqual(row['login_method'],'username_password')
        self.assertNotIn('synthetic-password',json.dumps(row))
        self.assertNotIn('synthetic-token',json.dumps(row))

    def test_failure_detail_does_not_persist_raw_exception(self):
        credentials = SimpleNamespace(username=None,email='synthetic@example.invalid',password=None,verification_code='synthetic-code')
        response = SimpleNamespace(success=False,user_id=None,message='password=synthetic-sensitive-detail')
        record_login_result(self.db,credentials,SimpleNamespace(client=None),response)
        row = self.store.list_users()['items'][0]
        self.assertEqual(row['result_message'],'登录未成功')
        self.assertEqual(row['login_method'],'email_code')
        self.assertNotIn('synthetic-sensitive-detail',json.dumps(row))

    def test_filter_pagination_and_literal_search(self):
        for index in range(5):
            self.store.record('account_%' if index==0 else 'other','username_password',index%2==0,'127.0.0.1')
        self.assertEqual(self.store.list_users(success=True)['total'],3)
        self.assertEqual(len(self.store.list_users(page=2,page_size=2)['items']),2)
        self.assertEqual(self.store.list_users(keyword='%')['total'],1)

    def test_account_audit_is_scoped_and_omits_raw_metadata(self):
        self.db.save_cookie('owned','synthetic',1)
        self.db.save_cookie('foreign','synthetic',2)
        for account in ('owned','foreign'):
            self.db.add_risk_control_log(account,event_type='cookie_refresh',trigger_scene='qr_login',processing_status='success',
                                         session_id='synthetic-session',event_meta={'cookie':'synthetic-cookie'},error_message='synthetic-detail')
        rows = self.store.list_accounts(1)['items']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['cookie_id'],'owned')
        self.assertNotIn('synthetic-session',json.dumps(rows))
        self.assertNotIn('synthetic-cookie',json.dumps(rows))
        self.assertNotIn('synthetic-detail',json.dumps(rows))


if __name__ == '__main__':
    unittest.main()
