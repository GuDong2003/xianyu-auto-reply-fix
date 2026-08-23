(function registerRemoteVerificationConsole(root, factory) {
    const api = factory();
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    if (root) root.RemoteVerificationConsole = api;
}(typeof window !== 'undefined' ? window : null, function createRemoteVerificationConsole() {
    const terminalState = payload => {
        const state = String(payload?.state || '').toLowerCase();
        const reason = String(payload?.reason_code || '').toLowerCase();
        if (state === 'succeeded') return 'succeeded';
        if (state === 'new_challenge' || state === 'manual_device_required' || ['risk_challenge', 'new_challenge'].includes(reason)) return 'new_challenge';
        if (['failed', 'expired', 'cancelled'].includes(state)) return 'failed';
        return null;
    };

    const createController = ({ request, view, delay = ms => new Promise(resolve => setTimeout(resolve, ms)), pollLimit = 90 }) => {
        let active = null;
        let starting = false;
        let checking = null;
        let generation = 0;
        const errorMessage = async (response, fallback) => {
            const payload = await response.json().catch(() => ({}));
            return String(payload.detail || payload.message || fallback);
        };
        const pathFor = (value, accountId, sessionId, suffix) => {
            const expected = `/api/accounts/${encodeURIComponent(accountId)}/verification-sessions/${encodeURIComponent(sessionId)}/${suffix}`;
            return typeof value === 'string' && value === expected;
        };
        const start = async options => {
            if (starting || !options.authToken || !String(options.accountId || '').trim()) return false;
            const accountId = String(options.accountId).trim();
            const startGeneration = generation;
            starting = true;
            try {
                const providedKey = String(options.idempotencyKey || '').trim();
                const headers = { Authorization: `Bearer ${options.authToken}` };
                if (providedKey) headers['Idempotency-Key'] = providedKey;
                const createResponse = await request(`/api/accounts/${encodeURIComponent(accountId)}/verification-sessions`, {
                    method: 'POST',
                    headers,
                });
                if (!createResponse.ok) throw new Error(await errorMessage(createResponse, '创建验证会话失败'));
                const created = await createResponse.json();
                const sessionId = String(created.session_id || '').trim();
                if (!sessionId) throw new Error('服务器未返回验证会话');
                const proofResponse = await request(`/api/accounts/${encodeURIComponent(accountId)}/verification-sessions/${encodeURIComponent(sessionId)}/remote-proof`, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${options.authToken}` },
                });
                if (!proofResponse.ok) throw new Error(await errorMessage(proofResponse, '远程验证入口不可用'));
                const access = await proofResponse.json();
                if (!pathFor(access.viewer_url, accountId, sessionId, 'viewer') || !pathFor(access.websocket_url, accountId, sessionId, 'remote')) {
                    throw new Error('服务器未返回有效的远程验证入口');
                }
                if (generation !== startGeneration) return false;
                if (typeof options.beforeOpen === 'function') await options.beforeOpen();
                if (generation !== startGeneration) return false;
                active = { accountId, authToken: options.authToken, sessionId, onRefresh: options.onRefresh, generation };
                view.showConsole(accountId, access.viewer_url);
                return true;
            } catch (error) {
                const message = error.message || '远程验证入口不可用';
                view.showError(message);
                options.onToast?.(message, 'danger');
                return false;
            } finally {
                starting = false;
            }
        };
        const isCurrent = current => active === current && current.generation === generation;
        const poll = async current => {
            const url = `/api/accounts/${encodeURIComponent(current.accountId)}/verification-sessions/${encodeURIComponent(current.sessionId)}`;
            for (let attempt = 0; attempt < pollLimit; attempt += 1) {
                if (!isCurrent(current)) return null;
                const response = await request(url, { headers: { Authorization: `Bearer ${current.authToken}` } });
                if (!isCurrent(current)) return null;
                if (response.status === 409) return 'new_challenge';
                if (response.status === 404 || response.status === 410) return 'failed';
                if (!response.ok) throw new Error(await errorMessage(response, '验证状态检查失败'));
                const payload = await response.json();
                const state = terminalState(payload);
                if (state) return { state, message: payload.reason_message };
                await delay(1000);
            }
            return { state: 'failed', message: '验证状态检查超时，请重新发起验证。' };
        };
        const complete = async () => {
            const current = active;
            if (!current || checking === current) return false;
            checking = current;
            view.showChecking();
            try {
                const url = `/api/accounts/${encodeURIComponent(current.accountId)}/verification-sessions/${encodeURIComponent(current.sessionId)}/complete`;
                const response = await request(url, {
                    method: 'POST',
                    headers: { Authorization: `Bearer ${current.authToken}` },
                });
                if (!isCurrent(current)) return false;
                if (!response.ok) throw new Error(await errorMessage(response, '验证完成请求失败'));
                const result = await poll(current);
                if (!result || !isCurrent(current)) return false;
                const state = typeof result === 'string' ? result : result.state;
                const message = typeof result === 'string' ? '' : result.message;
                const messages = {
                    succeeded: '验证成功，账号连接已恢复。',
                    failed: message || '验证失败，请重新发起验证。',
                    new_challenge: message || '平台要求重新完成验证。',
                };
                view.showResult(state, messages[state]);
                if (state === 'succeeded') await current.onRefresh?.();
                return state;
            } catch (error) {
                if (!isCurrent(current)) return false;
                const message = error.message || '验证状态检查失败';
                view.showError(message, true);
                return false;
            } finally {
                if (checking === current) checking = null;
            }
        };
        const close = () => {
            generation += 1;
            active = null;
            checking = null;
        };
        const revoke = async () => {
            const current = active;
            close();
            if (!current) return true;
            try {
                const response = await request(`/api/accounts/${encodeURIComponent(current.accountId)}/verification-sessions/${encodeURIComponent(current.sessionId)}/remote-proof`, {
                    method: 'DELETE',
                    headers: { Authorization: `Bearer ${current.authToken}` },
                });
                return response.ok;
            } catch {
                return false;
            }
        };
        return { start, complete, close, revoke };
    };

    const createDomView = () => {
        let modal = document.getElementById('remoteVerificationConsoleModal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'remoteVerificationConsoleModal';
            modal.className = 'modal fade remote-verification-console-modal';
            modal.tabIndex = -1;
            modal.setAttribute('role', 'dialog');
            modal.setAttribute('aria-modal', 'true');
            modal.innerHTML = `<div class="modal-dialog modal-xl modal-dialog-centered"><div class="modal-content"><div class="modal-header"><h5 class="modal-title" id="remoteVerificationConsoleTitle">闲鱼人工验证</h5><button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="关闭"></button></div><div class="modal-body"><p id="remoteVerificationConsoleStatus" class="remote-verification-console-status" role="status" aria-live="polite"></p><iframe id="remoteVerificationConsoleFrame" title="人工验证浏览器" sandbox="allow-scripts allow-same-origin"></iframe></div><div class="modal-footer"><button id="remoteVerificationConsoleComplete" class="btn btn-success" type="button">验证完成，检查账号</button><button class="btn btn-outline-secondary" type="button" data-bs-dismiss="modal">关闭</button></div></div></div>`;
            document.body.appendChild(modal);
        }
        const frame = modal.querySelector('#remoteVerificationConsoleFrame');
        const status = modal.querySelector('#remoteVerificationConsoleStatus');
        const complete = modal.querySelector('#remoteVerificationConsoleComplete');
        const instance = () => bootstrap.Modal.getInstance(modal) || new bootstrap.Modal(modal, { backdrop: 'static' });
        return {
            bind: handlers => {
                complete.addEventListener('click', handlers.onComplete);
                modal.addEventListener('hidden.bs.modal', () => {
                    frame.src = 'about:blank';
                    status.textContent = '';
                    complete.disabled = true;
                    delete modal.dataset.result;
                    handlers.onClose();
                });
                window.addEventListener('message', event => {
                    if (event.origin !== window.location.origin || event.source !== frame.contentWindow) return;
                    if (event.data?.type === 'remote-verification-viewer' && event.data.state === 'connected') {
                        status.textContent = '实时验证已连接，可使用鼠标完成验证。';
                    }
                });
            },
            showConsole: (accountId, viewerUrl) => {
                modal.querySelector('#remoteVerificationConsoleTitle').textContent = `账号 ${accountId} · 人工验证`;
                status.textContent = '正在打开实时验证浏览器...';
                frame.src = viewerUrl;
                complete.disabled = false;
                instance().show();
            },
            showChecking: () => { status.textContent = '验证已提交，正在检查账号恢复状态...'; complete.disabled = true; },
            showResult: (state, message) => { status.textContent = message; complete.disabled = true; modal.dataset.result = state; },
            showError: (message, canRetry = false) => { status.textContent = message; complete.disabled = !canRetry; instance().show(); },
        };
    };

    let controller = null;
    const start = options => {
        if (!controller) {
            const view = createDomView();
            controller = createController({
                request: options.request || window.fetch.bind(window),
                view,
            });
            view.bind({ onComplete: controller.complete, onClose: controller.close });
        }
        return controller.start(options);
    };
    const close = () => controller?.close();
    const revoke = () => controller?.revoke?.() ?? Promise.resolve(true);
    return { createController, start, close, revoke, terminalState };
}));
