const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/js/app.js'), 'utf8');
const start = source.indexOf('let qrCodeModalEventsBound');
const end = source.indexOf('// ==================== 图片关键词管理功能', start);
assert.ok(start >= 0 && end > start, 'QR login UI block must be present');

const response = (status, payload) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
});

function createContext(fetchImpl) {
    const elements = Object.fromEntries([
        'qrCodeContainer',
        'qrCodeImage',
        'qrCodeImg',
        'statusText',
        'statusSpinner',
        'verificationContainer',
        'refreshQRBtn',
        'qrLoginModalTitleText',
        'qrLoginTargetHint',
    ].map(id => [id, { id, style: {}, innerHTML: '', textContent: '', src: '', disabled: false }]));
    const toasts = [];
    const context = {
        apiBase: '',
        authToken: 'control-token',
        fetch: fetchImpl,
        authenticatedFetch: fetchImpl,
        document: {
            getElementById: id => elements[id] || null,
            querySelector: () => ({ appendChild: () => {} }),
            createElement: id => ({ id, style: {}, innerHTML: '' }),
        },
        escapeHtml: value => String(value)
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;'),
        setInterval: () => 1,
        clearInterval: () => {},
        setTimeout: () => 1,
        showToast: (message, tone) => toasts.push([message, tone]),
        loadCookies: () => {},
        getAuthToken: () => 'control-token',
        bootstrap: { Modal: function Modal() {} },
        crypto: { randomUUID: () => 'test-id' },
        console,
    };
    context.bootstrap.Modal.getInstance = () => null;
    vm.runInNewContext(source.slice(start, end), context);
    return { context, elements, toasts };
}

test('retry replaces the previous QR error with a real loading state', async () => {
    let resolveRetry;
    const requests = [];
    const { context, elements } = createContext(async () => {
        requests.push(true);
        if (requests.length === 1) {
            return response(503, { detail: '连接器暂时不可用' });
        }
        return new Promise(resolve => { resolveRetry = resolve; });
    });

    assert.equal(await context.generateQRCode(), false);
    assert.match(elements.qrCodeContainer.innerHTML, /连接器暂时不可用/);
    assert.equal(elements.refreshQRBtn.disabled, false);

    const retry = context.refreshQRCode();
    assert.equal(elements.refreshQRBtn.disabled, true);
    assert.match(elements.qrCodeContainer.innerHTML, /正在生成二维码/);
    assert.doesNotMatch(elements.qrCodeContainer.innerHTML, /连接器暂时不可用/);

    resolveRetry(response(202, {
        success: true,
        session_id: 'qr-2',
        qr_code_url: 'data:image/png;base64,AA==',
        expires_at: 200,
    }));
    assert.equal(await retry, true);
    assert.equal(elements.qrCodeImage.style.display, 'block');
    assert.equal(elements.qrCodeImg.src, 'data:image/png;base64,AA==');
    assert.equal(elements.refreshQRBtn.disabled, false);
});

test('non-success polling response stops polling and exposes regeneration', async () => {
    let requestNumber = 0;
    const { context, elements, toasts } = createContext(async () => {
        requestNumber += 1;
        if (requestNumber === 1) {
            return response(202, {
                success: true,
                session_id: 'qr-1',
                qr_code_url: 'data:image/png;base64,AA==',
                expires_at: 200,
            });
        }
        return response(410, { detail: 'qr session expired' });
    });

    assert.equal(await context.generateQRCode(), true);
    await context.checkQRCodeStatus();

    assert.equal(elements.statusText.textContent, '二维码不可用，请重新生成');
    assert.match(elements.qrCodeContainer.innerHTML, /二维码会话已失效，请重新生成二维码/);
    assert.equal(elements.refreshQRBtn.disabled, false);
    assert.deepEqual(toasts.at(-1), ['二维码会话已失效，请重新生成二维码', 'warning']);
});

