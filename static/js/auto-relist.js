let autoRelistRules = [];

async function autoRelistRequest(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
        ...options, headers: { 'Authorization': `Bearer ${authToken}`, 'Content-Type': 'application/json', ...(options.headers || {}) }
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || '续售操作失败');
    return data;
}

async function showAutoRelistModal(accountId, itemId) {
    if (!document.getElementById('autoRelistModal')) {
        document.body.insertAdjacentHTML('beforeend', `
            <div class="modal fade" id="autoRelistModal" tabindex="-1">
              <div class="modal-dialog modal-xl"><div class="modal-content">
                <div class="modal-header"><h5 class="modal-title">卖出后自动续售</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                <div class="modal-body">
                  <p class="text-muted">仅处理启用后的成功发货订单；原商品不在售且卡券仍有库存时才重新发布。新商品会复制回复、过滤和发货配置，保留原订单与历史商品。</p>
                  <div class="row g-2 mb-3">
                    <div class="col-md-3"><label class="form-label">账号</label><input id="relistAccount" class="form-control" readonly></div>
                    <div class="col-md-3"><label class="form-label">原商品</label><input id="relistItem" class="form-control" readonly></div>
                    <div class="col-md-3"><label class="form-label">发布素材</label><select id="relistMaterial" class="form-select"></select></div>
                    <div class="col-md-3"><label class="form-label">发货卡券</label><select id="relistCard" class="form-select"></select></div>
                    <div class="col-md-3"><label class="form-label">成交后等待秒数</label><input id="relistDelay" class="form-control" type="number" min="0" max="86400" value="60"></div>
                    <div class="col-md-3"><label class="form-label">最低剩余卡密数量</label><input id="relistStock" class="form-control" type="number" min="1" value="1"></div>
                    <div class="col-md-3 align-self-end"><div class="form-check mb-2"><input id="relistEnabled" type="checkbox" class="form-check-input"><label for="relistEnabled" class="form-check-label">启用自动续售</label></div></div>
                    <div class="col-md-3 align-self-end"><button id="createRelistRule" class="btn btn-primary">保存新规则</button></div>
                  </div>
                  <p class="text-muted small">原商品ID的全部发货规则会复制到新商品；未配置时关联所选卡券，每次发1份。请先核对多规格的卡券和发布素材。</p>
                  <button id="refreshRelistRules" class="btn btn-sm btn-outline-primary mb-2">刷新规则与事件</button>
                  <div id="relistRulesList"></div><h6 class="mt-3">最近续售事件</h6><div id="relistEventsList"></div>
                </div>
              </div></div>
            </div>`);
        document.getElementById('createRelistRule').addEventListener('click', createAutoRelistRule);
        document.getElementById('refreshRelistRules').addEventListener('click', refreshAutoRelistRules);
    }
    document.getElementById('relistAccount').value = accountId;
    document.getElementById('relistItem').value = itemId;
    document.getElementById('relistEnabled').checked = false;
    bootstrap.Modal.getOrCreateInstance(document.getElementById('autoRelistModal')).show();
    try {
        const [materials, cards] = await Promise.all([
            autoRelistRequest('/product-materials?page_size=100'), autoRelistRequest('/cards')
        ]);
        const materialRows = materials.items || materials.list || materials.materials || [];
        const cardRows = Array.isArray(cards) ? cards : (cards.cards || []);
        document.getElementById('relistMaterial').innerHTML = materialRows.map(row => `<option value="${row.id}">#${row.id} ${escapeHtml(row.title)}</option>`).join('');
        document.getElementById('relistCard').innerHTML = cardRows.filter(row => row.enabled).map(row => `<option value="${row.id}">#${row.id} ${escapeHtml(row.name)}</option>`).join('');
        await refreshAutoRelistRules();
    } catch (error) { showToast(error.message, 'danger'); }
}

async function createAutoRelistRule() {
    try {
        await autoRelistRequest('/api/auto-relist/rules', { method:'POST', body:JSON.stringify({
            cookie_id:document.getElementById('relistAccount').value,
            item_id:document.getElementById('relistItem').value,
            material_id:Number(document.getElementById('relistMaterial').value),
            card_id:Number(document.getElementById('relistCard').value),
            enabled:document.getElementById('relistEnabled').checked,
            delay_seconds:Number(document.getElementById('relistDelay').value),
            min_stock:Number(document.getElementById('relistStock').value)
        }) });
        showToast('续售规则已保存', 'success');
        await refreshAutoRelistRules();
    } catch (error) { showToast(error.message, 'danger'); }
}

