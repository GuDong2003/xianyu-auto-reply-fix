import base64
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import unittest

from playwright.sync_api import sync_playwright


REPO = Path(__file__).resolve().parents[1]
IMAGE = base64.b64decode(
    'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aOvsAAAAASUVORK5CYII='
)


class ItemPublishFormTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        if not Path(cls.playwright.chromium.executable_path).is_file():
            raise unittest.SkipTest('Install Playwright Chromium to run browser regressions')
        cls.browser = cls.playwright.chromium.launch(headless=True, args=['--no-sandbox'])
        cls.addClassCleanup(cls.browser.close)
        html = (REPO / 'static/index.html').read_text()
        cls.html = '<div id="item-publish-section"' + html.split(
            '<div id="item-publish-section"', 1
        )[1].split('<!-- 商品管理内容 -->', 1)[0]
        cls.html = '<style>' + (REPO / 'static/css/items.css').read_text() + '</style>' + cls.html
        cls.script = (REPO / 'static/js/app.js').read_text()

    def setUp(self):
        self.publishes = []
        self.materials = []
        self.page_errors = []
        self.page = self.browser.new_page(viewport={'width': 1100, 'height': 1000})
        self.page.set_default_timeout(5000)
        self.addCleanup(self.page.close)
        self.page.on('pageerror', lambda error: self.page_errors.append(str(error)))
        self.page.route('**/*', self.respond)
        self.page.goto('http://publish.test/')
        self.page.add_script_tag(content=self.script)
        self.page.evaluate('''() => {
            authToken = 'browser-test';
            window.testToasts = [];
            showToast = message => window.testToasts.push(message);
            document.getElementById('publishCookieId').innerHTML =
                '<option value="">请选择账号</option><option value="test-account">测试账号</option>';
            ensureItemPublishPageInitialized();
        }''')
        self.complete_form()

    def respond(self, route):
        request = route.request
        path = request.url.removeprefix('http://publish.test').split('?', 1)[0]
        if path == '/':
            route.fulfill(content_type='text/html; charset=utf-8', body=self.html)
            return
        if ((path == '/product-materials' and request.method == 'POST')
                or (path == '/product-materials/1' and request.method == 'PUT')):
            material = {**request.post_data_json, 'id': 1}
            self.materials = [material]
            data = {'success': True, 'material': material}
        elif path == '/product-materials' and request.method == 'GET':
            data = {'success': True, 'list': self.materials}
        elif path == '/publish-logs':
            data = {'success': True, 'list': []}
        elif path.startswith('/material-image-'):
            route.fulfill(content_type='image/png', body=IMAGE)
            return
        elif path in ('/product-publish', '/item-publish') and request.method == 'POST':
            self.publishes.append({
                'path': path,
                'content_type': request.headers.get('content-type', ''),
                'body': request.post_data_buffer,
            })
            data = {'success': True, 'message': '模拟发布成功'}
        else:
            route.abort()
            return
        route.fulfill(content_type='application/json', body=json.dumps(data))

    def complete_form(self):
        self.page.select_option('#publishCookieId', 'test-account')
        self.page.fill('#publishTitle', '测试商品')
        self.page.fill('#publishDescription', '测试商品描述')

    def upload_images(self, count):
        self.page.set_input_files('#publishImages', [
            {'name': f'image-{i}.png', 'mimeType': 'image/png', 'buffer': IMAGE}
            for i in range(count)
        ])

    def save_material(self):
        with self.page.expect_response(lambda response: response.url.endswith(('/product-materials', '/product-materials/1'))):
            self.page.click('#itemPublishSaveMaterialBtn')
        self.page.wait_for_function('!itemPublishSavingMaterial && itemPublishLoadedMaterialId === 1')
        self.assertEqual(len(self.materials), 1)
        self.assertEqual(self.page.locator('#publishImages').evaluate('(input) => input.files.length'), 0)

    def publish(self, path):
        with self.page.expect_response(lambda response: response.url.endswith(path)):
            self.page.click('#itemPublishSubmitBtn')
        self.page.wait_for_function('!itemPublishSubmitting')
        self.assertFalse(self.page_errors)

    def image_names(self):
        return self.page.locator('#publishImagePreviewList .item-publish-preview-name').all_text_contents()

    def drag_image(self, source, target):
        cards = self.page.locator('#publishImagePreviewList .item-publish-preview-card')
        cards.nth(source).drag_to(
            cards.nth(target), source_position={'x': 30, 'y': 30}, target_position={'x': 30, 'y': 30},
        )

    def multipart_images(self, sent):
        message = BytesParser(policy=policy.default).parsebytes(
            f'Content-Type: {sent["content_type"]}\r\nMIME-Version: 1.0\r\n\r\n'.encode()
            + sent['body']
        )
        return [part for part in message.iter_parts()
                if part.get_param('name', header='content-disposition') == 'images']

    def test_saved_and_reloaded_material_images_reach_publish(self):
        self.upload_images(7)
        self.save_material()
        self.assertTrue(self.page.locator('#itemPublishForm').evaluate('(form) => form.checkValidity()'))
        self.publish('/product-publish')
        first = json.loads(self.publishes[0]['body'])
        self.assertEqual(first['images'], self.materials[0]['images'])
        self.assertEqual(first['account_id'], 'test-account')

        self.page.get_by_role('button', name='重置表单', exact=True).click()
        self.page.wait_for_function('itemPublishLoadedMaterialImages.length === 0')
        self.page.select_option('#publishCookieId', 'test-account')
        self.page.get_by_role('button', name='载入', exact=True).click()
        self.assertEqual(self.page.locator('.item-publish-preview-card').count(), 7)
        self.assertTrue(self.page.locator('#itemPublishForm').evaluate('(form) => form.checkValidity()'))
        self.publish('/product-publish')
        self.assertEqual(json.loads(self.publishes[1]['body'])['images'], first['images'])

    def test_reset_without_images_is_rejected(self):
        self.upload_images(1)
        self.save_material()
        self.page.get_by_role('button', name='重置表单', exact=True).click()
        self.page.wait_for_function('itemPublishLoadedMaterialImages.length === 0')
        self.complete_form()
        self.page.click('#itemPublishSubmitBtn')
        self.page.wait_for_function("window.testToasts.includes('请至少上传 1 张商品图片或载入素材图片')")
        self.assertEqual(self.publishes, [])

    def test_new_files_replace_material_images_in_multipart_publish(self):
        self.upload_images(1)
        self.save_material()
        self.upload_images(2)
        self.publish('/item-publish')
        self.assertEqual(len(self.publishes), 1)
        images = self.multipart_images(self.publishes[0])
        self.assertEqual([image.get_filename() for image in images], ['image-0.png', 'image-1.png'])
        self.assertTrue(all(image.get_payload(decode=True) == IMAGE for image in images))

    def test_missing_product_fields_still_block_native_submission(self):
        self.upload_images(1)
        self.save_material()
        for selector in ('#publishCookieId', '#publishTitle', '#publishDescription'):
            with self.subTest(field=selector):
                original = self.page.locator(selector).input_value()
                self.page.locator(selector).evaluate('(input) => { input.value = ""; }')
                self.page.click('#itemPublishSubmitBtn')
                self.assertTrue(self.page.locator(selector).evaluate('(input) => !input.checkValidity()'))
                self.assertEqual(self.publishes, [])
                self.page.locator(selector).evaluate('(input, value) => { input.value = value; }', original)

    def test_too_many_uploads_are_rejected(self):
        self.upload_images(10)
        self.page.wait_for_function("window.testToasts.includes('单次最多上传 9 张图片')")
        self.assertEqual(self.page.locator('#publishImages').evaluate('(input) => input.files.length'), 0)
        self.assertEqual(self.publishes, [])

    def test_dragged_upload_order_is_used_for_publish_and_material_save(self):
        self.upload_images(3)
        self.drag_image(2, 0)
        expected = ['image-2.png', 'image-0.png', 'image-1.png']
        self.assertEqual(self.image_names(), expected)
        self.publish('/item-publish')
        self.assertEqual([image.get_filename() for image in self.multipart_images(self.publishes[0])], expected)
        self.save_material()
        self.assertEqual([image['filename'] for image in self.materials[0]['images']], expected)
        self.publish('/product-publish')
        self.assertEqual([image['filename'] for image in json.loads(self.publishes[1]['body'])['images']], expected)

    def test_dragged_material_order_is_saved_and_reloaded_without_changing_cached_material(self):
        self.upload_images(3)
        self.save_material()
        self.drag_image(0, 2)
        expected = ['image-1.png', 'image-2.png', 'image-0.png']
        self.assertEqual(self.image_names(), expected)
        self.assertEqual(
            self.page.evaluate('itemPublishMaterials[0].images.map(image => image.filename)'),
            ['image-0.png', 'image-1.png', 'image-2.png'],
        )
        self.publish('/product-publish')
        self.assertEqual([image['filename'] for image in json.loads(self.publishes[0]['body'])['images']], expected)
        self.save_material()
        self.page.get_by_role('button', name='重置表单', exact=True).click()
        self.page.wait_for_function('itemPublishLoadedMaterialImages.length === 0')
        self.page.select_option('#publishCookieId', 'test-account')
        self.page.get_by_role('button', name='载入', exact=True).click()
        self.assertEqual(self.image_names(), expected)
        self.publish('/product-publish')
        self.assertEqual([image['filename'] for image in json.loads(self.publishes[1]['body'])['images']], expected)

    def test_new_selection_replaces_dragged_order(self):
        self.upload_images(3)
        self.drag_image(2, 0)
        self.upload_images(2)
        self.assertEqual(self.image_names(), ['image-0.png', 'image-1.png'])
        self.publish('/item-publish')
        self.assertEqual(
            [image.get_filename() for image in self.multipart_images(self.publishes[0])],
            ['image-0.png', 'image-1.png'],
        )

    def test_order_buttons_work_with_keyboard_on_narrow_page_without_submitting(self):
        self.page.set_viewport_size({'width': 390, 'height': 1000})
        self.upload_images(3)
        self.assertTrue(self.page.get_by_role('button', name='向前移动图片 1', exact=True).is_disabled())
        self.assertTrue(self.page.get_by_role('button', name='向后移动图片 3', exact=True).is_disabled())
        button = self.page.get_by_role('button', name='向后移动图片 1', exact=True)
        button.focus()
        button.press('Enter')
        self.assertEqual(self.image_names(), ['image-1.png', 'image-0.png', 'image-2.png'])
        self.assertEqual(self.publishes, [])
        self.publish('/item-publish')
        self.assertEqual(
            [image.get_filename() for image in self.multipart_images(self.publishes[0])],
            ['image-1.png', 'image-0.png', 'image-2.png'],
        )

    def test_external_drop_does_not_reorder_images(self):
        self.upload_images(3)
        self.page.locator('.item-publish-preview-card').nth(2).evaluate('''card => {
            const transfer = new DataTransfer();
            transfer.setData('text/plain', '0');
            card.dispatchEvent(new DragEvent('drop', { bubbles: true, cancelable: true, dataTransfer: transfer }));
        }''')
        self.assertEqual(self.image_names(), ['image-0.png', 'image-1.png', 'image-2.png'])
        self.assertEqual(self.publishes, [])

    def test_stored_url_images_keep_their_order_and_fields_when_dragged(self):
        self.materials = [{
            'id': 1, 'title': '素材商品', 'description': '素材描述',
            'images': [
                {'filename': f'url-{i}.png', 'url': f'http://publish.test/material-image-{i}.png', 'width': 1, 'height': 1}
                for i in range(3)
            ],
        }]
        self.page.evaluate('loadItemPublishMaterials()')
        self.page.get_by_role('button', name='载入', exact=True).click()
        self.drag_image(2, 0)
        self.publish('/product-publish')
        self.assertEqual(
            json.loads(self.publishes[0]['body'])['images'],
            [self.materials[0]['images'][2], self.materials[0]['images'][0], self.materials[0]['images'][1]],
        )


if __name__ == '__main__':
    unittest.main()
