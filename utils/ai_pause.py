"""按账号、买家、商品持久化 AI 专用暂停，不影响关键词和默认回复。"""

import time


def initialize_ai_pause_schema(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS ai_manual_pause_settings (
        cookie_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, minutes INTEGER NOT NULL DEFAULT 10
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS ai_reply_pauses (
        cookie_id TEXT NOT NULL, buyer_id TEXT NOT NULL, item_id TEXT NOT NULL, pause_until REAL NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(cookie_id,buyer_id,item_id)
    )''')


def _id(value):
    return str(value or '').strip().removesuffix('@goofish')


def _known(value):
    return bool(value and value not in {'unknown','None','0'} and not value.startswith('auto_'))


class AIPauseStore:
    def __init__(self,database):
        self.db = database

    def config(self,cookie_id):
        with self.db.lock:
            row = self.db.conn.execute('SELECT enabled,minutes FROM ai_manual_pause_settings WHERE cookie_id=?',(cookie_id,)).fetchone()
            return {'enabled':bool(row[0]),'minutes':row[1]} if row else {'enabled':False,'minutes':10}

    def set_config(self,cookie_id,enabled,minutes):
        if not 1 <= int(minutes) <= 1440:
            raise ValueError('暂停时间应为1至1440分钟')
        with self.db.lock,self.db.conn:
            self.db.conn.execute('''INSERT INTO ai_manual_pause_settings (cookie_id,enabled,minutes) VALUES (?,?,?)
                ON CONFLICT(cookie_id) DO UPDATE SET enabled=excluded.enabled,minutes=excluded.minutes''',
                (cookie_id,int(enabled),int(minutes)))

    def context(self,cookie_id,chat_id,buyer_id='',item_id=''):
        buyer_id,item_id,chat_id = _id(buyer_id),_id(item_id),_id(chat_id)
        if _known(buyer_id) and _known(item_id):
            return buyer_id,item_id
        if not chat_id:
            return None
        where,params = ['cookie_id=?','chat_id IN (?,?)','direction=2'],[cookie_id,chat_id,chat_id+'@goofish']
        if _known(buyer_id):
            where.append('sender_id IN (?,?)')
            params.extend((buyer_id,buyer_id+'@goofish'))
        if _known(item_id):
            where.append('item_id=?')
            params.append(item_id)
        with self.db.lock:
            rows = self.db.conn.execute(f"SELECT sender_id,item_id FROM chat_messages WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT 50",params).fetchall()
            for row in rows:
                buyer,item = _id(row[0]),_id(row[1])
                if _known(buyer) and _known(item):
                    return buyer,item
        return None

    def pause(self,cookie_id,buyer_id,item_id,minutes):
        buyer_id,item_id = _id(buyer_id),_id(item_id)
        if not _known(buyer_id) or not _known(item_id) or not 1 <= int(minutes) <= 1440:
            raise ValueError('暂停AI需要明确的买家、商品及有效时长')
        until = time.time()+int(minutes)*60
        with self.db.lock,self.db.conn:
            self.db.conn.execute('''INSERT INTO ai_reply_pauses (cookie_id,buyer_id,item_id,pause_until) VALUES (?,?,?,?)
                ON CONFLICT(cookie_id,buyer_id,item_id) DO UPDATE SET
                pause_until=MAX(ai_reply_pauses.pause_until,excluded.pause_until),updated_at=CURRENT_TIMESTAMP''',
                (cookie_id,buyer_id,item_id,until))

    def remaining(self,cookie_id,buyer_id,item_id):
        with self.db.lock:
            row = self.db.conn.execute('SELECT pause_until FROM ai_reply_pauses WHERE cookie_id=? AND buyer_id=? AND item_id=?',
                                      (cookie_id,_id(buyer_id),_id(item_id))).fetchone()
            return max(0,int(row[0]-time.time()+0.999)) if row else 0

    def resume(self,cookie_id,buyer_id,item_id):
        with self.db.lock,self.db.conn:
            self.db.conn.execute('DELETE FROM ai_reply_pauses WHERE cookie_id=? AND buyer_id=? AND item_id=?',
                                 (cookie_id,_id(buyer_id),_id(item_id)))


def pause_after_manual_reply(database,cookie_id,chat_id,buyer_id='',item_id=''):
    store = AIPauseStore(database)
    config = store.config(cookie_id)
    if not config['enabled']:
        return False
    context = store.context(cookie_id,chat_id,buyer_id,item_id)
    if context:
        store.pause(cookie_id,*context,config['minutes'])
    return True