test('concurrent generation clicks issue only one request', async () => {
    let resolveRequest;
    let requestCount = 0;
    const { context, elements } = createContext(async () => {
        requestCount += 1;
        return new Promise(resolve => { resolveRequest = resolve; });
    });

    const first = context.generateQRCode();
    assert.equal(await context.generateQRCode(), false);
    assert.equal(requestCount, 1);
    assert.equal(elements.refreshQRBtn.disabled, true);

    resolveRequest(response(503, {}));
    assert.equal(await first, false);
    assert.equal(elements.refreshQRBtn.disabled, false);
});

test('authenticatedFetch reads the current token and redirects on 401', async () => {
    const authStart = source.indexOf('function getAuthToken');
    const authEnd = source.indexOf('// 移动端侧边栏切换', authStart);
    assert.ok(authStart >= 0 && authEnd > authStart);
    let token = 'token-1';
    let removed = 0;
    let stopped = 0;
    let redirected = '';
    const requests = [];
    const context = {
        authToken: 'stale-token',
        localStorage: {
            getItem: () => token,
            removeItem: () => { removed += 1; },
        },
        location: { href: '' },
        clearQRCodeCheck: () => { stopped += 1; },
        fetch: async (url, options) => {
            requests.push({ url, options });
            return response(requests.length === 1 ? 200 : 401, {});
        },
        console,
    };
    Object.defineProperty(context.location, 'href', {
        get: () => redirected,
        set: value => { redirected = value; },
    });
    vm.runInNewContext(source.slice(authStart, authEnd), context);

    const first = await context.authenticatedFetch('/protected');
    assert.equal(first.status, 200);
    token = 'token-2';
    const second = await context.authenticatedFetch('/protected');
    assert.equal(second.status, 401);
    assert.equal(requests[0].options.headers.Authorization, 'Bearer token-1');
    assert.equal(requests[1].options.headers.Authorization, 'Bearer token-2');
    assert.equal(removed, 1);
    assert.equal(stopped, 1);
    assert.equal(redirected, '/');
});

test('verification ticket 409 keeps the operator session authenticated', async () => {
    const authStart = source.indexOf('function getAuthToken');
    const authEnd = source.indexOf('// 移动端侧边栏切换', authStart);
    assert.ok(authStart >= 0 && authEnd > authStart);
    let removed = 0;
    let stopped = 0;
    let redirected = '';
    const context = {
        authToken: 'stale-token',
        localStorage: {
            getItem: () => 'control-token',
            removeItem: () => { removed += 1; },
        },
        location: { pathname: '/admin', href: '' },
        clearQRCodeCheck: () => { stopped += 1; },
        fetch: async () => response(409, {
            detail: '验证会话票据已失效，请重新发起人工验证',
        }),
        console,
    };
    Object.defineProperty(context.location, 'href', {
        get: () => redirected,
        set: value => { redirected = value; },
    });
    vm.runInNewContext(source.slice(authStart, authEnd), context);

    const result = await context.authenticatedFetch('/api/accounts/account-1/verification-sessions/verify-1');

    assert.equal(result.status, 409);
    assert.equal(context.authToken, 'control-token');
    assert.equal(removed, 0);
    assert.equal(stopped, 0);
    assert.equal(redirected, '');
});

test('checkAuth bypasses caches before consuming connection mode', async () => {
    const checkAuthStart = source.indexOf('async function checkAuth()');
    const checkAuthEnd = source.indexOf('// 初始化事件监听', checkAuthStart);
    assert.ok(checkAuthStart >= 0 && checkAuthEnd > checkAuthStart);
    const requests = [];
    const modes = [];
    const context = {
        getAuthToken: () => 'control-token',
        fetch: async (url, options) => {
            requests.push({ url, options });
            return response(200, {
                authenticated: true,
                is_admin: false,
                connection_mode: 'external_connector',
            });
        },
        applyFrontendConnectionMode: mode => { modes.push(mode); },
        localStorage: { removeItem: () => assert.fail('authenticated response must keep token') },
        window: { location: { href: '' } },
        document: { getElementById: () => null },
        loadRiskControlNightSettings: async () => {},
    };
    vm.runInNewContext(source.slice(checkAuthStart, checkAuthEnd), context);

    assert.equal(await context.checkAuth(), true);
    assert.equal(requests[0].url, '/verify');
    assert.equal(requests[0].options.cache, 'no-store');
    assert.equal(requests[0].options.headers.Authorization, 'Bearer control-token');
    assert.deepEqual(modes, ['external_connector']);
});

