const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/js/app.js'), 'utf8');
const start = source.indexOf('function getConnectorRuntimeState');
const end = source.indexOf('function isRuntimeStatusHealthy');
assert.ok(start >= 0 && end > start, 'connector runtime helpers must be present');

const context = {};
vm.runInNewContext(source.slice(start, end), context);

test('QR handoff no longer collapses all failures into a missing-session message', () => {
    assert.doesNotMatch(source, /扫码认证未生成可用的人工验证会话|当前没有可继续的人工验证会话/);
    assert.match(source, /扫码后连接校验超时，请重新生成二维码/);
});

test('manual state wins over a stale online connector_state', () => {
    assert.equal(context.isConnectorManualVerificationRequired({
        connector_state: 'online',
        state: 'manual_verification_required',
    }), true);
});

test('manual reason wins over stale non-manual state fields', () => {
    assert.equal(context.isConnectorManualVerificationRequired({
        connector_state: 'online',
        runtime_state: 'running',
        connector_reason_code: 'old_status',
        reason_code: 'risk_challenge',
    }), true);
});

test('healthy online state is not marked for manual verification', () => {
    assert.equal(context.isConnectorManualVerificationRequired({
        connector_state: 'online',
        reason_code: '',
    }), false);
});

test('QR handoff requires risk challenge state from the current runtime', () => {
    assert.equal(context.isQrManualVerificationHandoffAllowed({
        state: 'manual_verification_required',
        reason_code: 'risk_challenge',
    }), true);
    assert.equal(context.isQrManualVerificationHandoffAllowed({
        state: 'manual_verification_required',
        reason_code: 'authentication_timeout',
    }), false);
    assert.equal(context.isQrManualVerificationHandoffAllowed({
        state: 'manual_verification_required',
        reason_code: 'risk_challenge',
        entered_at: '2026-07-18T10:00:00Z',
    }, Date.parse('2026-07-18T10:01:00Z') / 1000), false);
    assert.equal(context.isQrManualVerificationHandoffAllowed({
        state: 'manual_verification_required',
        reason_code: 'risk_challenge',
        entered_at: '2026-07-18T10:00:00Z',
    }, Date.parse('2026-07-18T10:01:00Z') / 1000, { allowStaleEnteredAt: true }), true);
});

test('runtime handoff accepts a stale entered_at after current QR progress is observed', async () => {
    const loaderStart = source.indexOf('async function loadQrManualVerificationRuntime');
    const loaderEnd = source.indexOf('function hideQrModalForManualVerification', loaderStart);
    const calls = [];
    const loaderContext = {
        apiBase: '',
        getAuthToken: () => 'control-token',
        qrCodeVerificationState: { startedAt: 200, authProgressObserved: true },
        authenticatedFetch: async () => ({
            ok: true,
            json: async () => ({
                state: 'manual_verification_required',
                reason_code: 'risk_challenge',
                entered_at: '2026-07-18T10:00:00Z',
            }),
        }),
        isQrManualVerificationHandoffAllowed: (_runtime, _startedAt, options) => {
            calls.push(options);
            return true;
        },
    };
    vm.runInNewContext(source.slice(loaderStart, loaderEnd), loaderContext);

    const result = await loaderContext.loadQrManualVerificationRuntime('account-1', {
        allowStaleEnteredAt: true,
    });

    assert.equal(result.runtime.state, 'manual_verification_required');
    assert.equal(calls.length, 1);
    assert.equal(calls[0].allowStaleEnteredAt, true);
});

test('runtime timeout reason is surfaced as a QR regeneration failure', async () => {
    const loaderStart = source.indexOf('async function loadQrManualVerificationRuntime');
    const loaderEnd = source.indexOf('function hideQrModalForManualVerification', loaderStart);
    const loaderContext = {
        apiBase: '',
        getAuthToken: () => 'control-token',
        qrCodeVerificationState: { startedAt: 200, authProgressObserved: true },
        authenticatedFetch: async () => ({
            ok: true,
            json: async () => ({ state: 'manual_verification_required', reason_code: 'authentication_timeout' }),
        }),
        isQrManualVerificationHandoffAllowed: () => true,
    };
    vm.runInNewContext(source.slice(loaderStart, loaderEnd), loaderContext);

    const result = await loaderContext.loadQrManualVerificationRuntime('account-1');

    assert.equal(result.runtime, null);
    assert.equal(result.failure.kind, 'timeout');
    assert.equal(result.failure.message, '扫码后连接校验超时，请重新生成二维码');
});

