const loginAuditState = { kind:'accounts', page:1, total:0, request:0 };

async function showLoginAudit(kind = 'accounts') {
    loginAuditState.kind = kind;
    loginAuditState.page = 1;
    if (!document.getElementById('loginAuditModal')) {
        document.body.insertAdjacentHTML('beforeend', `
            <div class="modal fade" id="loginAuditModal" tabindex="-1"><div class="modal-dialog modal-xl"><div class="modal-content">
              <div class="modal-header"><h5 class="modal-title" id="loginAuditTitle">登录记录</h5><button class="btn-close" data-bs-dismiss="modal"></button></div>
              <div class="modal-body">
                <div class="d-flex gap-2 mb-3"><input id="loginAuditKeyword" class="form-control" placeholder="搜索登录账号或闲鱼账号ID">
                  <select id="loginAuditResult" class="form-select w-auto"><option value="">全部结果</option><option value="true">成功</option><option value="false">失败</option></select>
                  <button id="loginAuditSearch" class="btn btn-outline-primary">查询</button></div>
                <p class="text-muted small">系统登录记录仅管理员可见；账号登录记录仅显示当前用户的闲鱼账号。不会展示密码、验证码、Cookie或登录凭证。</p>
                <div class="table-responsive"><table class="table"><thead><tr><th>账号</th><th>登录方式/阶段</th><th>结果</th><th>连接对端IP</th><th>时间</th></tr></thead><tbody id="loginAuditRows"></tbody></table></div>
                <div class="d-flex justify-content-between"><button id="loginAuditPrevious" class="btn btn-sm btn-outline-secondary">上一页</button><span id="loginAuditPage"></span><button id="loginAuditNext" class="btn btn-sm btn-outline-secondary">下一页</button></div>
              </div></div></div></div>`);
        document.getElementById('loginAuditSearch').onclick = () => { loginAuditState.page=1; loadLoginAudit(); };
        document.getElementById('loginAuditPrevious').onclick = () => { if(loginAuditState.page>1){loginAuditState.page--;loadLoginAudit();} };
        document.getElementById('loginAuditNext').onclick = () => { if(loginAuditState.page*50<loginAuditState.total){loginAuditState.page++;loadLoginAudit();} };
    }
    document.getElementById('loginAuditTitle').textContent = kind === 'users' ? '系统用户登录记录' : '闲鱼账号登录与刷新记录';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('loginAuditModal')).show();
    await loadLoginAudit();
}

async function loadLoginAudit() {
    const sequence = ++loginAuditState.request;
    const query = new URLSearchParams({ page:loginAuditState.page, page_size:50, keyword:document.getElementById('loginAuditKeyword').value.trim() });
    const success = document.getElementById('loginAuditResult').value;
    if (success) query.set('success',success);
    try {
        const response = await fetch(`${apiBase}/api/login-audit/${loginAuditState.kind}?${query}`, {headers:{'Authorization':`Bearer ${authToken}`}});
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || '获取登录记录失败');
        if (sequence !== loginAuditState.request) return;
        loginAuditState.total = data.total || 0;
        const methods = { username_password:'账号密码',email_password:'邮箱密码',email_code:'邮箱验证码',qr_login:'扫码登录',manual_qr_refresh:'手动扫码刷新',manual_password_refresh:'手动账密刷新',password_login:'账密登录' };
        document.getElementById('loginAuditRows').innerHTML = (data.items || []).map(row => {
            const method = methods[row.login_method || row.trigger_scene || row.event_type] || '账号登录/刷新';
            const result = row.processing_status ? ({success:'成功',failed:'失败',processing:'处理中'}[row.processing_status] || '待处理') : (row.success ? '成功' : '失败');
            return `<tr><td>${escapeHtml(String(row.login_account || row.cookie_id || ''))}</td><td>${escapeHtml(method)}</td><td>${result}</td><td>${escapeHtml(row.client_ip || '—')}</td><td>${escapeHtml(row.created_at || '')}</td></tr>`;
        }).join('') || '<tr><td colspan="5" class="text-center text-muted">暂无记录</td></tr>';
        document.getElementById('loginAuditPage').textContent = `第 ${data.page} 页，共 ${data.total} 条`;
        document.getElementById('loginAuditPrevious').disabled = loginAuditState.page <= 1;
        document.getElementById('loginAuditNext').disabled = loginAuditState.page * 50 >= loginAuditState.total;
    } catch (error) { showToast(error.message,'danger'); }
}