async function refreshAutoRelistRules() {
    try {
        const [rules, events] = await Promise.all([autoRelistRequest('/api/auto-relist/rules'),autoRelistRequest('/api/auto-relist/events')]);
        autoRelistRules = rules.rules || [];
        const labels = { watching:'等待成交', paused:'条件未满足', unknown:'发布结果待确认', migration_retry:'等待配置迁移', publishing:'发布中', completed:'已完成', published:'已发布，待迁移' };
        document.getElementById('relistRulesList').innerHTML = autoRelistRules.map(row => `
            <div class="border rounded p-2 mb-2">#${row.id} ${escapeHtml(row.cookie_id)} / ${escapeHtml(row.current_item_id)}
              <span class="badge ${row.enabled ? 'bg-success' : 'bg-secondary'}">${row.enabled ? '已启用' : '已停用'}</span>
              <span>${escapeHtml(labels[row.status] || row.status)}</span>
              <button class="btn btn-sm btn-outline-secondary" data-relist-toggle="${row.id}">${row.enabled ? '停用' : '启用'}</button>
              <button class="btn btn-sm btn-outline-primary" data-relist-check="${row.id}">立即检查</button>
              ${!row.enabled ? `<button class="btn btn-sm btn-outline-danger" data-relist-delete="${row.id}">删除规则</button>` : ''}
              <div class="text-muted small">${escapeHtml(row.last_error || '')}</div>
            </div>`).join('') || '<p class="text-muted">暂无续售规则</p>';
        document.getElementById('relistEventsList').innerHTML = (events.events || []).map(row => `
            <div class="border-bottom py-2">#${row.id} ${escapeHtml(row.old_item_id)} → ${escapeHtml(row.new_item_id || '待确认')}
              <span>${escapeHtml(labels[row.state] || row.state)}</span>
              <div class="text-muted small">${escapeHtml(row.error || '')}</div>
              ${row.state === 'unknown' ? `<input class="form-control form-control-sm my-1" data-relist-result="${row.id}" placeholder="核对发布记录后填写新商品ID"><button class="btn btn-sm btn-outline-primary" data-relist-associate="${row.id}">确认关联已发布商品</button>` : ''}
            </div>`).join('') || '<p class="text-muted">暂无续售事件</p>';
        document.querySelectorAll('[data-relist-toggle]').forEach(button => button.onclick = async () => {
            const rule = autoRelistRules.find(row => row.id === Number(button.dataset.relistToggle));
            await performRelistAction(`/api/auto-relist/rules/${rule.id}`, 'PUT', { enabled:!rule.enabled,delay_seconds:rule.delay_seconds,min_stock:rule.min_stock });
        });
        document.querySelectorAll('[data-relist-check]').forEach(button => button.onclick = () => performRelistAction(`/api/auto-relist/rules/${button.dataset.relistCheck}/check`, 'POST'));
        document.querySelectorAll('[data-relist-delete]').forEach(button => button.onclick = () => performRelistAction(`/api/auto-relist/rules/${button.dataset.relistDelete}`, 'DELETE'));
        document.querySelectorAll('[data-relist-associate]').forEach(button => button.onclick = () => performRelistAction(
            `/api/auto-relist/events/${button.dataset.relistAssociate}/associate`, 'POST',
            {item_id:document.querySelector(`[data-relist-result="${button.dataset.relistAssociate}"]`).value.trim()}
        ));
    } catch (error) { showToast(error.message, 'danger'); }
}

async function performRelistAction(path, method, body) {
    try {
        const result = await autoRelistRequest(path, { method, ...(body ? { body:JSON.stringify(body) } : {}) });
        showToast(result.success === false ? '操作尚未完成，请查看事件详情' : '续售操作已完成', result.success === false ? 'warning' : 'success');
        await refreshAutoRelistRules();
    } catch (error) { showToast(error.message, 'danger'); }
}