test('verification_required returned while waiting goes straight to handoff', async () => {
    const { context } = createContext(async () => response(200, {
        status: 'waiting',
        verification_required: true,
    }));
    let handoff = 0;
    context.handoffQrToManualVerification = async (_data, sessionId) => {
        handoff += 1;
        assert.equal(sessionId, 'qr-waiting');
        return true;
    };
    vm.runInNewContext(`
        qrLoginTargetAccountId = 'account-1';
        qrCodeVerificationState.sessionId = 'qr-waiting';
        qrCodeVerificationState.activeSessionId = 'qr-waiting';
        qrCodeVerificationState.authProgressObserved = false;
        qrCodeVerificationState.completed = false;
    `, context);

    await context.checkQRCodeStatus();

    assert.equal(handoff, 1);
});

test('terminal QR completion keeps completed state after stopping polling', async () => {
    const { context } = createContext(async () => response(200, {
        status: 'success',
        message: 'done',
    }));
    vm.runInNewContext(`
        qrCodeVerificationState.sessionId = 'qr-terminal';
        qrCodeVerificationState.activeSessionId = 'qr-terminal';
        qrCodeVerificationState.completed = false;
    `, context);

    await context.checkQRCodeStatus();

    assert.equal(vm.runInNewContext('qrCodeVerificationState.completed', context), true);
    assert.equal(vm.runInNewContext('qrCodeVerificationState.activeSessionId', context), 'qr-terminal');
});

test('external connector mode rejects unscoped QR but keeps targeted relogin', () => {
    const { context, toasts } = createContext(async () => response(500, {}));
    vm.runInNewContext(`applyFrontendConnectionMode('external_connector')`, context);

    assert.equal(context.showQRCodeLogin('lite'), false);
    assert.deepEqual(toasts.at(-1), [
        '独立连接器模式不支持无目标账号扫码，请在账号列表使用“重登”',
        'warning',
    ]);
    assert.equal(
        vm.runInNewContext(`
            qrLoginTargetAccountId = 'account-1';
            getQRLoginEndpoints().generate;
        `, context),
        '/api/accounts/account-1/qr-sessions',
    );
});

test('standalone QR buttons declare the legacy-only capability', () => {
    const page = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
    const legacyButtons = page.match(/<button\b[^>]*data-requires-legacy-connection="true"[^>]*>/gs) || [];

    assert.equal(legacyButtons.length, 2);
    legacyButtons.forEach(button => {
        assert.match(button, /\bhidden\b/);
        assert.match(button, /\bdisabled\b/);
    });
    assert.match(page, /id="connectorQrModeHint"/);
});

test('legacy QR actions stay locked until connection mode is confirmed', () => {
    const { context } = createContext(async () => response(500, {}));
    const legacyButtons = [
        { hidden: true, disabled: true, setAttribute: () => {} },
        { hidden: true, disabled: true, setAttribute: () => {} },
    ];
    const hint = { hidden: true };
    context.document.querySelectorAll = selector => (
        selector === '[data-requires-legacy-connection="true"]' ? legacyButtons : []
    );
    const originalGetElementById = context.document.getElementById;
    context.document.getElementById = id => (
        id === 'connectorQrModeHint' ? hint : originalGetElementById(id)
    );

    assert.equal(legacyButtons.every(button => button.hidden && button.disabled), true);
    context.applyFrontendConnectionMode('legacy');
    assert.equal(legacyButtons.every(button => !button.hidden && !button.disabled), true);
    assert.equal(hint.hidden, true);

    context.applyFrontendConnectionMode('external_connector');
    assert.equal(legacyButtons.every(button => button.hidden && button.disabled), true);
    assert.equal(hint.hidden, false);
});
