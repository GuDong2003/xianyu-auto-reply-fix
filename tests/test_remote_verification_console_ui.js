const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const modulePath = path.join(root, 'static/js/remote-verification-console.js');
const remote = require(modulePath);

const makeView = calls => ({
    showConsole: (accountId, viewerUrl) => calls.push(['console', accountId, viewerUrl]),
    showChecking: () => calls.push(['checking']),
    showResult: (state, message) => calls.push(['result', state, message]),
    showError: message => calls.push(['error', message]),
});

test('direct start omits idempotency header and lets control derive the runtime key', async () => {
    const requests = [];
    const views = [];
    const controller = remote.createController({
        request: async (url, options) => {
            requests.push({ url, options });
            if (requests.length === 1) {
                return { ok: true, json: async () => ({ session_id: 'verify-1' }) };
            }
            return {
                ok: true,
                json: async () => ({
                    viewer_url: '/api/accounts/account-1/verification-sessions/verify-1/viewer',
                    websocket_url: '/api/accounts/account-1/verification-sessions/verify-1/remote',
                }),
            };
        },
        view: makeView(views),
        delay: async () => {},
    });

    const started = await controller.start({ accountId: 'account-1', authToken: 'bearer' });

    assert.equal(started, true);
    assert.equal(requests[0].url, '/api/accounts/account-1/verification-sessions');
    assert.equal(Object.hasOwn(requests[0].options.headers, 'Idempotency-Key'), false);
    assert.equal(
        requests[1].url,
        '/api/accounts/account-1/verification-sessions/verify-1/remote-proof',
    );
    assert.equal(requests[1].options.body, undefined);
    assert.deepEqual(views, [[
        'console',
        'account-1',
        '/api/accounts/account-1/verification-sessions/verify-1/viewer',
    ]]);
});

test('QR start reuses the provided stable idempotency key for retries', async () => {
    const requests = [];
    const controller = remote.createController({
        request: async (url, options) => {
            requests.push({ url, options });
            if (url.endsWith('/verification-sessions')) {
                return { ok: true, json: async () => ({ session_id: 'verify-stable' }) };
            }
            return { ok: false, status: 503, json: async () => ({ detail: 'browser not ready' }) };
        },
        view: makeView([]),
    });

    assert.equal(await controller.start({
        accountId: 'account-1',
        authToken: 'bearer',
        idempotencyKey: 'remote-create:qr-stable',
    }), false);
    assert.equal(await controller.start({
        accountId: 'account-1',
        authToken: 'bearer',
        idempotencyKey: 'remote-create:qr-stable',
    }), false);
    assert.equal(requests[0].options.headers['Idempotency-Key'], 'remote-create:qr-stable');
    assert.equal(requests[2].options.headers['Idempotency-Key'], 'remote-create:qr-stable');
});

test('complete persists browser cookies and polls through verifying to succeeded', async () => {
    const responses = [
        { session_id: 'verify-1' },
        {
            viewer_url: '/api/accounts/account-1/verification-sessions/verify-1/viewer',
            websocket_url: '/api/accounts/account-1/verification-sessions/verify-1/remote',
        },
        { state: 'verifying' },
        { state: 'verifying' },
        { state: 'succeeded' },
    ];
    const requests = [];
    const views = [];
    const controller = remote.createController({
        request: async (url, options) => {
            requests.push({ url, options });
            const payload = responses.shift();
            return { ok: true, json: async () => payload };
        },
        view: makeView(views),
        delay: async () => {},
    });
    await controller.start({ accountId: 'account-1', authToken: 'bearer' });

    const state = await controller.complete();

    assert.equal(state, 'succeeded');
    assert.equal(
        requests[2].url,
        '/api/accounts/account-1/verification-sessions/verify-1/complete',
    );
    assert.equal(requests[2].options.body, undefined);
    assert.equal(requests[3].url, '/api/accounts/account-1/verification-sessions/verify-1');
    assert.deepEqual(views.at(-1), ['result', 'succeeded', '验证成功，账号连接已恢复。']);
});

test('closing the console invalidates the active session and allows a clean reopen', async () => {
    const requests = [];
    const views = [];
    let sessionNumber = 0;
    const controller = remote.createController({
        request: async (url, options) => {
            requests.push({ url, options });
            if (url.endsWith('/verification-sessions')) {
                sessionNumber += 1;
                return { ok: true, json: async () => ({ session_id: `verify-${sessionNumber}` }) };
            }
            return {
                ok: true,
                json: async () => ({
                    viewer_url: `/api/accounts/account-1/verification-sessions/verify-${sessionNumber}/viewer`,
                    websocket_url: `/api/accounts/account-1/verification-sessions/verify-${sessionNumber}/remote`,
                }),
            };
        },
        view: makeView(views),
        delay: async () => {},
    });

    assert.equal(await controller.start({ accountId: 'account-1', authToken: 'bearer' }), true);
    controller.close();
    assert.equal(await controller.complete(), false);
    assert.equal(requests.length, 2);

    assert.equal(await controller.start({ accountId: 'account-1', authToken: 'bearer' }), true);
    assert.equal(views.filter(call => call[0] === 'console').length, 2);
});

