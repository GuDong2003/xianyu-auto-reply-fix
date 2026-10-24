"""登录审计：仅追加结果，不记录请求正文、密码、验证码或会话凭证。"""


def initialize_login_audit_schema(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS user_login_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, login_account TEXT NOT NULL,
        login_method TEXT NOT NULL, success INTEGER NOT NULL, client_ip TEXT NOT NULL,
        result_message TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_login_audit_time ON user_login_audit(created_at,id)')


class LoginAuditStore:
    def __init__(self, database):
        self.db = database

    def record(self, account, method, success, client_ip, user_id=None, message=''):
        with self.db.lock,self.db.conn:
            self.db.conn.execute('''INSERT INTO user_login_audit
                (user_id,login_account,login_method,success,client_ip,result_message) VALUES (?,?,?,?,?,?)''',
                (user_id if success else None,str(account or '')[:255],method,int(success),str(client_ip or 'unknown')[:64],message[:256]))

    def list_users(self, page=1, page_size=50, success=None, keyword=''):
        where, params = ['1=1'], []
        if success is not None:
            where.append('success=?')
            params.append(int(success))
        if keyword:
            where.append('instr(login_account,?)>0')
            params.append(keyword[:255])
        return self._page('user_login_audit','id,user_id,login_account,login_method,success,client_ip,result_message,created_at',
                          ' AND '.join(where),params,page,page_size)

    def list_accounts(self, user_id, page=1, page_size=50, success=None, keyword=''):
        where = ["r.cookie_id IN (SELECT id FROM cookies WHERE user_id=?)",
                 "(r.event_type='cookie_refresh' OR r.event_type LIKE '%login%' OR r.trigger_scene LIKE '%login%' OR r.trigger_scene LIKE '%cookie_refresh%')"]
        params = [user_id]
        if success is not None:
            where.append("r.processing_status=?")
            params.append('success' if success else 'failed')
        if keyword:
            where.append('instr(r.cookie_id,?)>0')
            params.append(keyword[:255])
        # 不返回 session_id、event_meta、验证链接、截图或原始异常文本。
        return self._page('risk_control_logs r','r.id,r.cookie_id,r.event_type,r.trigger_scene,r.processing_status,r.created_at',
                          ' AND '.join(where),params,page,page_size)

    def _page(self, table, columns, where, params, page, page_size):
        page, page_size = max(1,int(page)),max(1,min(100,int(page_size)))
        with self.db.lock:
            total = self.db.conn.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}',params).fetchone()[0]
            cursor = self.db.conn.execute(f'SELECT {columns} FROM {table} WHERE {where} ORDER BY id DESC LIMIT ? OFFSET ?',
                                          (*params,page_size,(page-1)*page_size))
            names = [c[0] for c in cursor.description]
            return {'items':[dict(zip(names,row)) for row in cursor.fetchall()],'total':total,'page':page,'page_size':page_size}


def record_login_result(database, login_request, request, response):
    method = 'username_password' if login_request.username and login_request.password else (
        'email_password' if login_request.email and login_request.password else (
            'email_code' if login_request.email and login_request.verification_code else 'unknown'
        )
    )
    account = login_request.username or login_request.email or ''
    success = bool(response and response.success)
    # 审计记录连接对端地址，不信任任意客户端传入的 X-Forwarded-For。
    peer_ip = request.client.host if request.client else 'unknown'
    allowed_messages = {'用户名或密码错误','邮箱或密码错误','验证码错误或已过期','用户不存在','请提供有效的登录信息'}
    message = '登录成功' if success else (response.message if response and response.message in allowed_messages else '登录未成功')
    LoginAuditStore(database).record(account,method,success,peer_ip,response.user_id if success else None,message)
