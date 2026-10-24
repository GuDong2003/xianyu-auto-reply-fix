import asyncio
import unittest
from unittest import mock

from support import IsolatedDatabaseMixin
from auto_relist_task import AutoRelistService
from utils.relist_store import RelistStore


class AutoRelistTests(IsolatedDatabaseMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner = self.db.get_user_by_username('admin')['id']
        self.db.save_cookie('a', 'synthetic-cookie', self.owner)
        with self.db.conn:
            for table in ('auto_relist_events','auto_relist_rules','publish_logs','delivery_logs','orders','item_info',
                          'keywords','item_replay','delivery_rules','cards','product_materials'):
                self.db.conn.execute(f'DELETE FROM {table}')
            self.db.conn.execute("INSERT INTO item_info (cookie_id,item_id,item_title,ai_prompt) VALUES ('a','old','Synthetic item','Keep instructions')")
            card = self.db.conn.execute("INSERT INTO cards (name,type,data_content,user_id) VALUES ('Synthetic card','data','one\ntwo',?)", (self.owner,))
            self.card_id = card.lastrowid
        self.material_id = self.db.add_product_material(self.owner, {
            'title':'Synthetic item','description':'Synthetic description','price':10,
            'images':['https://example.invalid/image.png'],
        })
        self.store = RelistStore(self.db)
        self.rule_id = self.store.create(self.owner,'a','old',self.material_id,self.card_id,True,0)
        with self.db.conn:
            self.db.conn.execute("INSERT INTO orders (order_id,cookie_id,item_id,order_status) VALUES ('order','a','old','shipped')")
            self.db.conn.execute("INSERT INTO delivery_logs (user_id,cookie_id,item_id,order_id,status) VALUES (?,'a','old','order','success')", (self.owner,))
        self.service = AutoRelistService(self.db)
        self.snapshot = mock.AsyncMock(return_value={'success':True,'complete':True,'items':[]})
        self.publish = mock.AsyncMock(return_value={'success':True,'published_item_id':'new'})
        self.enterContext(mock.patch.object(self.service,'_snapshot',self.snapshot))
        self.enterContext(mock.patch.object(self.service,'_publish',self.publish))

    async def run_rule(self):
        return (await self.service.run_once(self.owner,self.rule_id))[0]['status']

    async def test_complete_sale_republishes_and_preserves_associations_once(self):
        with self.db.conn:
            self.db.conn.execute("INSERT INTO keywords (cookie_id,item_id,keyword,reply) VALUES ('a','old','hello','reply')")
            self.db.conn.execute("INSERT INTO item_replay (cookie_id,item_id,reply_content) VALUES ('a','old','Special reply')")
            self.db.conn.execute("INSERT INTO delivery_rules (keyword,card_id,delivery_count) VALUES ('old',?,2)", (self.card_id,))
        self.assertEqual(await self.run_rule(),'completed')
        self.assertEqual(self.store.rule(self.rule_id)['current_item_id'],'new')
        self.assertEqual(self.db.get_item_info('a','new')['ai_prompt'],'Keep instructions')
        self.assertFalse(self.db.get_item_info('a','old')['is_listed'])
        with self.db.lock:
            self.assertEqual(self.db.conn.execute("SELECT reply FROM keywords WHERE cookie_id='a' AND item_id='new'").fetchone()[0],'reply')
            self.assertEqual(self.db.conn.execute("SELECT delivery_count FROM delivery_rules WHERE keyword='new'").fetchone()[0],2)
        await self.run_rule()
        self.publish.assert_awaited_once()

    async def test_old_product_still_on_sale_does_not_publish(self):
        self.snapshot.return_value['items'] = [{'id':'old'}]
        self.assertEqual(await self.run_rule(),'still_listed')
        self.publish.assert_not_awaited()

    async def test_all_explicit_sku_card_rules_are_copied(self):
        with self.db.conn:
            second = self.db.conn.execute("INSERT INTO cards (name,type,data_content,user_id) VALUES ('Second','data','stock',?)",(self.owner,)).lastrowid
            for card_id,count in ((self.card_id,1),(second,2)):
                self.db.conn.execute("INSERT INTO delivery_rules (keyword,card_id,delivery_count) VALUES ('old',?,?)",(card_id,count))
        self.assertEqual(await self.run_rule(),'completed')
        rows = self.db.conn.execute("SELECT card_id,delivery_count FROM delivery_rules WHERE keyword='new' ORDER BY card_id").fetchall()
        self.assertEqual(rows,[(self.card_id,1),(second,2)])

    async def test_incomplete_snapshot_does_not_publish(self):
        self.snapshot.return_value['complete'] = False
        self.assertEqual(await self.run_rule(),'paused')
        self.publish.assert_not_awaited()

    async def test_refunded_or_undelivered_orders_never_trigger(self):
        for status in ('pending_payment','processing','refunding','cancelled'):
            with self.db.conn:
                self.db.conn.execute('UPDATE orders SET order_status=?',(status,))
            self.assertEqual(await self.run_rule(),'waiting')
        self.snapshot.assert_not_awaited()
        self.publish.assert_not_awaited()

    async def test_inventory_exhaustion_and_foreign_owner_stop_before_snapshot(self):
        with self.db.conn:
            self.db.conn.execute("UPDATE cards SET data_content='' WHERE id=?",(self.card_id,))
        self.assertEqual(await self.run_rule(),'paused')
        self.snapshot.assert_not_awaited()
        with self.db.conn:
            self.db.conn.execute("UPDATE cards SET data_content='restocked' WHERE id=?",(self.card_id,))
            self.db.conn.execute('UPDATE cookies SET user_id=? WHERE id=?',(self.owner+1,'a'))
        self.store.status(self.rule_id,'watching',delay=0)
        self.assertEqual(await self.run_rule(),'paused')
        self.publish.assert_not_awaited()

    async def test_disable_and_reenable_do_not_replay_historical_sales(self):
        self.store.update(self.rule_id,self.owner,False,0,1)
        self.assertEqual(await self.run_rule(),'skipped')
        self.store.update(self.rule_id,self.owner,True,0,1)
        self.assertEqual(await self.run_rule(),'waiting')
        self.publish.assert_not_awaited()

    async def test_unknown_publish_never_automatically_retries(self):
        self.publish.side_effect = TimeoutError()
        self.assertEqual(await self.run_rule(),'unknown')
        self.assertEqual(await self.run_rule(),'unknown')
        self.publish.assert_awaited_once()

    async def test_successful_publish_log_recovers_without_republishing(self):
        self.publish.side_effect = TimeoutError()
        await self.run_rule()
        event = self.store.event_for_item(self.rule_id,'old')
        self.db.update_publish_log(event['publish_log_id'],status='success',item_id='new')
        self.assertEqual(await self.run_rule(),'completed')
        self.publish.assert_awaited_once()

    async def test_existing_material_publish_after_sale_is_adopted(self):
        log_id = self.db.add_publish_log(self.owner,'a','Synthetic',material_id=self.material_id)
        self.db.update_publish_log(log_id,status='success',item_id='new')
        self.snapshot.return_value['items'] = [{'id':'new'}]
        self.assertEqual(await self.run_rule(),'completed')
        self.publish.assert_not_awaited()
        self.assertEqual(self.store.rule(self.rule_id)['current_item_id'],'new')

    async def test_migration_failure_rolls_back_and_only_retries_migration(self):
        with self.db.conn:
            card = self.db.conn.execute("INSERT INTO cards (name,type,user_id) VALUES ('Conflict','text',?)",(self.owner,))
            self.db.conn.execute("INSERT INTO delivery_rules (keyword,card_id) VALUES ('new',?)",(card.lastrowid,))
        self.assertEqual(await self.run_rule(),'migration_retry')
        self.assertIsNone(self.db.get_item_info('a','new'))
        with self.db.conn:
            self.db.conn.execute("DELETE FROM delivery_rules WHERE keyword='new'")
        self.assertEqual(await self.run_rule(),'completed')
        self.publish.assert_awaited_once()

    async def test_concurrent_workers_submit_only_one_publish(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def publish(*args):
            entered.set()
            await release.wait()
            return {'success':True,'published_item_id':'new'}
        self.publish.side_effect = publish
        first = asyncio.create_task(self.run_rule())
        await entered.wait()
        self.assertEqual(await self.run_rule(),'publishing')
        release.set()
        self.assertEqual(await first,'completed')
        self.publish.assert_awaited_once()

    async def test_manual_association_validates_owner_and_remote_account(self):
        self.publish.side_effect = TimeoutError()
        await self.run_rule()
        event = self.store.event_for_item(self.rule_id,'old')
        with self.assertRaises(ValueError):
            await self.service.associate_result(event['id'],self.owner+1,'new')
        with self.assertRaises(ValueError):
            await self.service.associate_result(event['id'],self.owner,'not-in-account')
        self.snapshot.return_value['items'] = [{'id':'new'}]
        self.assertEqual(await self.service.associate_result(event['id'],self.owner,'new'),'completed')
        self.publish.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