test('runtime fetch rejection stays inside the bounded QR handoff retry', async () => {
    const loaderStart = source.indexOf('async function loadQrManualVerificationRuntime');
    const loaderEnd = source.indexOf('function hideQrModalForManualVerification', loaderStart);
    const loaderContext = {
        apiBase: '',
        getAuthToken: () => 'control-token',
        qrCodeVerificationState: { startedAt: 200, authProgressObserved: true },
        authenticatedFetch: async () => { throw new Error('connection reset'); },
        isQrManualVerificationHandoffAllowed: () => false,
        console: { error: () => {} },
    };
    vm.runInNewContext(source.slice(loaderStart, loaderEnd), loaderContext);

    const result = await loaderContext.loadQrManualVerificationRuntime('account-1');

    assert.equal(result.runtime, null);
    assert.equal(result.failure.kind, 'network');
    assert.equal(result.failure.message, '扫码后连接校验暂时不可用，请稍后重试');
});

test('historical manual state cannot hand off before QR auth progress', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    assert.ok(handoffStart >= 0 && handoffEnd > handoffStart);
    const calls = { runtime: 0, start: 0 };
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: false,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => {
            calls.runtime += 1;
            return { state: 'manual_verification_required', reason_code: 'risk_challenge' };
        },
        startManualVerification: async () => {
            calls.start += 1;
            return true;
        },
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => {},
        showToast: () => {},
        loadCookies: () => {},
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => ({ textContent: '' }) },
        setTimeout: callback => callback(),
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    const result = await handoffContext.handoffQrToManualVerification({}, 'qr-1');

    assert.equal(result, false);
    assert.deepEqual(calls, { runtime: 0, start: 0 });
});

test('QR verification failure cannot open manual verification without an available runtime', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    const calls = { start: 0 };
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: true,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => null,
        startManualVerification: async () => {
            calls.start += 1;
            return true;
        },
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => {},
        showToast: () => {},
        loadCookies: () => {},
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => ({ textContent: '' }) },
        setTimeout: callback => callback(),
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    const result = await handoffContext.handoffQrToManualVerification({}, 'qr-1');

    assert.equal(result, false);
    assert.equal(calls.start, 0);
});

test('current QR risk challenge hands off with the matching session id', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    const calls = { options: null, cleared: 0, loaded: 0 };
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: true,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => ({
            state: 'manual_verification_required',
            reason_code: 'risk_challenge',
        }),
        startManualVerification: async (accountId, options) => {
            assert.equal(accountId, 'account-1');
            calls.options = options;
            return true;
        },
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => { calls.cleared += 1; },
        showToast: () => {},
        loadCookies: () => { calls.loaded += 1; },
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => ({ textContent: '' }) },
        setTimeout: callback => callback(),
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    const result = await handoffContext.handoffQrToManualVerification({}, 'qr-1');

    assert.equal(result, true);
    assert.equal(calls.options.qrSessionId, 'qr-1');
    assert.equal(calls.options.deferModal, true);
    assert.equal(calls.options.beforeOpen, handoffContext.hideQrModalForManualVerification);
    assert.equal(handoffContext.qrCodeVerificationState.completed, true);
    assert.equal(calls.cleared, 1);
    assert.equal(calls.loaded, 1);
});

test('QR handoff retries transient runtime propagation without clearing the session', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    const calls = { runtime: 0, start: 0, cleared: 0 };
    const status = { textContent: '' };
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: true,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => {
            calls.runtime += 1;
            if (calls.runtime < 3) {
                return { runtime: null, failure: { kind: 'pending', message: '正在同步' } };
            }
            return { runtime: { state: 'manual_verification_required', reason_code: 'risk_challenge' } };
        },
        startManualVerification: async () => { calls.start += 1; return true; },
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => { calls.cleared += 1; },
        showToast: () => {},
        loadCookies: () => {},
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => status },
        setTimeout: callback => callback(),
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    assert.equal(await handoffContext.handoffQrToManualVerification({}, 'qr-1'), true);
    assert.deepEqual(calls, { runtime: 3, start: 1, cleared: 1 });
    assert.match(status.textContent, /正在准备人工验证窗口/);
});

test('QR handoff preserves the remote console error after bounded retries', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: true,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => ({
            runtime: { state: 'manual_verification_required', reason_code: 'risk_challenge' },
        }),
        startManualVerification: async (_accountId, options) => {
            options.onError('连接器返回 503：验证浏览器尚未就绪');
            return false;
        },
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => assert.fail('failed handoff must not clear QR inside the retry loop'),
        showToast: () => {},
        loadCookies: () => {},
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => ({ textContent: '' }) },
        setTimeout: callback => callback(),
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    assert.equal(await handoffContext.handoffQrToManualVerification({}, 'qr-1'), false);
    assert.equal(handoffContext.qrCodeVerificationState.handoffFailure.kind, 'remote');
    assert.equal(handoffContext.qrCodeVerificationState.handoffFailure.message, '连接器返回 503：验证浏览器尚未就绪');
});