test('logout revocation clears the active proof through its narrow session path', async () => {
    const requests = [];
    const controller = remote.createController({
        request: async (url, options) => {
            requests.push({ url, options });
            if (requests.length === 1) return { ok: true, json: async () => ({ session_id: 'verify-1' }) };
            if (requests.length === 2) {
                return {
                    ok: true,
                    json: async () => ({
                        viewer_url: '/api/accounts/account-1/verification-sessions/verify-1/viewer',
                        websocket_url: '/api/accounts/account-1/verification-sessions/verify-1/remote',
                    }),
                };
            }
            return { ok: true, json: async () => ({}) };
        },
        view: makeView([]),
    });
    await controller.start({ accountId: 'account-1', authToken: 'bearer' });

    assert.equal(await controller.revoke(), true);
    assert.equal(requests[2].url, '/api/accounts/account-1/verification-sessions/verify-1/remote-proof');
    assert.equal(requests[2].options.method, 'DELETE');
    assert.equal(await controller.complete(), false);
});

for (const terminal of [
    { payload: { state: 'failed', reason_message: 'Cookie 保存失败' }, expected: 'failed' },
    { payload: { state: 'new_challenge', reason_message: '平台发起了新验证' }, expected: 'new_challenge' },
    { payload: { state: 'manual_device_required' }, expected: 'new_challenge' },
]) {
    test(`terminal ${terminal.expected} is rendered instead of checking forever`, async () => {
        const responses = [
            { session_id: 'verify-1' },
            {
                viewer_url: '/api/accounts/account-1/verification-sessions/verify-1/viewer',
                websocket_url: '/api/accounts/account-1/verification-sessions/verify-1/remote',
            },
            { state: 'verifying' },
            terminal.payload,
        ];
        const views = [];
        const controller = remote.createController({
            request: async () => ({ ok: true, json: async () => responses.shift() }),
            view: makeView(views),
            delay: async () => {},
        });
        await controller.start({ accountId: 'account-1', authToken: 'bearer' });

        const state = await controller.complete();

        assert.equal(state, terminal.expected);
        assert.equal(views.at(-1)[0], 'result');
        assert.equal(views.at(-1)[1], terminal.expected);
    });
}

test('viewer uses bundled noVNC and blocks keyboard clipboard and file drop', () => {
    const viewer = fs.readFileSync(path.join(root, 'static/js/remote-verification-viewer.js'), 'utf8');
    const page = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
    const app = fs.readFileSync(path.join(root, 'static/js/app.js'), 'utf8');

    assert.match(viewer, /vendor\/novnc\/core\/rfb\.js/);
    assert.match(viewer, /new RFB\(/);
    assert.match(viewer, /keydown|keyup/);
    assert.match(viewer, /copy|cut|paste/);
    assert.match(viewer, /dragover|drop/);
    assert.doesNotMatch(viewer, /localStorage|clipboardPasteFrom|token=|access_token/);
    assert.match(page, /remote-verification-console\.js/);
    assert.doesNotMatch(page, /local-verification-handoff\.js/);
    assert.match(app, /window\.RemoteVerificationConsole\.start/);
    assert.match(app, /window\.RemoteVerificationConsole\.revoke/);
    assert.doesNotMatch(app, /LocalVerificationHandoff|openLocalVerificationHandoff/);
    assert.doesNotMatch(app, /getNoVncUrl|http:\/\/[^'"\s]*:6080/);
});

test('remote verification assets use a fresh immutable-cache revision', () => {
    const revision = '20260719-rfb2';
    const appRevision = '20260719-qr4';
    const viewerRevision = '20260719-rfb1';
    const page = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
    const appCss = fs.readFileSync(path.join(root, 'static/css/app.css'), 'utf8');
    const viewer = fs.readFileSync(path.join(root, 'static/js/remote-verification-viewer.js'), 'utf8');
    const router = fs.readFileSync(path.join(root, 'xianyu_control/accounts_router.py'), 'utf8');

    assert.match(page, new RegExp(`app\\.css\\?v=${viewerRevision}`));
    assert.match(page, new RegExp(`remote-verification-console\\.js\\?v=${revision}`));
    assert.match(page, new RegExp(`app\\.js\\?v=${appRevision}`));
    assert.match(appCss, new RegExp(`remote-verification-console\\.css\\?v=${viewerRevision}`));
    assert.match(router, new RegExp(`remote-verification-viewer\\.css\\?v=${viewerRevision}`));
    assert.match(router, new RegExp(`remote-verification-viewer\\.js\\?v=${viewerRevision}`));
    assert.match(viewer, /vendor\/novnc\/core\/rfb\.js\?v=1\.7\.0/);
    assert.doesNotMatch(page, /remote-verification-console\.js\?v=1\.0\.0|app\.js\?v=(?:1\.2\.9|20260719-(?:rfb1|qr1|qr2|qr3))/);
});

test('console unloads the viewer iframe and closes its controller on modal teardown', () => {
    const consoleSource = fs.readFileSync(modulePath, 'utf8');
    assert.match(consoleSource, /hidden\.bs\.modal/);
    assert.match(consoleSource, /frame\.src\s*=\s*['"]about:blank['"]/);
    assert.match(consoleSource, /handlers\.onClose\(\)/);
});
