"""自动续售的 SQLite 状态与关联迁移；发布前留痕，发布后只补迁移。"""

import json
import sqlite3
import time


def initialize_relist_schema(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS auto_relist_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, cookie_id TEXT NOT NULL,
        material_id INTEGER NOT NULL, card_id INTEGER NOT NULL, current_item_id TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 0, delay_seconds INTEGER NOT NULL DEFAULT 60,
        min_stock INTEGER NOT NULL DEFAULT 1, baseline_delivery_id INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'watching', last_error TEXT NOT NULL DEFAULT '', next_run REAL NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(cookie_id, current_item_id)
    )''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS auto_relist_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rule_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        cookie_id TEXT NOT NULL, order_id TEXT NOT NULL, old_item_id TEXT NOT NULL, new_item_id TEXT,
        state TEXT NOT NULL, publish_log_id INTEGER, snapshot_json TEXT NOT NULL,
        error TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, updated_at REAL NOT NULL,
        UNIQUE(rule_id, old_item_id)
    )''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_relist_rules_user ON auto_relist_rules(user_id, enabled)')


class RelistStore:
    def __init__(self, database):
        self.db = database

    def _one(self, sql, params=()):
        cursor = self.db.conn.execute(sql, params)
        row = cursor.fetchone()
        return dict(zip((c[0] for c in cursor.description), row)) if row else None

    def _all(self, sql, params=()):
        cursor = self.db.conn.execute(sql, params)
        names = [c[0] for c in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    def validate_resources(self, rule, *, stock=True):
        with self.db.lock:
            account = self._one('SELECT user_id FROM cookies WHERE id=?', (rule['cookie_id'],))
            material = self.db.get_product_material(rule['material_id'], user_id=rule['user_id'])
            card = self._one('SELECT * FROM cards WHERE id=? AND user_id=?', (rule['card_id'], rule['user_id']))
            if not account or account['user_id'] != rule['user_id'] or not material or not card:
                raise ValueError('账号、素材或卡券不存在，或不属于当前用户')
            if stock and not card['enabled']:
                raise ValueError('关联卡券已停用')
            if stock and (not material.get('title') or not material.get('description') or not material.get('images')):
                raise ValueError('发布素材需要完整标题、描述和图片')
            if stock and card['type'] == 'data':
                remaining = sum(bool(line.strip()) for line in (card['data_content'] or '').splitlines())
                if remaining < rule['min_stock']:
                    raise ValueError('卡密库存不足，等待补充库存')
            mapped = self._all('''SELECT DISTINCT c.* FROM cards c JOIN delivery_rules d ON d.card_id=c.id
                WHERE c.user_id=? AND d.keyword=?''', (rule['user_id'],rule['current_item_id']))
            if stock and mapped and rule['card_id'] not in {row['id'] for row in mapped}:
                raise ValueError('所选卡券不在原商品ID的发货规则中，请核对卡券关联')
            if stock:
                for mapped_card in mapped:
                    if mapped_card['enabled'] and mapped_card['type'] == 'data':
                        remaining = sum(bool(line.strip()) for line in (mapped_card['data_content'] or '').splitlines())
                        if remaining < rule['min_stock']:
                            raise ValueError('原商品关联的卡密库存不足，等待补充库存')
            return material, card

    def recent_publish_candidates(self, rule, delivered_at):
        with self.db.lock:
            return self._all('''SELECT DISTINCT item_id FROM publish_logs WHERE user_id=? AND account_id=?
                AND material_id=? AND status='success' AND item_id IS NOT NULL AND item_id!=?
                AND CAST(strftime('%s',created_at) AS INTEGER)>=?''',
                (rule['user_id'],rule['cookie_id'],rule['material_id'],rule['current_item_id'],delivered_at))

    def create(self, user_id, cookie_id, item_id, material_id, card_id, enabled=False, delay_seconds=60, min_stock=1):
        data = dict(user_id=user_id, cookie_id=cookie_id, current_item_id=item_id, material_id=material_id,
                    card_id=card_id, min_stock=min_stock)
        with self.db.lock, self.db.conn:
            self.validate_resources(data)
            if not self._one('SELECT item_id FROM item_info WHERE cookie_id=? AND item_id=?', (cookie_id, item_id)):
                raise ValueError('请先同步原商品到商品管理')
            baseline = self.db.conn.execute('SELECT COALESCE(MAX(id),0) FROM delivery_logs WHERE cookie_id=?', (cookie_id,)).fetchone()[0]
            try:
                cursor = self.db.conn.execute('''INSERT INTO auto_relist_rules
                (user_id,cookie_id,current_item_id,material_id,card_id,enabled,delay_seconds,min_stock,baseline_delivery_id)
                VALUES (?,?,?,?,?,?,?,?,?)''', (user_id, cookie_id, item_id, material_id, card_id, int(enabled), delay_seconds, min_stock, baseline))
            except sqlite3.IntegrityError as exc:
                raise ValueError('该账号的该商品已配置自动续售') from exc
            return cursor.lastrowid

    def rules(self, user_id=None):
        with self.db.lock:
            return self._all('SELECT * FROM auto_relist_rules' + (' WHERE user_id=?' if user_id is not None else ''),
                             (user_id,) if user_id is not None else ())

    def rule(self, rule_id, user_id=None):
        with self.db.lock:
            return self._one('SELECT * FROM auto_relist_rules WHERE id=?' + (' AND user_id=?' if user_id is not None else ''),
                             (rule_id, user_id) if user_id is not None else (rule_id,))

    def update(self, rule_id, user_id, enabled, delay_seconds, min_stock):
        with self.db.lock, self.db.conn:
            rule = self.rule(rule_id, user_id)
            if not rule:
                return False
            if enabled and not rule['enabled']:
                baseline = self.db.conn.execute('SELECT COALESCE(MAX(id),0) FROM delivery_logs WHERE cookie_id=?', (rule['cookie_id'],)).fetchone()[0]
            else:
                baseline = rule['baseline_delivery_id']
            self.db.conn.execute('''UPDATE auto_relist_rules SET enabled=?,delay_seconds=?,min_stock=?,baseline_delivery_id=?,
                next_run=0, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?''',
                (int(enabled), delay_seconds, min_stock, baseline, rule_id, user_id))
            return True

    def status(self, rule_id, status, error='', delay=60):
        with self.db.lock, self.db.conn:
            self.db.conn.execute('UPDATE auto_relist_rules SET status=?,last_error=?,next_run=?,updated_at=CURRENT_TIMESTAMP WHERE id=?',
                                 (status, error, time.time() + delay, rule_id))

    def delete(self, rule_id, user_id):
        with self.db.lock, self.db.conn:
            rule = self.rule(rule_id,user_id)
            if not rule:
                return False
            if rule['enabled']:
                raise ValueError('请先停用该续售规则')
            active = self._one("SELECT id FROM auto_relist_events WHERE rule_id=? AND state!='completed'", (rule_id,))
            if active:
                raise ValueError('该规则仍有待确认或迁移的发布结果，请先处理事件')
            self.db.conn.execute('DELETE FROM auto_relist_rules WHERE id=? AND user_id=?', (rule_id,user_id))
            return True

    def eligible_order(self, rule):
        with self.db.lock:
            return self._one('''SELECT o.*,d.id AS delivery_id,CAST(strftime('%s',d.created_at) AS INTEGER) AS delivered_at
                FROM orders o JOIN delivery_logs d ON d.order_id=o.order_id AND d.cookie_id=o.cookie_id AND d.item_id=o.item_id
                WHERE o.cookie_id=? AND o.item_id=? AND o.order_status IN ('shipped','completed')
                  AND d.status='success' AND d.user_id=? AND d.id>?
                ORDER BY d.id DESC LIMIT 1''', (rule['cookie_id'], rule['current_item_id'], rule['user_id'], rule['baseline_delivery_id']))

    def event_for_item(self, rule_id, item_id):
        with self.db.lock:
            return self._one('SELECT * FROM auto_relist_events WHERE rule_id=? AND old_item_id=?', (rule_id, item_id))

    def event(self, event_id, user_id=None):
        with self.db.lock:
            return self._one('SELECT * FROM auto_relist_events WHERE id=?' + (' AND user_id=?' if user_id is not None else ''),
                             (event_id, user_id) if user_id is not None else (event_id,))

    def events(self, user_id):
        with self.db.lock:
            rows = self._all('SELECT * FROM auto_relist_events WHERE user_id=? ORDER BY id DESC LIMIT 100', (user_id,))
            for row in rows:
                row.pop('snapshot_json', None)
            return rows

    def stage(self, rule, order, material):
        """以唯一旧商品创建发布事件；同一商品不会再次自动调用发布。"""
        with self.db.lock, self.db.conn:
            self.db.conn.execute('BEGIN IMMEDIATE')
            current = self.rule(rule['id'])
            if not current or not current['enabled'] or current['current_item_id'] != rule['current_item_id']:
                raise ValueError('续售规则已停用或绑定商品已变化')
            existing = self.event_for_item(rule['id'], rule['current_item_id'])
            if existing:
                return existing, False
            self.validate_resources(current)
            now = time.time()
            cursor = self.db.conn.execute('''INSERT INTO auto_relist_events
                (rule_id,user_id,cookie_id,order_id,old_item_id,state,snapshot_json,started_at,updated_at)
                VALUES (?,?,?,?,?,'publishing',?,?,?)''',
                (rule['id'], rule['user_id'], rule['cookie_id'], order['order_id'], rule['current_item_id'], json.dumps(material, ensure_ascii=False), now, now))
            event_id = cursor.lastrowid
            log = self.db.conn.execute('''INSERT INTO publish_logs (user_id,account_id,title,description,price,material_id,batch_id,status)
                VALUES (?,?,?,?,?,?,?,'publishing')''',
                (rule['user_id'],rule['cookie_id'],material['title'],material['description'],material.get('price'),rule['material_id'],f'relist_{event_id}'))
            self.db.conn.execute('UPDATE auto_relist_events SET publish_log_id=? WHERE id=?', (log.lastrowid, event_id))
            return self.event(event_id), True

    def published(self, event_id, item_id):
        with self.db.lock, self.db.conn:
            event = self.event(event_id)
            if not event or not str(item_id).strip() or str(item_id) == event['old_item_id']:
                raise ValueError('发布结果缺少有效的新商品ID')
            if event['new_item_id'] and event['new_item_id'] != str(item_id):
                raise ValueError('该续售事件已记录其它新商品ID')
            if event['state'] == 'completed':
                if event['new_item_id'] != str(item_id):
                    raise ValueError('该续售事件已关联其它商品')
                return
            self.db.conn.execute("UPDATE auto_relist_events SET new_item_id=?,state='published',error='',updated_at=? WHERE id=?",
                                 (str(item_id), time.time(), event_id))

    def event_error(self, event_id, state, message):
        with self.db.lock, self.db.conn:
            self.db.conn.execute('UPDATE auto_relist_events SET state=?,error=?,updated_at=? WHERE id=? AND state!=?',
                                 (state, message, time.time(), event_id, 'completed'))

    def recover_result(self, event):
        with self.db.lock:
            log = self._one('SELECT * FROM publish_logs WHERE id=? AND user_id=? AND account_id=?',
                            (event['publish_log_id'], event['user_id'], event['cookie_id']))
            return str(log['item_id']) if log and log['status'] == 'success' and log['item_id'] else None

    def migrate(self, event_id):
        with self.db.lock, self.db.conn:
            self.db.conn.execute('BEGIN IMMEDIATE')
            event = self.event(event_id)
            if event['state'] == 'completed':
                return
            if event['state'] not in {'published', 'migration_retry'} or not event['new_item_id']:
                raise ValueError('尚无可核验的发布结果')
            rule = self.rule(event['rule_id'])
            self.validate_resources(rule, stock=False)
            if rule['current_item_id'] != event['old_item_id']:
                raise ValueError('规则已绑定其它商品，请人工核对')
            old, new, account = event['old_item_id'], event['new_item_id'], event['cookie_id']
            old_item = self._one('SELECT * FROM item_info WHERE cookie_id=? AND item_id=?', (account, old))
            if not old_item:
                raise ValueError('原商品资料不存在，无法迁移')
            self.db.conn.execute('''INSERT OR IGNORE INTO item_info
                (cookie_id,item_id,item_title,item_description,item_category,item_price,item_detail,ai_prompt,is_multi_spec,multi_quantity_delivery,is_listed)
                VALUES (?,?,?,?,?,?,?,?,?,?,1)''', (account,new,old_item['item_title'],old_item['item_description'],old_item['item_category'],
                    old_item['item_price'],old_item['item_detail'],old_item['ai_prompt'],old_item['is_multi_spec'],old_item['multi_quantity_delivery']))
            self.db.conn.execute("UPDATE item_info SET ai_prompt=CASE WHEN ai_prompt='' THEN ? ELSE ai_prompt END,is_multi_spec=?,multi_quantity_delivery=? WHERE cookie_id=? AND item_id=?",
                                 (old_item['ai_prompt'],old_item['is_multi_spec'],old_item['multi_quantity_delivery'],account,new))
            for table in ('keywords','item_replay','xy_personal_blacklist','xy_message_filter_rules'):
                columns = [r[1] for r in self.db.conn.execute(f'PRAGMA table_info({table})') if r[1] not in {'id','created_at','updated_at'}]
                where = 'item_id=? AND cookie_id=?'
                values = [old, account]
                if 'user_id' in columns:
                    where = 'item_id=? AND (cookie_id=? OR cookie_id IS NULL) AND user_id=?'
                    values.append(rule['user_id'])
                if table == 'item_replay' and self._one('SELECT id FROM item_replay WHERE item_id=? AND cookie_id=?', (new,account)):
                    continue
                selection = ['?' if name == 'item_id' else name for name in columns]
                self.db.conn.execute(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) SELECT {','.join(selection)} FROM {table} WHERE {where}", (new,*values))
            target = self._all('''SELECT d.card_id FROM delivery_rules d JOIN cards c ON c.id=d.card_id
                WHERE d.keyword=? AND c.user_id=?''', (new,rule['user_id']))
            sources = self._all('''SELECT d.* FROM delivery_rules d JOIN cards c ON c.id=d.card_id
                WHERE d.keyword=? AND c.user_id=? ORDER BY d.id''', (old,rule['user_id']))
            expected_cards = {row['card_id'] for row in sources} if sources else {rule['card_id']}
            if any(row['card_id'] not in expected_cards for row in target):
                raise ValueError('新商品已有冲突发货规则，请人工核对')
            for source in sources or [{'card_id':rule['card_id'],'delivery_count':1,'enabled':1,'description':'自动续售关联'}]:
                if any(row['card_id'] == source['card_id'] for row in target):
                    continue
                self.db.conn.execute('''INSERT INTO delivery_rules (keyword,card_id,delivery_count,enabled,description)
                    VALUES (?,?,?,?,?)''', (new,source['card_id'],source['delivery_count'],source['enabled'],source['description']))
                target.append({'card_id':source['card_id']})
            self.db.conn.execute('UPDATE item_info SET is_listed=0 WHERE cookie_id=? AND item_id=?', (account,old))
            self.db.conn.execute("UPDATE auto_relist_rules SET current_item_id=?,status='watching',last_error='',next_run=0,updated_at=CURRENT_TIMESTAMP WHERE id=?", (new,rule['id']))
            self.db.conn.execute("UPDATE auto_relist_events SET state='completed',error='',updated_at=? WHERE id=?", (time.time(),event_id))