test('QR handoff classifies authentication timeout as a regeneration failure', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: true,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => ({
            runtime: null,
            failure: { kind: 'timeout', message: '扫码后连接校验超时，请重新生成二维码' },
        }),
        startManualVerification: async () => assert.fail('timeout must not open remote verification'),
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => {},
        showToast: () => {},
        loadCookies: () => {},
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => ({ textContent: '' }) },
        setTimeout: callback => callback(),
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    assert.equal(await handoffContext.handoffQrToManualVerification({}, 'qr-1'), false);
    assert.equal(handoffContext.qrCodeVerificationState.handoffFailure.kind, 'timeout');
    assert.equal(handoffContext.qrCodeVerificationState.handoffFailure.message, '扫码后连接校验超时，请重新生成二维码');
});

test('QR handoff preserves a real network failure over later pending responses', async () => {
    const handoffStart = source.indexOf('async function handoffQrToManualVerification');
    const handoffEnd = source.indexOf('// 检查二维码状态', handoffStart);
    const handoffContext = {
        qrCodeVerificationState: {
            authProgressObserved: true,
            activeSessionId: 'qr-1',
            completed: false,
        },
        qrLoginTargetAccountId: 'account-1',
        loadQrManualVerificationRuntime: async () => ({
            runtime: null,
            failure: handoffContext.calls++ === 0
                ? { kind: 'network', message: '状态接口网络错误' }
                : { kind: 'pending', message: '人工验证窗口正在准备，请稍候' },
        }),
        startManualVerification: async () => assert.fail('pending runtime must not open remote verification'),
        hideQrModalForManualVerification: async () => {},
        clearQRCodeCheck: () => {},
        showToast: () => {},
        loadCookies: () => {},
        QR_HANDOFF_RETRY_LIMIT: 3,
        QR_HANDOFF_RETRY_DELAY_MS: 0,
        document: { getElementById: () => ({ textContent: '' }) },
        setTimeout: callback => callback(),
        calls: 0,
    };
    vm.runInNewContext(source.slice(handoffStart, handoffEnd), handoffContext);

    assert.equal(await handoffContext.handoffQrToManualVerification({}, 'qr-1'), false);
    assert.equal(handoffContext.qrCodeVerificationState.handoffFailure.kind, 'network');
    assert.equal(handoffContext.qrCodeVerificationState.handoffFailure.message, '状态接口网络错误');
});

test('QR handoff passes one stable idempotency key through the remote console lifecycle', async () => {
    const startManual = source.indexOf('async function startManualVerification');
    const endManual = source.indexOf('// 显示扫码登录模态框', startManual);
    const openCalls = [];
    const context = {
        qrCodeVerificationState: {
            activeSessionId: 'qr-stable',
            authProgressObserved: true,
        },
        openRemoteVerificationConsole: async (accountId, options) => {
            openCalls.push({ accountId, options });
            return true;
        },
        showToast: () => {},
    };
    vm.runInNewContext(source.slice(startManual, endManual), context);

    assert.equal(await context.startManualVerification('account-1', { qrSessionId: 'qr-stable' }), true);
    assert.equal(openCalls[0].options.idempotencyKey, 'remote-create:account-1:qr-stable');
});

test('legacy screenshot captcha modal and entry are removed', () => {
    assert.doesNotMatch(source, /captchaVerifyModal|captchaIframe|showCaptchaVerificationModal|\/api\/captcha\/control/);
    const page = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
    assert.doesNotMatch(page, /captchaVerifyModal|captchaIframe/);
});

test('account verification action opens the remote verification console', async () => {
    const openStart = source.indexOf('async function openRemoteVerificationConsole');
    const openEnd = source.indexOf('async function startManualVerification', openStart);
    assert.ok(openStart >= 0 && openEnd > openStart);
    const calls = [];
    const toasts = [];
    const openContext = {
        window: {
            RemoteVerificationConsole: {
                start: async options => {
                    calls.push(options);
                    return true;
                },
            },
        },
        getAuthToken: () => 'control-token',
        authenticatedFetch: async () => ({ ok: true }),
        showToast: (message, tone) => toasts.push([message, tone]),
        loadCookies: () => {},
    };
    vm.runInNewContext(source.slice(openStart, openEnd), openContext);

    const result = await openContext.openRemoteVerificationConsole('account-1');

    assert.equal(result, true);
    assert.equal(calls.length, 1);
    assert.equal(calls[0].accountId, 'account-1');
    assert.equal(calls[0].authToken, 'control-token');
    calls[0].onToast('验证入口失败', 'danger');
    assert.deepEqual(toasts, [['验证入口失败', 'danger']]);
    assert.equal(calls[0].onRefresh, openContext.loadCookies);
});
