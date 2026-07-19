
// ================================
// 全局变量和配置
// ================================
const apiBase = location.origin;
let keywordsData = {};
let currentCookieId = '';
let editCookieId = '';
let authToken = localStorage.getItem('auth_token');
let dashboardData = {
    accounts: [],
    totalKeywords: 0,
    totalItems: 0
};
let pendingAccountManagementFocusId = '';
let aboutDiagnosticsAccounts = [];
let aboutDiagnosticsInitialized = false;
let dashboardRuntimeRetryTimer = null;
let aboutRuntimeRetryTimer = null;
let lastDashboardRuntimeRetryAt = 0;
let lastAboutRuntimeRetryAt = 0;
const DASHBOARD_ANNOUNCEMENT_DISMISS_PREFIX = 'dashboard_announcement_dismissed_';
let dashboardAnnouncementState = {
    current: null,
    history: []
};

// 账号关键词缓存
let accountKeywordCache = {};
let cacheTimestamp = 0;
const CACHE_DURATION = 30000; // 30秒缓存

// 商品列表搜索和分页相关变量
let allItemsData = []; // 存储所有商品数据
let filteredItemsData = []; // 存储过滤后的商品数据
let currentItemsPage = 1; // 当前页码
let itemsPerPage = 20; // 每页显示数量
let totalItemsPages = 0; // 总页数
let currentSearchKeyword = ''; // 当前搜索关键词
let itemPublishPreviewUrls = [];
let itemPublishInitialized = false;
let itemPublishSubmitting = false;
let itemPublishSavingMaterial = false;
let itemPublishLoadedMaterialId = null;
let itemPublishLoadedMaterialImages = [];
let itemPublishMaterials = [];
let itemPublishLogs = [];

// 订单列表搜索和分页相关变量
let allOrdersData = []; // 存储所有订单数据
let filteredOrdersData = []; // 存储过滤后的订单数据
let currentOrdersPage = 1; // 当前页码
let ordersPerPage = 20; // 每页显示数量
let totalOrdersPages = 0; // 总页数
let currentOrderSearchKeyword = ''; // 当前搜索关键词
let ordersStreamAbortController = null;
let ordersStreamReconnectTimer = null;
let ordersStreamRetryCount = 0;
let ordersStreamShouldRun = false;
let pendingOrderLocator = null;
let orderHistorySyncModalInstance = null;
let orderHistorySyncPollingTimer = null;
let activeOrderHistorySyncJobId = '';
let orderHistorySyncNotifiedJobId = '';
let orderHistorySyncAccounts = [];
let blacklistState = {
    page: 1,
    pageSize: 20,
    total: 0,
    accountsLoaded: false
};
let messageFilterState = {
    page: 1,
    pageSize: 20,
    total: 0,
    accountsLoaded: false,
    editingId: null
};
let loadingRequestCount = 0;
let loadingShowTimer = null;
const LOADING_SHOW_DELAY = 120;

// ================================
// 通用功能 - 菜单切换和导航
// ================================
function showSection(sectionName) {
    console.log('切换到页面:', sectionName); // 调试信息

    // 获取并校验目标内容区域
    const targetSection = document.getElementById(sectionName + '-section');
    if (!targetSection) {
        console.error('找不到页面元素:', sectionName + '-section'); // 调试信息
        return;
    }

    // 如果已经是当前页面，避免重复切换导致闪烁
    if (targetSection.classList.contains('active')) {
        return;
    }

    // 仅切换当前激活页面和目标页面，避免“先全关再全开”造成白闪
    const currentActiveSection = document.querySelector('.content-section.active');
    if (currentActiveSection) {
        currentActiveSection.classList.remove('active');
    }

    targetSection.classList.add('active');
    console.log('页面已激活:', sectionName + '-section'); // 调试信息

    // 仅处理侧边栏菜单 active，避免影响内容区域 tab 的 .nav-link
    document.querySelectorAll('#sidebar .sidebar-nav .nav-link').forEach(link => {
        link.classList.remove('active');
    });

    const activeMenuLink = document.querySelector(`#sidebar .nav-item[data-menu-id="${sectionName}"] .nav-link`);
    if (activeMenuLink) {
        activeMenuLink.classList.add('active');
    }

    // 根据不同section加载对应数据
    switch(sectionName) {
    case 'dashboard':        // 【仪表盘菜单】
        loadDashboard();
        break;
    case 'accounts':         // 【账号管理菜单】
        loadCookies();
        break;
    case 'item-publish':    // 【商品发布菜单】
        loadItemPublish();
        break;
    case 'items':           // 【商品管理菜单】
        loadItems();
        initItemsSearch(); // 确保搜索功能已初始化
        break;
    case 'items-reply':           // 【商品回复管理菜单】
        loadItemsReplay();
        break;
    case 'orders':          // 【订单管理菜单】
        loadOrders();
        break;
    case 'auto-reply':      // 【自动回复菜单】
        refreshAccountList();
        break;
    case 'message-filters': // 【消息过滤菜单】
        loadMessageFiltersPage();
        break;
    case 'cards':           // 【卡券管理菜单】
        loadCards();
        break;
    case 'auto-delivery':   // 【自动发货菜单】
        loadDeliveryRules();
        break;
    case 'notification-channels':  // 【通知渠道菜单】
        loadNotificationChannels();
        break;
    case 'message-notifications':  // 【消息通知菜单】
        loadMessageNotifications();
        loadNotificationTemplates();
        break;
    case 'system-settings':    // 【系统设置菜单】
        loadSystemSettings();
        initMenuManagement();
        break;
    case 'logs':            // 【日志管理菜单】
        // 自动加载系统日志
        setTimeout(() => {
            // 检查是否在正确的页面并且元素存在
            const systemLogContainer = document.getElementById('systemLogContainer');
            if (systemLogContainer) {
                console.log('首次进入日志页面，自动加载日志...');
                loadSystemLogs();
            }
        }, 100);
        break;
    case 'risk-control-logs': // 【风控日志菜单】
        // 自动加载风控日志
        setTimeout(() => {
            const riskLogContainer = document.getElementById('riskLogContainer');
            if (riskLogContainer) {
                console.log('首次进入风控日志页面，自动加载日志...');
                loadRiskControlLogs();
                loadCookieFilterOptions();
            }
        }, 100);
        break;
    case 'user-management':  // 【用户管理菜单】
        loadUserManagement();
        break;
    case 'online-im':        // 【在线客服菜单】
        loadOnlineIm();
        break;
    case 'blacklist':        // 【黑名单管理菜单】
        loadBlacklistPage();
        break;
    case 'data-management':  // 【数据管理菜单】
        loadDataManagement();
        break;
    }

    if (sectionName !== 'orders') {
        stopOrdersStream();
    }

    if (sectionName !== 'online-im') {
        stopChatStream();
    }

    // 如果切换到非日志页面，停止自动刷新
    if (sectionName !== 'logs' && window.autoRefreshInterval) {
    clearInterval(window.autoRefreshInterval);
    window.autoRefreshInterval = null;
    const button = document.querySelector('#autoRefreshText');
    const icon = button?.previousElementSibling;
    if (button) {
        button.textContent = '开启自动刷新';
        if (icon) icon.className = 'bi bi-play-circle me-1';
    }
    }

    if (sectionName !== 'dashboard' && dashboardRuntimeRetryTimer) {
        clearTimeout(dashboardRuntimeRetryTimer);
        dashboardRuntimeRetryTimer = null;
    }

    if (sectionName !== 'accounts' && aboutRuntimeRetryTimer) {
        clearTimeout(aboutRuntimeRetryTimer);
        aboutRuntimeRetryTimer = null;
    }
}

function getAuthToken() {
    authToken = localStorage.getItem('auth_token');
    return authToken || '';
}

async function authenticatedFetch(input, options = {}) {
    const token = getAuthToken();
    const headers = { ...(options.headers || {}) };
    delete headers.Authorization;
    delete headers.authorization;
    if (token) {
        headers.Authorization = `Bearer ${token}`;
    }
    const response = await fetch(input, { ...options, headers });
    if (response.status === 401) {
        localStorage.removeItem('auth_token');
        authToken = '';
        if (typeof clearQRCodeCheck === 'function') {
            clearQRCodeCheck();
        }
        if (location.pathname !== '/') {
            location.href = '/';
        }
    }
    return response;
}

// 移动端侧边栏切换
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('show');
}

// 侧边栏折叠切换
function toggleSidebarCollapse() {
    const sidebar = document.getElementById('sidebar');
    const body = document.body;
    sidebar.classList.toggle('collapsed');
    body.classList.toggle('sidebar-collapsed');
    // 保存状态到 localStorage
    localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
}

// 初始化侧边栏折叠状态
function initSidebarCollapse() {
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    if (isCollapsed) {
        const sidebar = document.getElementById('sidebar');
        const body = document.body;
        if (sidebar) {
            sidebar.classList.add('collapsed');
            body.classList.add('sidebar-collapsed');
        }
    }
}

// ================================
// 暗色模式功能
// ================================

// 检测系统是否为暗色模式
function isSystemDarkMode() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

// 更新主题图标
function updateDarkModeIcon(mode) {
    const icon = document.getElementById('darkModeIcon');
    if (!icon) return;

    // 清除所有可能的图标类
    icon.classList.remove('bi-moon-fill', 'bi-sun-fill', 'bi-circle-half');

    if (mode === 'auto') {
        icon.classList.add('bi-circle-half');
    } else if (mode === 'dark') {
        icon.classList.add('bi-sun-fill');
    } else {
        icon.classList.add('bi-moon-fill');
    }
}

// 应用主题
function applyDarkMode(mode) {
    const html = document.documentElement;
    let shouldBeDark = false;

    if (mode === 'auto') {
        shouldBeDark = isSystemDarkMode();
    } else if (mode === 'dark') {
        shouldBeDark = true;
    }

    if (shouldBeDark) {
        html.setAttribute('data-theme', 'dark');
    } else {
        html.removeAttribute('data-theme');
    }

    updateDarkModeIcon(mode);
}

// 切换暗色模式（三态切换：light → dark → auto）
function toggleDarkMode() {
    const currentMode = localStorage.getItem('darkMode') || 'light';
    let nextMode;

    if (currentMode === 'light') {
        nextMode = 'dark';
    } else if (currentMode === 'dark') {
        nextMode = 'auto';
    } else {
        nextMode = 'light';
    }

    localStorage.setItem('darkMode', nextMode);
    applyDarkMode(nextMode);

    // 显示提示
    const modeNames = {
        'light': '浅色模式',
        'dark': '深色模式',
        'auto': '跟随系统'
    };
    showToast(`已切换至${modeNames[nextMode]}`, 'info');
}

// 初始化暗色模式
function initDarkMode() {
    const savedMode = localStorage.getItem('darkMode') || 'light';
    applyDarkMode(savedMode);

    // 监听系统主题变化
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            const currentMode = localStorage.getItem('darkMode') || 'light';
            if (currentMode === 'auto') {
                applyDarkMode('auto');
            }
        });
    }
}

// ================================
// 【仪表盘菜单】相关功能
// ================================

async function fetchDashboardResource(path, fallbackValue) {
    try {
        const response = await fetch(`${apiBase}${path}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            return fallbackValue;
        }

        return await response.json();
    } catch (error) {
        console.error(`加载仪表盘资源失败: ${path}`, error);
        return fallbackValue;
    }
}

async function enrichDashboardAccounts(accounts) {
    const scheduledTaskData = await fetchDashboardResource('/scheduled-tasks', { success: false, tasks: [] });
    const scheduledTasks = scheduledTaskData && scheduledTaskData.success ? (scheduledTaskData.tasks || []) : [];

    return Promise.all(accounts.map(async (account) => {
        const [keywordsData, defaultReplyData, aiReplyData] = await Promise.all([
            fetchDashboardResource(`/keywords/${encodeURIComponent(account.id)}`, []),
            fetchDashboardResource(`/default-replies/${encodeURIComponent(account.id)}`, { enabled: false, reply_content: '' }),
            fetchDashboardResource(`/ai-reply-settings/${encodeURIComponent(account.id)}`, { ai_enabled: false, model_name: 'qwen-plus' })
        ]);

        return {
            ...account,
            keywords: Array.isArray(keywordsData) ? keywordsData : [],
            keywordCount: Array.isArray(keywordsData) ? keywordsData.length : 0,
            defaultReply: defaultReplyData || { enabled: false, reply_content: '' },
            aiReply: aiReplyData || { ai_enabled: false, model_name: 'qwen-plus' },
            polishSchedule: getPolishScheduledTask(scheduledTasks, account.id)
        };
    }));
}

function getDashboardAnnouncementDismissKey(id) {
    return `${DASHBOARD_ANNOUNCEMENT_DISMISS_PREFIX}${String(id || '').trim()}`;
}

function normalizeDashboardAnnouncementState(payload) {
    return {
        current: payload?.current || null,
        history: Array.isArray(payload?.history) ? payload.history : []
    };
}

function isDashboardAnnouncementDismissed(announcement) {
    const announcementId = String(announcement?.id || '').trim();
    if (!announcementId) {
        return false;
    }
    return localStorage.getItem(getDashboardAnnouncementDismissKey(announcementId)) === 'true';
}

function dismissDashboardAnnouncement(announcement) {
    const announcementId = String(announcement?.id || '').trim();
    if (announcementId) {
        localStorage.setItem(getDashboardAnnouncementDismissKey(announcementId), 'true');
    }
    renderDashboardAnnouncement();
}

function handleDashboardAnnouncementAction(announcement) {
    const actionType = String(announcement?.action_type || '').trim().toLowerCase();
    if (!actionType) {
        return;
    }

    if (actionType === 'changelog') {
        showChangelogModal();
        return;
    }

    if (actionType === 'update') {
        performHotUpdate();
        return;
    }

    if (actionType === 'url') {
        const targetUrl = String(announcement?.action_url || '').trim();
        if (targetUrl) {
            window.open(targetUrl, '_blank', 'noopener,noreferrer');
        }
    }
}

function getDashboardAnnouncementLevelText(level) {
    const normalizedLevel = String(level || '').trim().toLowerCase();
    if (normalizedLevel === 'success') return '成功';
    if (normalizedLevel === 'warning') return '提醒';
    if (normalizedLevel === 'danger') return '重要';
    return '公告';
}

function getDashboardAnnouncementStatusText(status) {
    const normalizedStatus = String(status || '').trim().toLowerCase();
    if (normalizedStatus === 'active') return '当前生效';
    if (normalizedStatus === 'scheduled') return '尚未生效';
    if (normalizedStatus === 'expired') return '已结束';
    if (normalizedStatus === 'disabled') return '未启用';
    return '历史记录';
}

function getDashboardAnnouncementDisplayTime(announcement) {
    const timeValue = String(
        announcement?.published_at
        || announcement?.start_at
        || announcement?.end_at
        || ''
    ).trim();
    if (!timeValue) {
        return '未设置时间';
    }
    return formatDateTime(timeValue);
}

function showDashboardAnnouncementHistoryModal() {
    const history = Array.isArray(dashboardAnnouncementState.history) ? dashboardAnnouncementState.history : [];
    if (!history.length) {
        showToast('暂无公告记录', 'info');
        return;
    }

    const modalId = 'dashboardAnnouncementHistoryModal';
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }

    const historyHtml = history.map((announcement, index) => {
        const level = ['info', 'success', 'warning', 'danger'].includes(String(announcement?.level || '').trim().toLowerCase())
            ? String(announcement.level || '').trim().toLowerCase()
            : 'info';
        const status = String(announcement?.status || '').trim().toLowerCase() || 'disabled';
        const title = String(announcement?.title || '').trim() || '未命名公告';
        const message = String(announcement?.message || '').trim() || '暂无内容';
        const actionText = String(announcement?.action_type ? (announcement?.action_text || '') : '').trim();
        const timeText = getDashboardAnnouncementDisplayTime(announcement);
        const currentBadge = announcement?.is_current
            ? '<span class="dashboard-announcement-history-badge is-current">当前</span>'
            : '';

        return `
            <article class="dashboard-announcement-history-item ${announcement?.is_current ? 'is-current' : ''}">
                <div class="dashboard-announcement-history-head">
                    <div class="dashboard-announcement-history-meta">
                        <div class="dashboard-announcement-history-title-row">
                            <h6 class="dashboard-announcement-history-title mb-0">${escapeHtml(title)}</h6>
                            ${currentBadge}
                            <span class="dashboard-announcement-history-badge is-${level}">${escapeHtml(getDashboardAnnouncementLevelText(level))}</span>
                            <span class="dashboard-announcement-history-badge is-status">${escapeHtml(getDashboardAnnouncementStatusText(status))}</span>
                        </div>
                        <div class="dashboard-announcement-history-time">
                            <i class="bi bi-clock-history"></i>
                            <span>${escapeHtml(timeText)}</span>
                        </div>
                    </div>
                    ${actionText ? `
                        <button
                            type="button"
                            class="btn btn-sm dashboard-announcement-history-action"
                            data-announcement-history-action-index="${index}"
                        >
                            ${escapeHtml(actionText)}
                        </button>
                    ` : ''}
                </div>
                <div class="dashboard-announcement-history-message">${escapeHtml(message)}</div>
            </article>
        `;
    }).join('');

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="${modalId}" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-lg modal-dialog-scrollable">
                <div class="modal-content dashboard-announcement-history-modal">
                    <div class="modal-header dashboard-announcement-history-modal-header">
                        <div>
                            <h5 class="modal-title mb-1">
                                <i class="bi bi-megaphone-fill me-2"></i>公告记录
                            </h5>
                            <div class="dashboard-announcement-history-modal-subtitle">按发布时间倒序展示近期公告内容</div>
                        </div>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
                    </div>
                    <div class="modal-body dashboard-announcement-history-modal-body">
                        <div class="dashboard-announcement-history-list">
                            ${historyHtml}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modalElement = document.getElementById(modalId);
    if (!modalElement) {
        return;
    }

    modalElement.querySelectorAll('[data-announcement-history-action-index]').forEach(button => {
        button.addEventListener('click', () => {
            const index = Number(button.getAttribute('data-announcement-history-action-index'));
            const announcement = Number.isFinite(index) ? history[index] : null;
            if (!announcement) {
                return;
            }
            const modalInstance = bootstrap.Modal.getInstance(modalElement);
            if (modalInstance) {
                modalInstance.hide();
            }
            setTimeout(() => {
                handleDashboardAnnouncementAction(announcement);
            }, 120);
        });
    });

    modalElement.addEventListener('hidden.bs.modal', () => {
        modalElement.remove();
    }, { once: true });

    const modal = new bootstrap.Modal(modalElement);
    modal.show();
}

function renderDashboardAnnouncement() {
    const slot = document.getElementById('dashboardAnnouncementSlot');
    if (!slot) return;

    const currentAnnouncement = dashboardAnnouncementState.current;
    if (!currentAnnouncement || isDashboardAnnouncementDismissed(currentAnnouncement)) {
        slot.style.display = 'none';
        slot.innerHTML = '';
        return;
    }

    const level = ['info', 'success', 'warning', 'danger'].includes(String(currentAnnouncement.level || '').trim().toLowerCase())
        ? String(currentAnnouncement.level || '').trim().toLowerCase()
        : 'info';
    const title = String(currentAnnouncement.title || '').trim();
    const message = String(currentAnnouncement.message || '').trim();
    const summary = String(currentAnnouncement.summary || currentAnnouncement.brief || currentAnnouncement.short_message || '').trim();
    const displayMessage = summary || message;
    const actionText = String(currentAnnouncement.action_type ? (currentAnnouncement.action_text || '') : '').trim();
    const dismissible = currentAnnouncement.dismissible !== false;

    slot.style.display = '';
    slot.innerHTML = `
        <div class="dashboard-announcement-card is-${level}" role="status" aria-live="polite">
            <button
                type="button"
                class="dashboard-announcement-main"
                id="dashboardAnnouncementOpenBtn"
                title="点击查看公告记录"
                aria-label="查看公告记录"
            >
                <span class="dashboard-announcement-icon">
                    <i class="bi bi-megaphone-fill"></i>
                </span>
                <span class="dashboard-announcement-body">
                    ${title ? `<span class="dashboard-announcement-title">${escapeHtml(title)}</span>` : ''}
                    ${displayMessage ? `<span class="dashboard-announcement-message">${escapeHtml(displayMessage)}</span>` : ''}
                </span>
            </button>
            <div class="dashboard-announcement-actions">
                ${actionText ? `<button type="button" class="btn btn-sm dashboard-announcement-action" id="dashboardAnnouncementActionBtn">${escapeHtml(actionText)}</button>` : ''}
                ${dismissible ? `
                    <button type="button" class="btn btn-sm dashboard-announcement-close" id="dashboardAnnouncementCloseBtn" aria-label="关闭公告">
                        <i class="bi bi-x-lg"></i>
                    </button>
                ` : ''}
            </div>
        </div>
    `;

    const openButton = document.getElementById('dashboardAnnouncementOpenBtn');
    if (openButton) {
        openButton.onclick = () => showDashboardAnnouncementHistoryModal();
    }

    const actionButton = document.getElementById('dashboardAnnouncementActionBtn');
    if (actionButton) {
        actionButton.onclick = () => handleDashboardAnnouncementAction(currentAnnouncement);
    }

    const closeButton = document.getElementById('dashboardAnnouncementCloseBtn');
    if (closeButton) {
        closeButton.onclick = () => dismissDashboardAnnouncement(currentAnnouncement);
    }
}

async function loadDashboardAnnouncement() {
    const result = await fetchDashboardResource('/api/announcement', { success: false, current: null, history: [] });
    dashboardAnnouncementState = normalizeDashboardAnnouncementState(result?.success ? result : null);
    renderDashboardAnnouncement();
}

function renderDashboardSummaryCard(label, value, tone = 'primary', details = []) {
    const detailMarkup = Array.isArray(details) && details.length ? `
        <div class="dashboard-account-summary-details">
            ${details.map(([detailLabel, detailValue]) => `
                <span class="dashboard-account-summary-detail">
                    <span class="dashboard-account-summary-detail-label">${escapeHtml(detailLabel)}</span>
                    <span class="dashboard-account-summary-detail-value">${escapeHtml(detailValue)}</span>
                </span>
            `).join('')}
        </div>
    ` : '';

    return `
        <div class="dashboard-account-summary-item is-${tone}">
            <div class="dashboard-account-summary-main">
                <div class="dashboard-account-summary-label">${escapeHtml(label)}</div>
            </div>
            <div class="dashboard-account-summary-side">
                <div class="dashboard-account-summary-value">${escapeHtml(value)}</div>
                ${detailMarkup}
            </div>
        </div>
    `;
}

function renderDashboardAccountMetric(label, value, tone = 'off') {
    return `
        <div class="dashboard-account-metric is-${tone}">
            <div class="dashboard-account-metric-label">${escapeHtml(label)}</div>
            <div class="dashboard-account-metric-value">${escapeHtml(value)}</div>
        </div>
    `;
}

function getConnectorRuntimeState(runtimeStatus) {
    const state = runtimeStatus?.connector_state
        ?? runtimeStatus?.runtime_state
        ?? runtimeStatus?.state
        ?? '';
    return String(state).trim().toLowerCase();
}

function isConnectorManualVerificationRequired(runtimeStatus) {
    const states = [
        runtimeStatus?.connector_state,
        runtimeStatus?.runtime_state,
        runtimeStatus?.state,
    ].map(value => String(value || '').trim().toLowerCase());
    if (states.includes('manual_verification_required')) {
        return true;
    }
    const reasonCodes = [
        runtimeStatus?.connector_reason_code,
        runtimeStatus?.reason_code,
    ].map(value => String(value || '').trim().toLowerCase());
    const manualReasonCodes = new Set([
        'risk_challenge',
        'session_expired',
        'cookie_missing',
        'manual_verification_required',
    ]);
    return reasonCodes.some(code => manualReasonCodes.has(code));
}

function getConnectorRuntimeLabel(runtimeStatus) {
    const connectorState = getConnectorRuntimeState(runtimeStatus);
    if (isConnectorManualVerificationRequired(runtimeStatus)) {
        return '需重新认证';
    }
    const labels = {
        online: '在线',
        connecting: '连接中',
        authenticating: '认证中',
        recovering: '恢复中',
        degraded: '连接异常',
        offline: '离线',
        paused: '已暂停',
        failed: '失败',
    };
    return labels[connectorState] || connectorState;
}

function isQrManualVerificationHandoffAllowed(runtimeStatus, qrStartedAt = 0, options = {}) {
    const states = [
        runtimeStatus?.connector_state,
        runtimeStatus?.runtime_state,
        runtimeStatus?.state,
    ].map(value => String(value || '').trim().toLowerCase());
    const reasons = [
        runtimeStatus?.connector_reason_code,
        runtimeStatus?.reason_code,
    ].map(value => String(value || '').trim().toLowerCase());
    const enteredAt = Date.parse(String(runtimeStatus?.entered_at || '')) / 1000;
    const stateIsCurrent = options.allowStaleEnteredAt || !qrStartedAt
        || (Number.isFinite(enteredAt) && enteredAt >= Number(qrStartedAt) - 1);
    return stateIsCurrent
        && states.includes('manual_verification_required')
        && reasons.some(reason => ['risk_challenge', 'manual_verification_required'].includes(reason));
}

function isRuntimeStatusHealthy(runtimeStatus) {
    if (isConnectorManualVerificationRequired(runtimeStatus)) {
        return false;
    }
    return Boolean(
        runtimeStatus?.running
        && runtimeStatus.ws_ready
        && runtimeStatus.session_ready
        && runtimeStatus.has_current_token
        && runtimeStatus.message_stream_ready
    );
}

function getRuntimeStatusRecentAnchor(runtimeStatus) {
    const normalizedRuntimeStatus = runtimeStatus || {};
    const timestampKeys = [
        'state_last_changed_at',
        'last_successful_connection_at',
        'last_heartbeat_response_at',
        'session_keepalive_at',
        'token_last_refreshed_at',
        'last_message_received_at',
    ];

    const timestamps = timestampKeys
        .map(key => Number(normalizedRuntimeStatus[key] || 0))
        .filter(value => Number.isFinite(value) && value > 0);

    return timestamps.length ? Math.max(...timestamps) : 0;
}

function shouldAutoRetryRuntimeStatus(runtimeStatus) {
    if (isConnectorManualVerificationRequired(runtimeStatus)) {
        return false;
    }
    if (!runtimeStatus?.running) {
        return false;
    }

    const connectionState = String(runtimeStatus.connection_state || '').trim();
    if (connectionState === 'connecting' || connectionState === 'reconnecting') {
        return true;
    }

    if (isRuntimeStatusHealthy(runtimeStatus)) {
        return false;
    }

    const recentAnchor = getRuntimeStatusRecentAnchor(runtimeStatus);
    if (!recentAnchor) {
        return false;
    }

    return ((Date.now() / 1000) - recentAnchor) <= 90;
}

function getMessageStreamRuntimeDisplay(runtimeStatus) {
    const normalizedRuntimeStatus = runtimeStatus || {};
    const explicitStatus = String(normalizedRuntimeStatus.message_stream_status || '').trim();
    const explicitNote = String(normalizedRuntimeStatus.message_stream_note || '').trim();
    const connectionState = String(normalizedRuntimeStatus.connection_state || '').trim();

    let status = explicitStatus;
    if (!status) {
        if (!normalizedRuntimeStatus.running) {
            status = 'not_running';
        } else if (connectionState === 'connecting' || connectionState === 'reconnecting') {
            status = 'recovering';
        } else if (connectionState !== 'connected' || normalizedRuntimeStatus.ws_ready === false) {
            status = 'connection_unready';
        } else if (normalizedRuntimeStatus.message_stream_ready) {
            status = 'watching';
        } else {
            status = 'connection_unready';
        }
    }

    let note = explicitNote;
    if (!note) {
        if (!normalizedRuntimeStatus.running) {
            note = '账号实例未启动，业务消息流尚未建立';
        } else if (status === 'recovering') {
            note = '连接正在恢复，业务消息流状态将在重连稳定后更新';
        } else if (status === 'connection_unready') {
            note = '连接未就绪，业务消息流状态待 WebSocket 恢复后更新';
        } else if (status === 'watching') {
            note = '当前连接尚未收到非心跳业务包';
        } else {
            note = '业务消息流状态等待更多运行时数据';
        }
    }

    return { status, note };
}

function scheduleDashboardRuntimeAutoRetry(accounts) {
    if (dashboardRuntimeRetryTimer) {
        clearTimeout(dashboardRuntimeRetryTimer);
        dashboardRuntimeRetryTimer = null;
    }

    if (!document.getElementById('dashboard-section')?.classList.contains('active')) {
        return;
    }

    if (!Array.isArray(accounts) || !accounts.some(account => shouldAutoRetryRuntimeStatus(account.runtime_status))) {
        return;
    }

    if (Date.now() - lastDashboardRuntimeRetryAt < 15000) {
        return;
    }

    const hasTransientState = accounts.some(account => {
        const connectionState = String(account?.runtime_status?.connection_state || '').trim();
        return connectionState === 'connecting' || connectionState === 'reconnecting';
    });
    const delay = hasTransientState ? 3500 : 5000;

    dashboardRuntimeRetryTimer = setTimeout(() => {
        dashboardRuntimeRetryTimer = null;
        if (!document.getElementById('dashboard-section')?.classList.contains('active')) {
            return;
        }
        lastDashboardRuntimeRetryAt = Date.now();
        refreshDashboardRuntimeSnapshots();
    }, delay);
}

function scheduleAboutRuntimeAutoRetry(accountId, runtimeStatus) {
    if (aboutRuntimeRetryTimer) {
        clearTimeout(aboutRuntimeRetryTimer);
        aboutRuntimeRetryTimer = null;
    }

    const normalizedAccountId = String(accountId || '').trim();
    if (!normalizedAccountId) {
        return;
    }

    if (!document.getElementById('accounts-section')?.classList.contains('active')) {
        return;
    }

    if (!shouldAutoRetryRuntimeStatus(runtimeStatus)) {
        return;
    }

    if (Date.now() - lastAboutRuntimeRetryAt < 12000) {
        return;
    }

    const connectionState = String(runtimeStatus?.connection_state || '').trim();
    const delay = (connectionState === 'connecting' || connectionState === 'reconnecting') ? 3000 : 5000;

    aboutRuntimeRetryTimer = setTimeout(() => {
        aboutRuntimeRetryTimer = null;
        if (!document.getElementById('accounts-section')?.classList.contains('active')) {
            return;
        }
        if (getAboutSelectedAccountId() !== normalizedAccountId) {
            return;
        }
        lastAboutRuntimeRetryAt = Date.now();
        loadAboutRuntimeStatus(normalizedAccountId);
    }, delay);
}

function renderDashboardAccountRuntimeSnapshot(runtimeStatus) {
    const normalizedRuntimeStatus = runtimeStatus || {};
    const connectorNeedsManual = isConnectorManualVerificationRequired(normalizedRuntimeStatus);
    const connectionState = normalizedRuntimeStatus.connection_state || 'not_running';
    const keepaliveDisplayStatus = normalizedRuntimeStatus.session_keepalive_display_status || normalizedRuntimeStatus.session_keepalive_status || '';
    const tokenStatus = normalizedRuntimeStatus.token_refresh_status || '';
    const messageStreamDisplay = getMessageStreamRuntimeDisplay(normalizedRuntimeStatus);
    const messageStreamStatus = messageStreamDisplay.status;

    const connectionText = connectorNeedsManual
        ? '需重新认证'
        : (getAboutStatusText('connection', connectionState) || '未运行');
    const connectionTone = connectorNeedsManual
        ? 'danger'
        : getAboutStatusVariant('connection', connectionState);
    const keepaliveText = keepaliveDisplayStatus
        ? (getAboutStatusText('keepalive', keepaliveDisplayStatus) || keepaliveDisplayStatus)
        : (normalizedRuntimeStatus.running ? '未执行' : '未运行');
    const keepaliveTone = keepaliveDisplayStatus
        ? getAboutStatusVariant('keepalive', keepaliveDisplayStatus)
        : 'secondary';
    const tokenText = tokenStatus
        ? (getAboutStatusText('token', tokenStatus) || tokenStatus)
        : (normalizedRuntimeStatus.running ? '未刷新' : '未运行');
    const tokenTone = tokenStatus
        ? getAboutStatusVariant('token', tokenStatus)
        : 'secondary';
    const messageStreamText = messageStreamStatus
        ? (getAboutStatusText('stream', messageStreamStatus) || messageStreamStatus)
        : (normalizedRuntimeStatus.running ? '观察中' : '未运行');
    const messageStreamTone = messageStreamStatus
        ? getAboutStatusVariant('stream', messageStreamStatus)
        : 'secondary';
    const runningHealthy = isRuntimeStatusHealthy(normalizedRuntimeStatus);
    const summaryText = connectorNeedsManual
        ? '需重新认证'
        : !normalizedRuntimeStatus.running
        ? '未运行'
        : (runningHealthy ? '运行正常' : '部分异常');
    const summaryTone = connectorNeedsManual
        ? 'danger'
        : !normalizedRuntimeStatus.running
        ? 'secondary'
        : (runningHealthy ? 'success' : 'warning');
    const items = [
        { label: '连接', text: connectionText, tone: connectionTone },
        { label: '保活', text: keepaliveText, tone: keepaliveTone },
        { label: 'Token', text: tokenText, tone: tokenTone },
        { label: '消息流', text: messageStreamText, tone: messageStreamTone }
    ];

    return `
        <div class="dashboard-account-runtime" aria-label="账号运行态快照">
            <div class="dashboard-account-runtime-summary is-${summaryTone}">
                <span class="dashboard-account-runtime-summary-dot" aria-hidden="true"></span>
                <span class="dashboard-account-runtime-summary-text">${escapeHtml(summaryText)}</span>
            </div>
            <div class="dashboard-account-runtime-signals">
                ${items.map(item => {
                    const detailText = `${item.label}: ${item.text}`;
                    return `
                        <span class="dashboard-account-runtime-signal is-${item.tone}" title="${escapeHtml(detailText)}" aria-label="${escapeHtml(detailText)}">
                            <span class="dashboard-account-runtime-signal-dot" aria-hidden="true"></span>
                            <span class="dashboard-account-runtime-signal-label">${escapeHtml(item.label)}</span>
                        </span>
                    `;
                }).join('')}
            </div>
        </div>
    `;
}

function renderStatusNoteBadge(statusNote, className) {
    const noteText = String(statusNote || '').trim();
    if (!noteText) {
        return '';
    }
    const safeClassName = className || 'account-status-note-badge';
    return `
        <span class="${safeClassName}" title="${escapeHtml(noteText)}">
            <i class="bi bi-shield-exclamation"></i>
            ${escapeHtml(noteText)}
        </span>
    `;
}

function getManualInterventionAlert(statusNote, runtimeStatus, accountId = '') {
    const noteText = String(statusNote || '').trim();
    const connectorNeedsManual = isConnectorManualVerificationRequired(runtimeStatus);
    const connectorReason = String(runtimeStatus?.connector_reason_message || '').trim();
    const tokenStatus = String(runtimeStatus?.token_refresh_status || '').trim();
    const tokenError = String(runtimeStatus?.token_refresh_error_message || '').trim();
    const combinedText = `${noteText} ${tokenStatus} ${tokenError}`;
    const manualStatuses = new Set([
        'account_risk_protected',
        'manual_verification_required',
        'verification_pending_manual',
        'consecutive_failure_protected',
        'captcha_max_retries_exceeded',
        'password_login_backoff_wait',
        'token_refresh_failed',
        'token_refresh_exception',
    ]);
    const manualKeywords = ['滑块', '风控', '验证码', '验证', '账号存在风险', '拦截', '客户端登录'];
    const needsIntervention = connectorNeedsManual
        || Boolean(noteText)
        || manualStatuses.has(tokenStatus)
        || manualKeywords.some(keyword => combinedText.includes(keyword));

    if (!needsIntervention) {
        return null;
    }

    let title = connectorNeedsManual
        ? '账号需要重新认证'
        : (noteText || '检测到滑块/风控，需要人工处理');
    if (!connectorNeedsManual && !noteText && tokenStatus === 'password_login_backoff_wait') {
        title = '登录恢复退避中，暂不可接管';
    } else if (!connectorNeedsManual && !noteText && tokenStatus === 'captcha_max_retries_exceeded') {
        title = '滑块自动处理失败，需要人工验证';
    }

    let detail = connectorNeedsManual
        ? (connectorReason || '连接器已停止自动恢复，请重新扫码认证。')
        : (tokenError || '系统检测到认证链路异常。');
    if (!connectorNeedsManual && tokenStatus === 'password_login_backoff_wait') {
        detail = tokenError || '当前只是失败退避等待，浏览器流程通常已结束。请重新发起“刷新 Cookie”并勾选“显示浏览器”，或等待退避结束。';
    } else if (!connectorNeedsManual && (tokenStatus === 'captcha_max_retries_exceeded' || tokenStatus === 'token_refresh_failed' || tokenStatus === 'token_refresh_exception')) {
        detail = tokenError || '当前没有可接管的浏览器流程。请重新发起“刷新 Cookie”并勾选“显示浏览器”，让系统打开新的可接管页面。';
    }

    return {
        title,
        detail,
        accountId: String(accountId || '').trim(),
    };
}

function buildManualInterventionAlert(statusNote, runtimeStatus, options = {}) {
    const alert = getManualInterventionAlert(statusNote, runtimeStatus, options.accountId);
    if (!alert) {
        return '';
    }

    const compactClass = options.compact ? ' is-compact' : '';
    return `
        <div class="manual-intervention-alert${compactClass}">
            <div class="manual-intervention-alert-icon">
                <i class="bi bi-exclamation-octagon-fill"></i>
            </div>
            <div class="manual-intervention-alert-copy">
                <div class="manual-intervention-alert-title">${escapeHtml(alert.title)}</div>
                <div class="manual-intervention-alert-detail">${escapeHtml(alert.detail)}</div>
            </div>
            ${alert.accountId ? `
                <button type="button" class="manual-intervention-alert-action" onclick="event.stopPropagation(); openRemoteVerificationConsole('${escapeHtml(alert.accountId)}');">
                    <i class="bi bi-display"></i>
                    打开实时验证
                </button>
            ` : ''}
        </div>
    `;
}

function renderDashboardAccountCard(account) {
    const isEnabled = account.enabled === undefined ? true : account.enabled;
    const keywordCount = account.keywordCount || 0;
    const defaultReplyEnabled = Boolean(account.defaultReply?.enabled);
    const aiReplyEnabled = Boolean(account.aiReply?.ai_enabled);
    const autoConfirmEnabled = account.auto_confirm === undefined ? true : Boolean(account.auto_confirm);
    const autoCommentEnabled = Boolean(account.auto_comment);
    const hasCredentials = Boolean(account.username) && Boolean(account.has_password);
    const hasPartialCredentials = !hasCredentials && (Boolean(account.username) || Boolean(account.has_password));
    const pauseDuration = account.pause_duration === 0 ? '不暂停' : `${account.pause_duration || 10} 分钟`;
    const polishSchedule = account.polishSchedule;
    const remarkText = account.remark || '';
    const statusNoteText = String(account.status_note || '').trim();

    let replyModeText = '未开启';
    let replyModeTone = 'off';
    if (aiReplyEnabled && defaultReplyEnabled) {
        replyModeText = 'AI + 默认';
        replyModeTone = 'info';
    } else if (aiReplyEnabled) {
        replyModeText = 'AI 回复';
        replyModeTone = 'info';
    } else if (defaultReplyEnabled) {
        replyModeText = '默认回复';
        replyModeTone = 'on';
    }

    let polishScheduleMetricText = '未设置';
    let polishScheduleTone = 'off';
    if (polishSchedule) {
        if (polishSchedule.enabled) {
            const displayHour = formatPolishScheduleHour(polishSchedule.delay_minutes ?? polishSchedule.run_hour);
            polishScheduleMetricText = `${displayHour}`;
            polishScheduleTone = 'info';
        } else {
            const displayHour = formatPolishScheduleHour(polishSchedule.delay_minutes ?? polishSchedule.run_hour);
            polishScheduleMetricText = `${displayHour} 未开`;
            polishScheduleTone = 'warn';
        }
    } else if (isEnabled) {
        polishScheduleMetricText = '未设置';
        polishScheduleTone = 'off';
    }

    const metrics = [
        renderDashboardAccountMetric('关键词', keywordCount > 0 ? `${keywordCount} 个` : '未配置', keywordCount > 0 ? 'on' : 'off'),
        renderDashboardAccountMetric('回复模式', replyModeText, replyModeTone),
        renderDashboardAccountMetric('定时擦亮', polishScheduleMetricText, polishScheduleTone)
    ].join('');
    const runtimeSnapshot = renderDashboardAccountRuntimeSnapshot(account.runtime_status);
    const manualInterventionAlert = buildManualInterventionAlert(statusNoteText, account.runtime_status, { compact: true, accountId: account.id });

    const secondarySummary = [
        {
            label: '关键词',
            icon: 'chat-left-text-fill',
            tone: keywordCount > 0 ? 'on' : 'off'
        },
        {
            label: '自动发货',
            icon: 'lightning-charge-fill',
            tone: autoConfirmEnabled ? 'on' : 'off'
        },
        {
            label: '自动好评',
            icon: 'chat-heart-fill',
            tone: autoCommentEnabled ? 'on' : 'off'
        },
        {
            label: '账密',
            icon: hasPartialCredentials ? 'exclamation-triangle-fill' : 'shield-lock-fill',
            tone: hasCredentials ? 'info' : (hasPartialCredentials ? 'warn' : 'off')
        },
        {
            label: '暂停',
            value: pauseDuration,
            icon: 'clock-history',
            tone: 'neutral'
        }
    ].map(({ label, value = '', icon, tone }) => `
        <span class="dashboard-account-secondary-pill is-${tone}">
            <i class="bi bi-${icon} dashboard-account-secondary-pill-icon"></i>
            <span class="dashboard-account-secondary-pill-label">${escapeHtml(label)}</span>
            ${value ? `<span class="dashboard-account-secondary-pill-value">${escapeHtml(value)}</span>` : ''}
        </span>
    `).join('');

    return `
        <div class="dashboard-account-card ${isEnabled ? '' : 'is-disabled'}" data-account-id="${escapeHtml(account.id)}" role="button" tabindex="0" onclick="openAccountManagement(this.dataset.accountId)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openAccountManagement(this.dataset.accountId);}">
            <div class="dashboard-account-card-head">
                <div class="dashboard-account-card-main">
                    <div class="dashboard-account-card-title">
                        <div class="dashboard-account-card-id">${escapeHtml(account.id)}</div>
                        ${remarkText ? `<span class="dashboard-account-card-remark-badge">${escapeHtml(remarkText)}</span>` : ''}
                    </div>
                    <div class="dashboard-account-secondary">${secondarySummary}</div>
                </div>
                <div class="dashboard-account-card-side">
                    <span class="dashboard-account-status ${isEnabled ? 'is-enabled' : 'is-disabled'}">
                        <i class="bi bi-${isEnabled ? 'check-circle-fill' : 'pause-circle-fill'}"></i>
                        ${isEnabled ? '启用中' : '已禁用'}
                    </span>
                    ${renderStatusNoteBadge(statusNoteText, 'dashboard-account-status-note')}
                </div>
            </div>
            ${manualInterventionAlert}
            <div class="dashboard-account-main-metrics">${metrics}</div>
            ${runtimeSnapshot}
        </div>
    `;
}

function renderDashboardAccountOverview(accounts, totalItems = 0) {
    const summary = document.getElementById('dashboardAccountSummary');
    const enabledContainer = document.getElementById('dashboardEnabledAccounts');
    const disabledContainer = document.getElementById('dashboardDisabledAccounts');
    const enabledHint = document.getElementById('dashboardEnabledAccountsHint');
    const disabledHint = document.getElementById('dashboardDisabledAccountsHint');

    if (!summary || !enabledContainer || !disabledContainer || !enabledHint || !disabledHint) {
        return;
    }

    const enabledAccounts = accounts.filter(account => account.enabled === undefined ? true : account.enabled);
    const disabledAccounts = accounts.filter(account => !(account.enabled === undefined ? true : account.enabled));
    const riskProtectedAccounts = disabledAccounts.filter(account => String(account.status_note || '').trim()).length;
    const activeKeywordAccounts = enabledAccounts.filter(account => (account.keywordCount || 0) > 0).length;
    const totalKeywords = enabledAccounts.reduce((sum, account) => sum + (account.keywordCount || 0), 0);

    summary.innerHTML = [
        ['全部账号', String(accounts.length), 'primary', []],
        ['已启用 / 已禁用', `${enabledAccounts.length} / ${disabledAccounts.length}`, 'success', []],
        ['关键词总数', String(totalKeywords), 'info', []],
        ['商品总数', String(totalItems), 'muted', []]
    ].map(([label, value, tone, details]) => renderDashboardSummaryCard(label, value, tone, details)).join('');

    enabledHint.textContent = `${enabledAccounts.length} 个账号`;
    disabledHint.textContent = disabledAccounts.length
        ? `${disabledAccounts.length} 个账号待恢复${riskProtectedAccounts ? `，其中 ${riskProtectedAccounts} 个处于风控保护中` : ''}`
        : '暂无禁用账号';

    const sortAccounts = (items) => [...items].sort((a, b) => {
        const keywordDiff = (b.keywordCount || 0) - (a.keywordCount || 0);
        if (keywordDiff !== 0) {
            return keywordDiff;
        }
        return String(a.id || '').localeCompare(String(b.id || ''), 'zh-Hans-CN');
    });

    enabledContainer.innerHTML = enabledAccounts.length
        ? sortAccounts(enabledAccounts).map(renderDashboardAccountCard).join('')
        : '<div class="dashboard-account-empty"><i class="bi bi-inbox me-1"></i>暂无启用账号</div>';

    disabledContainer.innerHTML = disabledAccounts.length
        ? sortAccounts(disabledAccounts).map(renderDashboardAccountCard).join('')
        : '<div class="dashboard-account-empty"><i class="bi bi-inbox me-1"></i>暂无禁用账号</div>';
}

// 加载仪表盘数据
async function loadDashboard() {
    try {
    toggleLoading(true);
    loadDashboardAnnouncement();

    // 获取账号列表
    const cookiesResponse = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (cookiesResponse.ok) {
        const cookiesData = await cookiesResponse.json();

        const accountsWithKeywords = await enrichDashboardAccounts(cookiesData);

        dashboardData.accounts = accountsWithKeywords;
        dashboardData.totalKeywords = accountsWithKeywords.reduce((sum, account) => {
        const isEnabled = account.enabled === undefined ? true : account.enabled;
        return sum + (isEnabled ? (account.keywordCount || 0) : 0);
        }, 0);

        // 加载商品总数
        const totalItems = await loadItemsCount();
        dashboardData.totalItems = totalItems;

        // 加载订单看板数据
        const orderMetrics = await loadOrderDashboardMetrics();

        // 加载销售额摘要数据
        await loadSalesSummary();

        // 加载销售额图表数据（默认显示最近1周）
        await loadSalesChart('week');

        // 更新仪表盘显示
        renderDashboardAccountOverview(accountsWithKeywords, totalItems);
        scheduleDashboardRuntimeAutoRetry(accountsWithKeywords);
        await loadDashboardDeliveryLogs();
    }
    } catch (error) {
    console.error('加载仪表盘数据失败:', error);
    showToast('加载仪表盘数据失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

async function refreshDashboardRuntimeSnapshots() {
    if (!dashboardData.accounts.length) {
        return;
    }

    try {
        const cookieDetails = await fetchJSON(`${apiBase}/cookies/details`);
        const runtimeStatusMap = new Map(
            (Array.isArray(cookieDetails) ? cookieDetails : []).map(cookie => [
                String(cookie.id),
                {
                    runtime_status: cookie.runtime_status || null,
                    enabled: cookie.enabled,
                    status_note: cookie.status_note || '',
                }
            ])
        );

        dashboardData.accounts = dashboardData.accounts.map(account => {
            const accountId = String(account.id || '');
            if (!runtimeStatusMap.has(accountId)) {
                return account;
            }
            const latestDetail = runtimeStatusMap.get(accountId);
            return {
                ...account,
                runtime_status: latestDetail.runtime_status,
                enabled: latestDetail.enabled,
                status_note: latestDetail.status_note,
            };
        });

        renderDashboardAccountOverview(dashboardData.accounts, dashboardData.totalItems || 0);
        scheduleDashboardRuntimeAutoRetry(dashboardData.accounts);
    } catch (error) {
        console.error('刷新仪表盘运行态失败:', error);
    }
}

// 加载商品总数
async function loadItemsCount() {
    try {
        const response = await fetch(`${apiBase}/items`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('获取商品列表失败');
        }

        const data = await response.json();
        const items = Array.isArray(data.items) ? data.items : [];
        return items.length;
    } catch (error) {
        console.error('加载商品总数失败:', error);
        return 0;
    }
}

// 加载仪表盘订单指标
async function loadOrderDashboardMetrics() {
    const defaultMetrics = {
        totalOrders: 0,
        totalSalesAmount: 0,
        completionRate: 0,
        todayOrders: 0
    };

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/api/orders', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (!data.success) {
            console.error('加载订单数量失败:', data.message);
            updateDashboardOrderMetrics(defaultMetrics);
            return defaultMetrics;
        }

        const orders = Array.isArray(data.data) ? data.data : [];
        const totalOrders = orders.length;

        let totalSalesAmount = 0;
        let completedOrders = 0;
        let completionEligibleOrders = 0;
        let todayOrders = 0;

        orders.forEach(order => {
            const normalizedStatus = normalizeOrderStatus(order?.order_status);
            const parsedAmount = parseOrderAmount(order);

            if (isSalesEligibleOrder(normalizedStatus) && parsedAmount !== null) {
                totalSalesAmount += parsedAmount;
            }

            if (isCompletionEligibleOrder(normalizedStatus)) {
                completionEligibleOrders++;
                if (isCompletedOrder(normalizedStatus)) {
                    completedOrders++;
                }
            }

            if (isTodayOrder(getEffectiveOrderSalesTime(order))) {
                todayOrders++;
            }
        });

        const metrics = {
            totalOrders,
            totalSalesAmount,
            completionRate: completionEligibleOrders > 0 ? (completedOrders / completionEligibleOrders) * 100 : 0,
            todayOrders
        };

        updateDashboardOrderMetrics(metrics);
        return metrics;
    } catch (error) {
        console.error('加载订单数量失败:', error);
        updateDashboardOrderMetrics(defaultMetrics);
        return defaultMetrics;
    }
}

// 销售额摘要定时刷新定时器
let salesSummaryRefreshTimer = null;

// 加载销售额摘要数据
async function loadSalesSummary() {
    const todaySalesEl = document.getElementById('dashboardTodaySales');
    const weekSalesEl = document.getElementById('dashboardWeekSales');
    const monthSalesEl = document.getElementById('dashboardMonthSales');
    const updateTimeEl = document.getElementById('dashboardSalesUpdateTime');
    
    // 显示加载状态
    showSalesLoadingState(todaySalesEl);
    showSalesLoadingState(weekSalesEl);
    showSalesLoadingState(monthSalesEl);
    
    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/api/sales/summary', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (data.success && data.data) {
            updateDashboardSalesMetrics(data.data);
        } else {
            showSalesErrorState(todaySalesEl, '获取失败');
            showSalesErrorState(weekSalesEl, '获取失败');
            showSalesErrorState(monthSalesEl, '获取失败');
        }
    } catch (error) {
        console.error('加载销售额摘要失败:', error);
        showSalesErrorState(todaySalesEl, '加载失败');
        showSalesErrorState(weekSalesEl, '加载失败');
        showSalesErrorState(monthSalesEl, '加载失败');
    }
    
    // 启动定时刷新（每5分钟刷新一次）
    startSalesSummaryRefreshTimer();
}

// 显示销售额加载状态
function showSalesLoadingState(element) {
    if (element) {
        element.innerHTML = '<span class="sales-value-loading">加载中...</span>';
    }
}

// 显示销售额错误状态
function showSalesErrorState(element, message) {
    if (element) {
        element.innerHTML = `<span class="sales-value-error">${message}</span>`;
    }
}

// 格式化销售额显示（带千分位分隔符）
function formatSalesAmount(amount) {
    return amount.toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

// 更新销售额指标
function updateDashboardSalesMetrics(metrics) {
    const todaySalesEl = document.getElementById('dashboardTodaySales');
    const weekSalesEl = document.getElementById('dashboardWeekSales');
    const monthSalesEl = document.getElementById('dashboardMonthSales');
    const updateTimeEl = document.getElementById('dashboardSalesUpdateTime');

    if (todaySalesEl) {
        todaySalesEl.innerHTML = `￥${formatSalesAmount(metrics.today_sales)}`;
    }

    if (weekSalesEl) {
        weekSalesEl.innerHTML = `￥${formatSalesAmount(metrics.week_sales)}`;
    }

    if (monthSalesEl) {
        monthSalesEl.innerHTML = `￥${formatSalesAmount(metrics.month_sales)}`;
    }

    if (updateTimeEl) {
        updateTimeEl.textContent = metrics.update_time;
    }
}

// 启动销售额摘要定时刷新
function startSalesSummaryRefreshTimer() {
    // 清除现有定时器
    if (salesSummaryRefreshTimer) {
        clearInterval(salesSummaryRefreshTimer);
    }
    
    // 每5分钟刷新一次
    salesSummaryRefreshTimer = setInterval(async () => {
        try {
            const token = localStorage.getItem('auth_token');
            if (!token) {
                clearInterval(salesSummaryRefreshTimer);
                return;
            }
            
            const response = await fetch('/api/sales/summary', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            const data = await response.json();
            if (data.success && data.data) {
                updateDashboardSalesMetrics(data.data);
            }
        } catch (error) {
            console.error('定时刷新销售额摘要失败:', error);
        }
    }, 5 * 60 * 1000); // 5分钟
}

// 停止销售额摘要定时刷新
function stopSalesSummaryRefreshTimer() {
    if (salesSummaryRefreshTimer) {
        clearInterval(salesSummaryRefreshTimer);
        salesSummaryRefreshTimer = null;
    }
}

// 销售额图表实例
let salesChartInstance = null;
let currentChartPeriod = null;
let salesDateRangeOutsideClickBound = false;

// 显示图表加载状态
function showChartLoading() {
    const chartContainer = document.querySelector('.chart-container');
    if (!chartContainer) return;
    
    // 添加加载遮罩
    let loadingOverlay = chartContainer.querySelector('.chart-loading-overlay');
    if (!loadingOverlay) {
        loadingOverlay = document.createElement('div');
        loadingOverlay.className = 'chart-loading-overlay';
        loadingOverlay.innerHTML = `
            <div class="chart-loading-spinner">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">加载中...</span>
                </div>
                <span class="chart-loading-text">数据加载中...</span>
            </div>
        `;
        chartContainer.style.position = 'relative';
        chartContainer.appendChild(loadingOverlay);
    }
    loadingOverlay.style.display = 'flex';
}

// 隐藏图表加载状态
function hideChartLoading() {
    const loadingOverlay = document.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.style.display = 'none';
    }
}

// 更新按钮激活状态
function updateChartButtonState(activePeriod) {
    const buttons = document.querySelectorAll('.sales-period-button');
    buttons.forEach(btn => {
        const btnPeriod = btn.dataset.period;
        const isActive = btnPeriod === activePeriod;

        btn.classList.toggle('is-active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
}

// 加载销售额图表数据
async function loadSalesChart(period) {
    showChartLoading();
    updateChartButtonState(period);
    setDateRangePickerVisible(false);
    
    try {
        const token = localStorage.getItem('auth_token');
        let startDate, endDate;
        const now = new Date();

        if (period === 'week') {
            startDate = new Date(now);
            startDate.setDate(now.getDate() - 6);
        } else if (period === 'month') {
            startDate = new Date(now);
            startDate.setMonth(now.getMonth() - 1);
        }

        const startDateStr = startDate.toISOString().split('T')[0];
        const endDateStr = now.toISOString().split('T')[0];

        const response = await fetch(`/api/sales?start_date=${startDateStr}&end_date=${endDateStr}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (data.success && data.data) {
            currentChartPeriod = period;
            renderSalesChart(data.data.sales, period);
        }
    } catch (error) {
        console.error('加载销售额图表数据失败:', error);
        showToast('加载销售额数据失败', 'danger');
    } finally {
        hideChartLoading();
    }
}

// 加载自定义日期范围的销售额数据
async function loadCustomSalesChart() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;

    if (!startDate || !endDate) {
        showToast('请选择开始和结束日期', 'warning');
        return;
    }

    if (new Date(startDate) > new Date(endDate)) {
        showToast('开始日期不能晚于结束日期', 'warning');
        return;
    }

    showChartLoading();
    updateChartButtonState('custom');

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/api/sales?start_date=${startDate}&end_date=${endDate}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (data.success && data.data) {
            currentChartPeriod = 'custom';
            renderSalesChart(data.data.sales, 'custom');
        }
    } catch (error) {
        console.error('加载自定义销售额数据失败:', error);
        showToast('加载销售额数据失败', 'danger');
    } finally {
        hideChartLoading();
    }
}

function setDateRangePickerVisible(visible) {
    const dateRangePicker = document.getElementById('dateRangePicker');
    const customButton = document.querySelector('.sales-period-button[data-period="custom"]');
    const timeRangeSelector = document.querySelector('.time-range-selector');
    if (!dateRangePicker) {
        return;
    }

    dateRangePicker.hidden = !visible;
    if (timeRangeSelector) {
        timeRangeSelector.classList.toggle('is-open', visible);
    }
    if (customButton) {
        customButton.setAttribute('aria-expanded', visible ? 'true' : 'false');
    }

    if (!salesDateRangeOutsideClickBound) {
        document.addEventListener('click', event => {
            const control = document.querySelector('.time-range-selector');
            const picker = document.getElementById('dateRangePicker');
            if (!control || !picker || picker.hidden) {
                return;
            }

            if (!control.contains(event.target)) {
                setDateRangePickerVisible(false);
                updateChartButtonState(currentChartPeriod || 'week');
            }
        });

        document.addEventListener('keydown', event => {
            const picker = document.getElementById('dateRangePicker');
            if (event.key === 'Escape' && picker && !picker.hidden) {
                setDateRangePickerVisible(false);
                updateChartButtonState(currentChartPeriod || 'week');
            }
        });

        salesDateRangeOutsideClickBound = true;
    }
}

// 切换日期选择器显示
function toggleDateRangePicker() {
    const dateRangePicker = document.getElementById('dateRangePicker');
    if (!dateRangePicker) {
        return;
    }

    const willShow = dateRangePicker.hidden;
    setDateRangePickerVisible(willShow);

    if (willShow) {
        updateChartButtonState('custom');
        return;
    }

    updateChartButtonState(currentChartPeriod || 'week');
}

// 渲染销售额图表
function renderSalesChart(salesData, period) {
    const ctx = document.getElementById('salesChart').getContext('2d');
    
    // 准备数据
    const labels = salesData.map(item => item.date);
    const data = salesData.map(item => item.amount);

    // 创建渐变填充
    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(0, 123, 255, 0.3)');
    gradient.addColorStop(0.5, 'rgba(0, 123, 255, 0.15)');
    gradient.addColorStop(1, 'rgba(0, 123, 255, 0.02)');

    // 如果图表已存在，使用平滑更新
    if (salesChartInstance) {
        // 使用动画更新数据
        salesChartInstance.data.labels = labels;
        salesChartInstance.data.datasets[0].data = data;
        salesChartInstance.data.datasets[0].backgroundColor = gradient;
        
        // 更新标题
        salesChartInstance.options.plugins.title.text = getChartTitle(period);
        
        // 平滑过渡更新
        salesChartInstance.update('active');
        return;
    }

    // 创建新图表
    salesChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: '销售额',
                data: data,
                borderColor: '#007bff',
                backgroundColor: gradient,
                borderWidth: 3,
                tension: 0.4,
                cubicInterpolationMode: 'monotone',
                fill: true,
                pointBackgroundColor: '#007bff',
                pointBorderColor: '#fff',
                pointBorderWidth: 2,
                pointRadius: 5,
                pointHoverRadius: 7,
                pointHoverBackgroundColor: '#0056b3',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 750,
                easing: 'easeInOutQuart'
            },
            transitions: {
                active: {
                    animation: {
                        duration: 750,
                        easing: 'easeInOutQuart'
                    }
                }
            },
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        usePointStyle: true,
                        padding: 15,
                        font: {
                            size: 13,
                            weight: '500'
                        }
                    }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    borderColor: '#007bff',
                    borderWidth: 1,
                    padding: 12,
                    displayColors: true,
                    callbacks: {
                        label: function(context) {
                            return `销售额: ￥${context.parsed.y.toFixed(2)}`;
                        }
                    }
                },
                title: {
                    display: true,
                    text: getChartTitle(period),
                    font: {
                        size: 16,
                        weight: '600'
                    },
                    padding: {
                        bottom: 15
                    }
                }
            },
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: '日期',
                        font: {
                            size: 12,
                            weight: '500'
                        }
                    },
                    grid: {
                        display: false
                    },
                    ticks: {
                        font: {
                            size: 11
                        }
                    }
                },
                y: {
                    display: true,
                    title: {
                        display: true,
                        text: '销售额 (￥)',
                        font: {
                            size: 12,
                            weight: '500'
                        }
                    },
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        callback: function(value) {
                            return '￥' + value;
                        },
                        font: {
                            size: 11
                        }
                    }
                }
            }
        }
    });
}

// 获取图表标题
function getChartTitle(period) {
    if (period === 'week') {
        return '最近1周销售额趋势';
    } else if (period === 'month') {
        return '最近1月销售额趋势';
    } else {
        return '自定义时间范围销售额趋势';
    }
}

function parseOrderAmount(order) {
    const amountCandidates = [
        order?.amount,
        order?.total_amount,
        order?.order_amount,
        order?.pay_amount,
        order?.price
    ];

    for (const amount of amountCandidates) {
        if (amount === undefined || amount === null || amount === '') continue;
        const normalized = String(amount).replace(/[^\d.-]/g, '');
        if (!normalized || normalized === '-' || normalized === '.' || normalized === '-.') {
            continue;
        }
        const numericAmount = parseFloat(normalized);
        if (!Number.isNaN(numericAmount)) {
            return numericAmount;
        }
    }

    return null;
}

function formatOrderAmountDisplay(rawAmount) {
    if (rawAmount === undefined || rawAmount === null) {
        return '-';
    }

    const amountText = String(rawAmount).trim();
    if (!amountText) {
        return '-';
    }

    // 已包含货币符号时直接展示，避免重复拼接
    if (/[¥￥$]/.test(amountText)) {
        return amountText;
    }

    return `¥${amountText}`;
}

function normalizeOrderStatus(status) {
    const value = String(status || '').toLowerCase();
    const aliasMap = {
        success: 'completed',
        finished: 'completed',
        pending_delivery: 'pending_ship',
        partial_success: 'partial_success',
        partial_pending_finalize: 'partial_pending_finalize',
        delivered: 'shipped',
        closed: 'cancelled',
        refunded: 'cancelled',
        canceled: 'cancelled'
    };
    return aliasMap[value] || value || 'unknown';
}

function isCompletedOrder(normalizedStatus) {
    return normalizedStatus === 'completed';
}

function isSalesEligibleOrder(normalizedStatus) {
    const salesEligibleStatuses = ['pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped', 'completed'];
    return salesEligibleStatuses.includes(normalizedStatus);
}

function isCompletionEligibleOrder(normalizedStatus) {
    const completionEligibleStatuses = ['pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped', 'completed', 'cancelled', 'refunding', 'refund_cancelled'];
    return completionEligibleStatuses.includes(normalizedStatus);
}

function parseUtcDateTime(dateString) {
    if (!dateString) return null;

    if (dateString instanceof Date) {
        return Number.isNaN(dateString.getTime()) ? null : dateString;
    }

    const raw = String(dateString).trim();
    if (!raw) return null;

    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const hasTimezone = /([zZ]|[+-]\d{2}:\d{2})$/.test(normalized);
    const parsed = new Date(hasTimezone ? normalized : `${normalized}Z`);

    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

const beijingMinuteFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    hourCycle: 'h23'
});

const beijingDateFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
});

const beijingSecondFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    hourCycle: 'h23'
});

function formatBeijingDateTime(dateString) {
    const date = parseUtcDateTime(dateString);
    if (!date) return '--';

    const parts = {};
    beijingMinuteFormatter.formatToParts(date).forEach(part => {
        if (part.type !== 'literal') {
            parts[part.type] = part.value;
        }
    });

    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function formatBeijingDateTimeWithSeconds(dateInput) {
    const date = parseUtcDateTime(dateInput);
    if (!date) return '--';

    const parts = {};
    beijingSecondFormatter.formatToParts(date).forEach(part => {
        if (part.type !== 'literal') {
            parts[part.type] = part.value;
        }
    });

    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

function getBeijingDateKey(dateInput) {
    const date = parseUtcDateTime(dateInput);
    if (!date) return '';

    const parts = {};
    beijingDateFormatter.formatToParts(date).forEach(part => {
        if (part.type !== 'literal') {
            parts[part.type] = part.value;
        }
    });

    return `${parts.year}-${parts.month}-${parts.day}`;
}

function getEffectiveOrderSalesTime(order) {
    const platformPaidAt = String(order?.platform_paid_at || '').trim();
    if (platformPaidAt) return platformPaidAt;

    const platformCreatedAt = String(order?.platform_created_at || '').trim();
    if (platformCreatedAt) return platformCreatedAt;

    const createdAt = String(order?.created_at || '').trim();
    return createdAt || null;
}

function formatAboutRuntimeTime(displayValue, rawTimestamp) {
    const displayText = typeof displayValue === 'string' ? displayValue.trim() : '';
    if (displayText) {
        if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$/.test(displayText)) {
            return displayText.replace('T', ' ');
        }

        const normalizedDisplay = formatBeijingDateTimeWithSeconds(displayText);
        if (normalizedDisplay !== '--') {
            return normalizedDisplay;
        }

        return displayText;
    }

    const numericTimestamp = Number(rawTimestamp);
    if (!Number.isFinite(numericTimestamp) || numericTimestamp <= 0) {
        return '暂无记录';
    }

    const millis = numericTimestamp > 1e12 ? numericTimestamp : numericTimestamp * 1000;
    return formatBeijingDateTimeWithSeconds(new Date(millis));
}

function isTodayOrder(createdAt) {
    const orderDateKey = getBeijingDateKey(createdAt);
    if (!orderDateKey) return false;

    return orderDateKey === getBeijingDateKey(new Date());
}

function updateDashboardOrderMetrics(metrics) {
    const totalOrdersEl = document.getElementById('dashboardOrderTotal');
    const salesAmountEl = document.getElementById('dashboardSalesAmount');
    const completionRateEl = document.getElementById('dashboardCompletionRate');
    const todayOrdersEl = document.getElementById('dashboardTodayOrders');

    if (totalOrdersEl) {
        totalOrdersEl.textContent = metrics.totalOrders;
    }

    if (salesAmountEl) {
        salesAmountEl.textContent = `￥${metrics.totalSalesAmount.toLocaleString('zh-CN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        })}`;
    }

    if (completionRateEl) {
        completionRateEl.textContent = `${metrics.completionRate.toFixed(1)}%`;
    }

    if (todayOrdersEl) {
        todayOrdersEl.textContent = metrics.todayOrders;
    }
}

// 更新仪表盘统计数据
function openAccountManagement(accountId) {
    pendingAccountManagementFocusId = accountId || '';
    const accountsSection = document.getElementById('accounts-section');
    if (accountsSection && accountsSection.classList.contains('active')) {
        loadCookies();
        return;
    }
    showSection('accounts');
}

function focusPendingAccountManagementRow() {
    if (!pendingAccountManagementFocusId) {
        return;
    }

    const rows = document.querySelectorAll('#cookieTable tbody tr[data-account-id]');
    const targetRow = Array.from(rows).find(row => row.dataset.accountId === pendingAccountManagementFocusId);
    if (!targetRow) {
        return;
    }

    pendingAccountManagementFocusId = '';
    targetRow.classList.add('dashboard-account-focus');
    targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => targetRow.classList.remove('dashboard-account-focus'), 2200);
}

async function loadDashboardDeliveryLogs() {
    const tbody = document.getElementById('dashboardDeliveryLogsList');
    if (!tbody) return;

    try {
        const response = await fetch(`${apiBase}/delivery-logs/recent?limit=20`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const logs = Array.isArray(data.logs) ? data.logs : [];
        renderDashboardDeliveryLogs(logs);
    } catch (error) {
        console.error('加载仪表盘发货日志失败:', error);
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-4">
                    <i class="bi bi-exclamation-triangle fs-4 d-block mb-2"></i>
                    发货日志加载失败
                </td>
            </tr>
        `;
    }
}

function renderDashboardDeliveryLogs(logs) {
    const tbody = document.getElementById('dashboardDeliveryLogsList');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (!logs.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    暂无发货日志
                </td>
            </tr>
        `;
        return;
    }

    logs.forEach(log => {
        const normalizedStatus = String(log.status || '').toLowerCase();
        const isSuccess = normalizedStatus === 'success';
        const isSkipped = normalizedStatus === 'skipped';
        const statusBadge = isSuccess
            ? '<span class="badge bg-success">成功</span>'
            : (isSkipped
                ? '<span class="badge bg-secondary">已跳过</span>'
                : '<span class="badge bg-danger">失败</span>');

        const matchModeLabelMap = {
            no_spec_match: '无规格',
            one_spec_exact: '一组规格',
            one_spec_fallback_no_spec: '单规兜底',
            two_spec_exact: '两组规格',
            blocked_no_rule: '无规则',
            blocked_no_spec_parsed: '缺少规格',
            blocked_multiple_no_spec_rules: '多规则阻断',
            blocked_rule_mode_mismatch: '模式不一致'
        };

        const specModeLabelMap = {
            no_spec: '无规格',
            one_spec: '一组规格',
            two_spec: '两组规格',
            spec_enabled: '已开规格'
        };

        function buildBadge(text, className) {
            return `<span class="badge ${className}">${escapeHtml(text)}</span>`;
        }

        let matchBadge = buildBadge(matchModeLabelMap[log.match_mode] || (log.match_mode || '未知'), 'bg-secondary');
        if (log.match_mode === 'one_spec_exact' || log.match_mode === 'two_spec_exact') {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode], 'bg-primary');
        } else if (log.match_mode === 'one_spec_fallback_no_spec') {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode], 'bg-info text-dark');
        } else if (log.match_mode === 'no_spec_match') {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode], 'bg-warning text-dark');
        } else if (String(log.match_mode || '').startsWith('blocked_')) {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode] || log.match_mode, 'bg-danger');
        }

        const specModes = [log.order_spec_mode, log.rule_spec_mode, log.item_config_mode].filter(Boolean);
        const uniqueSpecLabels = [...new Set(specModes.map(mode => specModeLabelMap[mode] || mode))];
        const hasEnabledSpecMode = specModes.some(mode => ['one_spec', 'two_spec', 'spec_enabled'].includes(mode));
        const hasNoSpecMode = specModes.some(mode => mode === 'no_spec');
        let specModeTitle = '';
        if (log.match_mode === 'blocked_rule_mode_mismatch') {
            specModeTitle = uniqueSpecLabels.join(' / ') || '规格不一致';
        } else if (log.match_mode === 'two_spec_exact' || specModes.includes('two_spec')) {
            specModeTitle = '两组规格';
        } else if (log.match_mode === 'one_spec_exact' || log.match_mode === 'one_spec_fallback_no_spec' || specModes.includes('one_spec')) {
            specModeTitle = '一组规格';
        } else if (log.match_mode === 'no_spec_match' || hasNoSpecMode) {
            specModeTitle = '无规格';
        } else if (specModes.includes('spec_enabled')) {
            specModeTitle = '已开规格';
        }

        let specSummary = '<span class="text-muted">-</span>';
        if (log.match_mode === 'blocked_rule_mode_mismatch') {
            specSummary = `<span title="${escapeHtml(specModeTitle || '规格模式不一致')}">${buildBadge('规格不一致', 'bg-warning text-dark')}</span>`;
        } else if (hasEnabledSpecMode || ['one_spec_exact', 'one_spec_fallback_no_spec', 'two_spec_exact'].includes(log.match_mode)) {
            specSummary = `<span title="${escapeHtml(specModeTitle || '已开规格')}">${buildBadge('已开规格', 'bg-info text-dark')}</span>`;
        } else if (hasNoSpecMode || log.match_mode === 'no_spec_match') {
            specSummary = `<span title="${escapeHtml(specModeTitle || '未开规格')}">${buildBadge('未开规格', 'bg-secondary')}</span>`;
        }

        const ruleText = log.rule_keyword
            ? `<div class="dashboard-delivery-rule" title="${escapeHtml(log.rule_keyword)}">${escapeHtml(log.rule_keyword)}</div>`
            : '<span class="text-muted">未命中规则</span>';

        const channelText = log.channel === 'manual' ? '手动' : '自动';
        const channelBadgeClass = log.channel === 'manual' ? 'dashboard-delivery-channel-manual' : 'dashboard-delivery-channel-auto';
        const reasonText = isSuccess
            ? (log.reason || '发货成功')
            : (isSkipped
                ? (log.reason || '已跳过重复发货')
                : (log.reason || '未知失败原因'));

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="text-nowrap"><small>${escapeHtml(formatDateTime(log.created_at || ''))}</small></td>
            <td class="text-nowrap">${escapeHtml(log.order_id || '-')}</td>
            <td>${statusBadge}</td>
            <td>${ruleText}</td>
            <td>${matchBadge}</td>
            <td>${specSummary}</td>
            <td>
                <span class="badge ${channelBadgeClass}">${escapeHtml(channelText)}</span>
            </td>
            <td class="dashboard-delivery-reason" title="${escapeHtml(reasonText)}">${escapeHtml(reasonText)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// 获取账号关键词数量（带缓存）- 包含普通关键词和商品关键词
async function getAccountKeywordCount(accountId) {
    const now = Date.now();

    // 检查缓存
    if (accountKeywordCache[accountId] && (now - cacheTimestamp) < CACHE_DURATION) {
    return accountKeywordCache[accountId];
    }

    try {
    const response = await fetch(`${apiBase}/keywords/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const keywordsData = await response.json();
        // 现在API返回的是包含普通关键词和商品关键词的完整列表
        const count = keywordsData.length;

        // 更新缓存
        accountKeywordCache[accountId] = count;
        cacheTimestamp = now;

        return count;
    } else {
        return 0;
    }
    } catch (error) {
    console.error(`获取账号 ${accountId} 关键词失败:`, error);
    return 0;
    }
}

// 清除关键词缓存
function clearKeywordCache() {
    accountKeywordCache = {};
    cacheTimestamp = 0;
}

// ================================
// 【自动回复菜单】相关功能
// ================================

// 刷新账号列表（用于自动回复页面）
async function refreshAccountList() {
    try {
    toggleLoading(true);

    // 获取账号列表
    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const accounts = await response.json();
        const select = document.getElementById('accountSelect');
        select.innerHTML = '<option value="">🔍 请选择一个账号开始配置...</option>';

        // 为每个账号获取关键词数量
        const accountsWithKeywords = await Promise.all(
        accounts.map(async (account) => {
            try {
            const keywordsResponse = await fetch(`${apiBase}/keywords/${account.id}`, {
                headers: {
                'Authorization': `Bearer ${authToken}`
                }
            });

            if (keywordsResponse.ok) {
                const keywordsData = await keywordsResponse.json();
                return {
                ...account,
                keywords: keywordsData,
                keywordCount: keywordsData.length
                };
            } else {
                return {
                ...account,
                keywordCount: 0
                };
            }
            } catch (error) {
            console.error(`获取账号 ${account.id} 关键词失败:`, error);
            return {
                ...account,
                keywordCount: 0
            };
            }
        })
        );

        // 渲染账号选项（显示所有账号，但标识禁用状态）
        if (accountsWithKeywords.length === 0) {
        select.innerHTML = '<option value="">❌ 暂无账号，请先添加账号</option>';
        return;
        }

        // 分组显示：先显示启用的账号，再显示禁用的账号
        const enabledAccounts = accountsWithKeywords.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        console.log(`账号 ${account.id} 过滤状态: enabled=${account.enabled}, 判断为启用=${enabled}`); // 调试信息
        return enabled;
        });
        const disabledAccounts = accountsWithKeywords.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return !enabled;
        });

        // 渲染启用的账号
        enabledAccounts.forEach(account => {
        const option = document.createElement('option');
        option.value = account.id;

        // 根据关键词数量显示不同的图标和样式
        let icon = '📝';
        let status = '';
        if (account.keywordCount === 0) {
            icon = '⚪';
            status = ' (未配置)';
        } else if (account.keywordCount >= 5) {
            icon = '🟢';
            status = ` (${account.keywordCount} 个关键词)`;
        } else {
            icon = '🟡';
            status = ` (${account.keywordCount} 个关键词)`;
        }

        option.textContent = `${icon} ${account.id}${status}`;
        select.appendChild(option);
        });

        // 如果有禁用的账号，添加分隔线和禁用账号
        if (disabledAccounts.length > 0) {
        // 添加分隔线
        const separatorOption = document.createElement('option');
        separatorOption.disabled = true;
        separatorOption.textContent = `--- 禁用账号 (${disabledAccounts.length} 个) ---`;
        select.appendChild(separatorOption);

        // 渲染禁用的账号
        disabledAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;

            // 禁用账号使用特殊图标和样式
            let icon = '🔴';
            let status = '';
            if (account.keywordCount === 0) {
            status = ' (未配置) [已禁用]';
            } else {
            status = ` (${account.keywordCount} 个关键词) [已禁用]`;
            }

            option.textContent = `${icon} ${account.id}${status}`;
            option.style.color = '#6b7280';
            option.style.fontStyle = 'italic';
            select.appendChild(option);
        });
        }

        console.log('账号列表刷新完成，关键词统计:', accountsWithKeywords.map(a => ({id: a.id, keywords: a.keywordCount})));
    } else {
        showToast('获取账号列表失败', 'danger');
    }
    } catch (error) {
    console.error('刷新账号列表失败:', error);
    showToast('刷新账号列表失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 只刷新关键词列表（不重新加载商品列表等其他数据）
async function refreshKeywordsList() {
    if (!currentCookieId) {
        console.warn('没有选中的账号，无法刷新关键词列表');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/keywords-with-item-id/${currentCookieId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            console.log('刷新关键词列表，从服务器获取的数据:', data);

            // 更新缓存数据
            keywordsData[currentCookieId] = data;

            // 只重新渲染关键词列表
            renderKeywordsList(data);

            // 清除关键词缓存
            clearKeywordCache();
        } else {
            console.error('刷新关键词列表失败:', response.status);
            showToast('刷新关键词列表失败', 'danger');
        }
    } catch (error) {
        console.error('刷新关键词列表失败:', error);
        showToast('刷新关键词列表失败', 'danger');
    }
}

// 加载账号关键词
async function loadAccountKeywords() {
    const accountId = document.getElementById('accountSelect').value;
    const keywordManagement = document.getElementById('keywordManagement');

    if (!accountId) {
    keywordManagement.style.display = 'none';
    return;
    }

    try {
    toggleLoading(true);
    currentCookieId = accountId;

    // 获取账号详情以检查状态
    const accountResponse = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let accountStatus = true; // 默认启用
    if (accountResponse.ok) {
        const accounts = await accountResponse.json();
        const currentAccount = accounts.find(acc => acc.id === accountId);
        accountStatus = currentAccount ? (currentAccount.enabled === undefined ? true : currentAccount.enabled) : true;
        console.log(`加载关键词时账号 ${accountId} 状态: enabled=${currentAccount?.enabled}, accountStatus=${accountStatus}`); // 调试信息
    }

    const response = await fetch(`${apiBase}/keywords-with-item-id/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        console.log('从服务器获取的关键词数据:', data); // 调试信息

        // 后端返回的是 [{keyword, reply, item_id, type, image_url}, ...] 格式，直接使用
        const formattedData = data;

        console.log('格式化后的关键词数据:', formattedData); // 调试信息
        keywordsData[accountId] = formattedData;
        renderKeywordsList(formattedData);

        // 加载商品列表
        await loadItemsList(accountId);

        // 更新账号徽章显示
        updateAccountBadge(accountId, accountStatus);

        keywordManagement.style.display = 'block';
    } else {
        showToast('加载关键词失败', 'danger');
    }
    } catch (error) {
    console.error('加载关键词失败:', error);
    showToast('加载关键词失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 更新账号徽章显示
function updateAccountBadge(accountId, isEnabled) {
    const badge = document.getElementById('currentAccountBadge');
    if (!badge) return;

    const statusIcon = isEnabled ? '🟢' : '🔴';
    const statusText = isEnabled ? '启用' : '禁用';
    const statusClass = isEnabled ? 'bg-success' : 'bg-warning';

    badge.innerHTML = `
    <span class="badge ${statusClass} me-2">
        ${statusIcon} ${accountId}
    </span>
    <small class="text-muted">
        状态: ${statusText}
        ${!isEnabled ? ' (配置的关键词不会参与自动回复)' : ''}
    </small>
    `;
}

// 显示添加关键词表单
function showAddKeywordForm() {
    const form = document.getElementById('addKeywordForm');
    form.style.display = form.style.display === 'none' ? 'block' : 'none';

    if (form.style.display === 'block') {
    document.getElementById('newKeyword').focus();
    }
}

// 加载商品列表
async function loadItemsList(accountId) {
    try {
    const response = await fetch(`${apiBase}/items/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        const items = data.items || [];

        // 更新商品选择下拉框
        const selectElement = document.getElementById('newItemIdSelect');
        if (selectElement) {
        // 清空现有选项（保留第一个默认选项）
        selectElement.innerHTML = '<option value="">选择商品或留空表示通用关键词</option>';

        // 添加商品选项
        items.forEach(item => {
            const option = document.createElement('option');
            option.value = item.item_id;
            option.textContent = `${item.item_id} - ${item.item_title}`;
            selectElement.appendChild(option);
        });
        }

        console.log(`加载了 ${items.length} 个商品到选择列表`);
    } else {
        console.warn('加载商品列表失败:', response.status);
    }
    } catch (error) {
    console.error('加载商品列表时发生错误:', error);
    }
}



// 添加或更新关键词
async function addKeyword() {
    const keywordInput = document.getElementById('newKeyword').value.trim();
    const reply = document.getElementById('newReply').value.trim();
    const selectElement = document.getElementById('newItemIdSelect');
    const selectedOptions = Array.from(selectElement.selectedOptions);

    if (!keywordInput) {
    showToast('请填写关键词', 'warning');
    return;
    }

    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    // 检查是否为编辑模式
    const isEditMode = typeof window.editingIndex !== 'undefined';
    const actionText = isEditMode ? '更新' : '添加';

    try {
    toggleLoading(true);

    // 解析多个关键词（支持竖线、换行符分隔）
    const keywords = keywordInput
        .split(/[\|\n]/)
        .map(k => k.trim())
        .filter(k => k.length > 0);
    
    if (keywords.length === 0) {
        showToast('请填写有效的关键词', 'warning');
        toggleLoading(false);
        return;
    }

    // 获取选中的商品ID列表
    let itemIds = selectedOptions
        .map(opt => opt.value)
        .filter(id => id !== ''); // 过滤掉空值（通用关键词选项）
    
    // 如果没有选中任何商品，或者选中了空值，则作为通用关键词
    if (itemIds.length === 0) {
        itemIds = [''];
    }

    // 获取当前关键词列表
    let currentKeywords = [...(keywordsData[currentCookieId] || [])];

    // 如果是编辑模式，先移除原关键词
    if (isEditMode) {
        currentKeywords.splice(window.editingIndex, 1);
    }

    // 准备要保存的关键词列表（只包含文本类型的关键字）
    let textKeywords = currentKeywords.filter(item => (item.type || 'text') === 'text');

    // 如果是编辑模式，先移除原关键词
    if (isEditMode && typeof window.editingIndex !== 'undefined') {
        // 需要重新计算在文本关键字中的索引
        const originalKeyword = keywordsData[currentCookieId][window.editingIndex];
        const textIndex = textKeywords.findIndex(item =>
            item.keyword === originalKeyword.keyword &&
            (item.item_id || '') === (originalKeyword.item_id || '')
        );
        if (textIndex !== -1) {
            textKeywords.splice(textIndex, 1);
        }
    }

    // 检查关键词是否已存在（考虑商品ID，检查所有类型的关键词）
    // 在编辑模式下，需要排除正在编辑的关键词本身
    let allKeywords = keywordsData[currentCookieId] || [];
    if (isEditMode && typeof window.editingIndex !== 'undefined') {
        // 创建一个副本，排除正在编辑的关键词
        allKeywords = allKeywords.filter((item, index) => index !== window.editingIndex);
    }

    // 检查重复关键词
    const duplicates = [];
    for (const keyword of keywords) {
        for (const itemId of itemIds) {
    const existingKeyword = allKeywords.find(item =>
        item.keyword === keyword &&
        (item.item_id || '') === (itemId || '')
    );
    if (existingKeyword) {
        const itemIdText = itemId ? `（商品ID: ${itemId}）` : '（通用关键词）';
        const typeText = existingKeyword.type === 'image' ? '图片' : '文本';
                duplicates.push(`"${keyword}" ${itemIdText}`);
            }
        }
    }

    if (duplicates.length > 0) {
        showToast(`以下关键词已存在：\n${duplicates.join('\n')}\n请修改后重试`, 'warning');
        toggleLoading(false);
        return;
    }

    // 展开添加多个关键词和多个商品ID的组合
    for (const keyword of keywords) {
        for (const itemId of itemIds) {
    const newKeyword = {
        keyword: keyword,
        reply: reply,
        item_id: itemId || ''
    };
    textKeywords.push(newKeyword);
        }
    }

    const response = await fetch(`${apiBase}/keywords-with-item-id/${currentCookieId}`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        keywords: textKeywords
        })
    });

    if (response.ok) {
        const totalAdded = keywords.length * itemIds.length;
        const keywordText = keywords.length > 1 ? `${keywords.length}个关键词` : `"${keywords[0]}"`;
        const itemText = itemIds.length > 1 ? `${itemIds.length}个商品` : (itemIds[0] ? '指定商品' : '通用');
        showToast(`✨ ${keywordText} ${actionText}成功！（共${totalAdded}条配置，应用于${itemText}）`, 'success');

        // 清空输入框并重置样式
        const keywordInputEl = document.getElementById('newKeyword');
        const replyInput = document.getElementById('newReply');
        const selectElement = document.getElementById('newItemIdSelect');
        const addBtn = document.querySelector('.add-btn');

        keywordInputEl.value = '';
        replyInput.value = '';
        if (selectElement) {
            // 清除所有选中项
            Array.from(selectElement.options).forEach(opt => opt.selected = false);
        }
        keywordInputEl.style.borderColor = '#e5e7eb';
        replyInput.style.borderColor = '#e5e7eb';
        addBtn.style.opacity = '0.7';
        addBtn.style.transform = 'scale(0.95)';

        // 如果是编辑模式，重置编辑状态
        if (isEditMode) {
        delete window.editingIndex;
        delete window.originalKeyword;

        // 恢复添加按钮
        addBtn.innerHTML = '<i class="bi bi-plus-lg"></i>添加';
        addBtn.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';

        // 移除取消按钮
        const cancelBtn = document.getElementById('cancelEditBtn');
        if (cancelBtn) {
            cancelBtn.remove();
        }
        }

        // 聚焦到关键词输入框，方便连续添加
        setTimeout(() => {
        keywordInputEl.focus();
        }, 100);

        // 只刷新关键词列表，不重新加载整个界面
        await refreshKeywordsList();
    } else {
        try {
            const errorData = await response.json();
            const errorMessage = errorData.detail || '关键词添加失败';
            console.error('关键词添加失败:', errorMessage);

            // 检查是否是重复关键词的错误
            if (errorMessage.includes('关键词已存在') || errorMessage.includes('关键词重复') || errorMessage.includes('UNIQUE constraint')) {
                showToast(`❌ 关键词重复：${errorMessage}`, 'warning');
            } else {
                showToast(`❌ ${errorMessage}`, 'danger');
            }
        } catch (parseError) {
            // 如果无法解析JSON，使用原始文本
            const errorText = await response.text();
            console.error('关键词添加失败:', errorText);
            showToast('❌ 关键词添加失败', 'danger');
        }
    }
    } catch (error) {
    console.error('添加关键词失败:', error);
    showToast('添加关键词失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 渲染现代化关键词列表（分组显示）
function renderKeywordsList(keywords) {
    console.log('渲染关键词列表:', keywords);
    const container = document.getElementById('keywordsList');

    if (!container) {
    console.error('找不到关键词列表容器元素');
    return;
    }

    container.innerHTML = '';

    if (!keywords || keywords.length === 0) {
    console.log('关键词列表为空，显示空状态');
    container.innerHTML = `
        <div class="empty-state">
        <i class="bi bi-chat-dots"></i>
        <h3>还没有关键词</h3>
        <p>添加第一个关键词，让您的闲鱼店铺自动回复客户消息</p>
        <button class="quick-add-btn" onclick="focusKeywordInput()">
            <i class="bi bi-plus-lg me-2"></i>立即添加
        </button>
        </div>
    `;
    return;
    }

    // 按回复内容和类型分组
    const groups = groupKeywordsByReply(keywords);
    
    console.log(`开始渲染 ${groups.length} 个分组，共 ${keywords.length} 个关键词`);

    groups.forEach((group, groupIndex) => {
        const groupItem = document.createElement('div');
        groupItem.className = 'keyword-group-item';

        const isImageType = group.type === 'image';
    const typeBadge = isImageType ?
        '<span class="keyword-type-badge keyword-type-image"><i class="bi bi-image"></i> 图片</span>' :
        '<span class="keyword-type-badge keyword-type-text"><i class="bi bi-chat-text"></i> 文本</span>';

        // 回复内容显示
        let replyDisplay = '';
    if (isImageType) {
            const imageUrl = group.reply || group.image_url || '';
            replyDisplay = `
                <div class="keyword-group-reply">
                    <div class="d-flex align-items-center gap-3">
                <img src="${imageUrl}" alt="关键词图片" class="keyword-image-preview" onclick="showImageModal('${imageUrl}')">
                <div class="flex-grow-1">
                            <strong>回复图片：</strong>
                            <small class="text-muted d-block">点击图片查看大图</small>
                </div>
                    </div>
                </div>
            `;
    } else {
            replyDisplay = `
                <div class="keyword-group-reply" id="reply-display-${groupIndex}">
                    <div class="d-flex align-items-center">
                        <strong>回复内容：</strong>
                        <span class="reply-text-content">${group.reply || '<span class="text-muted">（空回复，不自动回复）</span>'}</span>
                        <button class="reply-edit-btn" onclick="editGroupReply(${groupIndex})" title="编辑回复内容">
                            <i class="bi bi-pencil"></i> 编辑
                        </button>
                    </div>
                </div>
            `;
    }

        // 关键词列表
        const keywordsList = group.keywords.map((kw, kwIndex) => `
            <span class="keyword-chip">
            <i class="bi bi-tag-fill"></i>
                ${kw}
                <button class="chip-remove-btn" onclick="deleteSpecificKeyword('${group.id}', ${kwIndex})" title="删除此关键词">
                    <i class="bi bi-x"></i>
            </button>
            </span>
        `).join('');

        // 商品列表
        const itemsList = group.items.map((itemInfo, itemIndex) => {
            const itemName = getItemName(itemInfo.item_id, itemInfo.item_title);
            const displayText = itemInfo.item_id ? 
                `${itemInfo.item_id} - ${itemName}` : 
                '通用关键词（所有商品）';
            const icon = itemInfo.item_id ? 'bi-box' : 'bi-globe';
            
            return `
                <span class="item-chip">
                    <i class="bi ${icon}"></i>
                    ${displayText}
                    <button class="chip-remove-btn" onclick="deleteSpecificItem('${group.id}', ${itemIndex})" title="删除此商品配置">
                        <i class="bi bi-x"></i>
            </button>
                </span>
            `;
        }).join('');

        groupItem.innerHTML = `
            <div class="keyword-group-header">
                <div class="keyword-group-title">
                    ${typeBadge}
                    <span class="keyword-count-badge">${group.keywords.length}个关键词 × ${group.items.length}个应用 = ${group.keywords.length * group.items.length}条配置</span>
        </div>
        </div>
            ${replyDisplay}
            <div class="keyword-group-content">
                <div class="keyword-section">
                    <div class="section-title"><i class="bi bi-tags"></i> 触发关键词</div>
                    <div class="chips-container">
                        ${keywordsList}
                    </div>
                </div>
                <div class="item-section">
                    <div class="section-title"><i class="bi bi-box-seam"></i> 应用范围</div>
                    <div class="chips-container">
                        ${itemsList}
                    </div>
                </div>
        </div>
    `;
        
        container.appendChild(groupItem);
    });

    console.log('关键词列表渲染完成');
}

// 按回复内容分组关键词
function groupKeywordsByReply(keywords) {
    const groupMap = new Map();
    
    keywords.forEach((item, index) => {
        // 使用回复内容+类型+图片URL作为分组键
        const key = `${item.type || 'text'}:${item.reply || ''}:${item.image_url || ''}`;
        
        if (!groupMap.has(key)) {
            groupMap.set(key, {
                id: `group_${groupMap.size}`,
                type: item.type || 'text',
                reply: item.reply || '',
                image_url: item.image_url || '',
                keywords: [],
                items: [],
                indices: [] // 保存原始索引
            });
        }
        
        const group = groupMap.get(key);
        
        // 添加关键词（去重）
        if (!group.keywords.includes(item.keyword)) {
            group.keywords.push(item.keyword);
        }
        
        // 添加商品（去重）
        const itemId = item.item_id || '';
        const existingItem = group.items.find(i => (i.item_id || '') === itemId);
        if (!existingItem) {
            group.items.push({
                item_id: itemId,
                item_title: item.item_title || '',  // 添加商品名称
                indices: [index]
            });
        } else {
            existingItem.indices.push(index);
        }
        
        // 记录原始索引
        group.indices.push(index);
    });
    
    return Array.from(groupMap.values());
}

// 获取商品名称（截取前30个字符）
function getItemName(itemId, itemTitle) {
    if (!itemId) return '';
    
    // 优先使用传入的商品名称
    if (itemTitle && itemTitle.trim()) {
        const name = itemTitle.trim();
        // 截取前30个字符
        return name.length > 30 ? name.substring(0, 30) + '...' : name;
    }
    
    // 从商品列表中查找商品名称
    const itemsSelect = document.getElementById('newItemIdSelect');
    if (itemsSelect) {
        const option = Array.from(itemsSelect.options).find(opt => opt.value === itemId);
        if (option && option.textContent) {
            // 提取商品名称（格式：itemId - 商品名称）
            const parts = option.textContent.split(' - ');
            if (parts.length > 1) {
                const name = parts.slice(1).join(' - ');
                // 截取前30个字符
                return name.length > 30 ? name.substring(0, 30) + '...' : name;
            }
        }
    }
    
    return '未知商品';
}

// 聚焦到关键词输入框
function focusKeywordInput() {
    document.getElementById('newKeyword').focus();
}

// 编辑分组回复内容（就地编辑）
function editGroupReply(groupIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups[groupIndex];

    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }

    const container = document.getElementById(`reply-display-${groupIndex}`);
    if (!container) return;

    // 转义HTML用于textarea
    const replyText = group.reply || '';

    container.innerHTML = `
        <strong>回复内容：</strong>
        <div class="reply-edit-area">
            <textarea class="reply-edit-textarea" id="reply-edit-input-${groupIndex}" rows="3" placeholder="请输入回复内容">${replyText}</textarea>
            <div class="reply-edit-actions">
                <button class="reply-cancel-btn" onclick="cancelGroupReplyEdit(${groupIndex})">
                    <i class="bi bi-x-lg"></i> 取消
                </button>
                <button class="reply-save-btn" onclick="saveGroupReply(${groupIndex})">
                    <i class="bi bi-check-lg"></i> 保存
                </button>
            </div>
        </div>
    `;

    // 聚焦并将光标移到末尾
    const textarea = document.getElementById(`reply-edit-input-${groupIndex}`);
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
}

// 取消编辑分组回复
function cancelGroupReplyEdit(groupIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    renderKeywordsList(keywords);
}

// 保存分组回复内容
async function saveGroupReply(groupIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups[groupIndex];

    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }

    const textarea = document.getElementById(`reply-edit-input-${groupIndex}`);
    if (!textarea) return;

    const newReply = textarea.value.trim();

    // 更新所有属于该分组的关键词回复内容
    const updatedKeywords = keywords.map((item, index) => {
        if (group.indices.includes(index)) {
            return { ...item, reply: newReply };
        }
        return item;
    });

    // 提取文本类型的关键词用于保存
    const textKeywords = updatedKeywords.filter(item => (item.type || 'text') === 'text');

    try {
        toggleLoading(true);

        const response = await fetch(`${apiBase}/keywords-with-item-id/${currentCookieId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                keywords: textKeywords
            })
        });

        if (response.ok) {
            showToast(`回复内容已更新（影响${group.indices.length}条配置）`, 'success');
            await refreshKeywordsList();
        } else {
            const errorText = await response.text();
            console.error('更新回复内容失败:', errorText);
            showToast('更新回复内容失败', 'danger');
        }
    } catch (error) {
        console.error('更新回复内容失败:', error);
        showToast('更新回复内容失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 编辑关键词 - 改进版本
function editKeyword(index) {
    const keywords = keywordsData[currentCookieId] || [];
    const keyword = keywords[index];

    if (!keyword) {
    showToast('关键词不存在', 'warning');
    return;
    }

    // 将关键词信息填入输入框
    document.getElementById('newKeyword').value = keyword.keyword;
    document.getElementById('newReply').value = keyword.reply;

    // 设置商品ID选择框
    const selectElement = document.getElementById('newItemIdSelect');
    if (selectElement) {
    selectElement.value = keyword.item_id || '';
    }

    // 设置编辑模式标识
    window.editingIndex = index;
    window.originalKeyword = keyword.keyword;
    window.originalItemId = keyword.item_id || '';

    // 更新按钮文本和样式
    const addBtn = document.querySelector('.add-btn');
    addBtn.innerHTML = '<i class="bi bi-check-lg"></i>更新';
    addBtn.style.background = 'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)';

    // 显示取消按钮
    showCancelEditButton();

    // 聚焦到关键词输入框并选中文本
    setTimeout(() => {
    const keywordInput = document.getElementById('newKeyword');
    keywordInput.focus();
    keywordInput.select();
    }, 100);

    showToast('📝 编辑模式：修改后点击"更新"按钮保存', 'info');
}

// 显示取消编辑按钮
function showCancelEditButton() {
    // 检查是否已存在取消按钮
    if (document.getElementById('cancelEditBtn')) {
    return;
    }

    const addBtn = document.querySelector('.add-btn');
    const cancelBtn = document.createElement('button');
    cancelBtn.id = 'cancelEditBtn';
    cancelBtn.className = 'btn btn-outline-secondary';
    cancelBtn.style.marginLeft = '0.5rem';
    cancelBtn.innerHTML = '<i class="bi bi-x-lg"></i>取消';
    cancelBtn.onclick = cancelEdit;

    addBtn.parentNode.appendChild(cancelBtn);
}

// 取消编辑
function cancelEdit() {
    // 清空输入框
    document.getElementById('newKeyword').value = '';
    document.getElementById('newReply').value = '';

    // 清空商品ID选择框
    const selectElement = document.getElementById('newItemIdSelect');
    if (selectElement) {
    selectElement.value = '';
    }

    // 重置编辑状态
    delete window.editingIndex;
    delete window.originalKeyword;
    delete window.originalItemId;

    // 恢复添加按钮
    const addBtn = document.querySelector('.add-btn');
    addBtn.innerHTML = '<i class="bi bi-plus-lg"></i>添加';
    addBtn.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';

    // 移除取消按钮
    const cancelBtn = document.getElementById('cancelEditBtn');
    if (cancelBtn) {
    cancelBtn.remove();
    }

    showToast('已取消编辑', 'info');
}

// 删除关键词
async function deleteKeyword(cookieId, index) {
    if (!confirm('确定要删除这个关键词吗？')) {
    return;
    }

    try {
    toggleLoading(true);

    // 使用新的删除API
    const response = await fetch(`${apiBase}/keywords/${cookieId}/${index}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('关键词删除成功', 'success');
        // 只刷新关键词列表，不重新加载整个界面
        await refreshKeywordsList();
    } else {
        const errorText = await response.text();
        console.error('关键词删除失败:', errorText);
        showToast('关键词删除失败', 'danger');
    }
    } catch (error) {
    console.error('删除关键词失败:', error);
    showToast('删除关键词删除失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 删除特定关键词（删除该关键词在所有商品中的配置）
async function deleteSpecificKeyword(groupId, keywordIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups.find(g => g.id === groupId);
    
    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }
    
    const targetKeyword = group.keywords[keywordIndex];
    if (!confirm(`确定要删除关键词 "${targetKeyword}" 在所有商品中的配置吗？`)) {
        return;
    }
    
    try {
        toggleLoading(true);
        
        // 找到所有需要删除的索引（从后往前删除，避免索引变化）
        const indicesToDelete = [];
        keywords.forEach((item, index) => {
            if (item.keyword === targetKeyword && 
                (item.type || 'text') === group.type &&
                (item.reply || '') === group.reply &&
                (item.image_url || '') === group.image_url) {
                indicesToDelete.push(index);
            }
        });
        
        // 从后往前删除
        indicesToDelete.sort((a, b) => b - a);
        
        for (const index of indicesToDelete) {
            const response = await fetch(`${apiBase}/keywords/${currentCookieId}/${index}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            
            if (!response.ok) {
                throw new Error('删除失败');
            }
        }
        
        showToast(`✅ 关键词 "${targetKeyword}" 已删除（${indicesToDelete.length}条配置）`, 'success');
        await refreshKeywordsList();
        
    } catch (error) {
        console.error('删除关键词失败:', error);
        showToast('删除关键词失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 删除特定商品的配置（删除该商品下所有关键词的配置）
async function deleteSpecificItem(groupId, itemIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups.find(g => g.id === groupId);
    
    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }
    
    const targetItem = group.items[itemIndex];
    const itemId = targetItem.item_id || '';
    const itemName = itemId ? `商品 ${itemId} - ${getItemName(itemId, targetItem.item_title)}` : '通用关键词（所有商品）';
    
    if (!confirm(`确定要删除 "${itemName}" 的所有关键词配置吗？\n将删除该商品下的 ${group.keywords.length} 个关键词。`)) {
        return;
    }
    
    try {
        toggleLoading(true);
        
        // 找到所有需要删除的索引
        const indicesToDelete = [];
        keywords.forEach((item, index) => {
            if ((item.item_id || '') === itemId &&
                (item.type || 'text') === group.type &&
                (item.reply || '') === group.reply &&
                (item.image_url || '') === group.image_url) {
                indicesToDelete.push(index);
            }
        });
        
        // 从后往前删除
        indicesToDelete.sort((a, b) => b - a);
        
        for (const index of indicesToDelete) {
            const response = await fetch(`${apiBase}/keywords/${currentCookieId}/${index}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            
            if (!response.ok) {
                throw new Error('删除失败');
            }
        }
        
        showToast(`✅ ${itemName} 的配置已删除（${indicesToDelete.length}条）`, 'success');
        await refreshKeywordsList();
        
    } catch (error) {
        console.error('删除商品配置失败:', error);
        showToast('删除商品配置失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 显示/隐藏加载动画
function toggleLoading(show) {
    const loadingEl = document.getElementById('loading');
    if (!loadingEl) return;

    if (show) {
        loadingRequestCount += 1;

        if (loadingRequestCount === 1) {
            if (loadingShowTimer) {
                clearTimeout(loadingShowTimer);
            }

            loadingShowTimer = setTimeout(() => {
                if (loadingRequestCount > 0) {
                    loadingEl.classList.remove('d-none');
                }
                loadingShowTimer = null;
            }, LOADING_SHOW_DELAY);
        }
        return;
    }

    if (loadingRequestCount > 0) {
        loadingRequestCount -= 1;
    }

    if (loadingRequestCount === 0) {
        if (loadingShowTimer) {
            clearTimeout(loadingShowTimer);
            loadingShowTimer = null;
        }
        loadingEl.classList.add('d-none');
    }
}

// ================================
// 通用工具函数
// ================================

// 显示提示消息
function showToast(message, type = 'success') {
    // 将 'error' 类型映射为 'danger'，因为 Bootstrap 使用 'danger' 作为错误类型
    if (type === 'error') {
        type = 'danger';
    }
    
    let toastContainer = document.querySelector('.toast-container');
    
    // 如果 toast 容器不存在，创建一个
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }
    
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');

    const toastRow = document.createElement('div');
    toastRow.className = 'd-flex';

    const toastBody = document.createElement('div');
    toastBody.className = 'toast-body';
    toastBody.style.whiteSpace = 'pre-line';
    toastBody.textContent = String(message ?? '');

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'btn-close btn-close-white me-2 m-auto';
    closeButton.setAttribute('data-bs-dismiss', 'toast');
    closeButton.setAttribute('aria-label', 'Close');

    toastRow.appendChild(toastBody);
    toastRow.appendChild(closeButton);
    toast.appendChild(toastRow);

    toastContainer.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast, { delay: 5000 });  // 增加显示时间到5秒
    bsToast.show();

    // 自动移除
    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });
}

// 错误处理
async function handleApiError(err) {
    console.error(err);
    showToast(err.message || '操作失败', 'danger');
    toggleLoading(false);
}

// API请求包装
async function fetchJSON(url, opts = {}) {
    toggleLoading(true);
    try {
    // 添加认证头
    const token = getAuthToken();
    if (token) {
        opts.headers = opts.headers || {};
        opts.headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(url, opts);
    if (res.status === 401) {
        // 未授权，跳转到登录页面
        localStorage.removeItem('auth_token');
        window.location.href = '/';
        return;
    }
    if (!res.ok) {
        let errorMessage = `HTTP ${res.status}`;
        try {
        const errorText = await res.text();
        if (errorText) {
            // 尝试解析JSON错误信息
            try {
            const errorJson = JSON.parse(errorText);
            errorMessage = errorJson.detail || errorJson.message || errorText;
            } catch {
            errorMessage = errorText;
            }
        }
        } catch {
        errorMessage = `HTTP ${res.status} ${res.statusText}`;
        }
        throw new Error(errorMessage);
    }
    const data = await res.json();
    toggleLoading(false);
    return data;
    } catch (err) {
    handleApiError(err);
    throw err;
    }
}

// ================================
// 【消息过滤菜单】相关功能
// ================================

async function loadMessageFiltersPage() {
    await loadMessageFilterAccountOptions();
    await loadMessageFilters(messageFilterState.page || 1);
}

async function loadMessageFilterAccountOptions(force = false) {
    const accountSelect = document.getElementById('messageFilterCookieId');
    if (!accountSelect) return;
    if (messageFilterState.accountsLoaded && !force) return;

    try {
        const currentValue = accountSelect.value;
        const accounts = await fetchJSON(`${apiBase}/cookies/details`);
        const safeAccounts = Array.isArray(accounts) ? accounts : [];
        accountSelect.innerHTML = '<option value="">全部账号</option>' + safeAccounts.map(account => {
            const accountId = String(account.id || '').trim();
            const remark = String(account.remark || '').trim();
            const label = remark ? `${accountId}（${remark}）` : accountId;
            return `<option value="${escapeHtml(accountId)}">${escapeHtml(label)}</option>`;
        }).join('');
        if (currentValue && safeAccounts.some(account => String(account.id || '') === currentValue)) {
            accountSelect.value = currentValue;
        }
        messageFilterState.accountsLoaded = true;
    } catch (error) {
        console.error('加载消息过滤账号选项失败:', error);
    }
}

function handleMessageFilterKeydown(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        loadMessageFilters(1);
    }
}

async function loadMessageFilters(page = 1) {
    const tableBody = document.getElementById('messageFilterTableBody');
    if (!tableBody) return;

    const pageSizeSelect = document.getElementById('messageFilterPageSize');
    const pageSize = Math.max(1, parseInt(pageSizeSelect?.value || '20', 10) || 20);
    const safePage = Math.max(1, parseInt(page, 10) || 1);
    const params = new URLSearchParams({
        page: String(safePage),
        page_size: String(pageSize)
    });
    const keyword = document.getElementById('messageFilterKeyword')?.value?.trim();
    if (keyword) params.set('keyword', keyword);

    try {
        const result = await fetchJSON(`${apiBase}/api/message-filters?${params.toString()}`);
        const records = Array.isArray(result?.data) ? result.data : [];
        messageFilterState.page = Number(result?.page || safePage);
        messageFilterState.pageSize = Number(result?.page_size || pageSize);
        messageFilterState.total = Number(result?.total || 0);
        renderMessageFilters(records);
        renderMessageFilterPagination();
    } catch (error) {
        console.error('加载消息过滤规则失败:', error);
        tableBody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4 text-danger">
                    <i class="bi bi-exclamation-triangle fs-1 d-block mb-3"></i>
                    加载消息过滤规则失败
                </td>
            </tr>
        `;
    }
}

function getMessageFilterScopeBadge(scope) {
    const normalizedScope = String(scope || 'user');
    const config = {
        item: { text: '商品级', cls: 'bg-warning text-dark' },
        account: { text: '账号级', cls: 'bg-info text-dark' },
        user: { text: '用户级', cls: 'bg-secondary' }
    }[normalizedScope] || { text: normalizedScope || '未知', cls: 'bg-secondary' };
    return `<span class="badge ${config.cls}">${escapeHtml(config.text)}</span>`;
}

function getMessageFilterMatchTypeLabel(matchType) {
    return ({
        contains: '包含',
        exact: '完全',
        regex: '正则'
    }[String(matchType || 'contains')] || '包含');
}

function getMessageFilterSourceLabel(source) {
    return ({
        user: '客户',
        system: '系统',
        all: '全部'
    }[String(source || 'user')] || '客户');
}

function getMessageFilterActionsHtml(record) {
    const actions = [];
    if (record?.action_skip_auto_reply) actions.push('跳过自动回复');
    if (record?.action_skip_ai_reply) actions.push('跳过AI');
    const pauseMinutes = Number(record?.action_pause_minutes || 0);
    if (pauseMinutes > 0) actions.push(`暂停${pauseMinutes}分钟`);
    if (record?.action_notify) actions.push('通知人工');
    if (actions.length === 0) return '<span class="text-muted small">仅记录</span>';
    return actions.map(action => `<span class="badge bg-light text-dark border me-1 mb-1">${escapeHtml(action)}</span>`).join('');
}

function renderMessageFilters(records) {
    const tableBody = document.getElementById('messageFilterTableBody');
    const totalText = document.getElementById('messageFilterTotalText');
    if (!tableBody) return;

    if (totalText) {
        totalText.textContent = `共 ${messageFilterState.total || 0} 条`;
    }

    if (!Array.isArray(records) || records.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="7" class="text-center py-4 text-muted">
                    <i class="bi bi-funnel fs-1 d-block mb-3"></i>
                    暂无过滤规则
                </td>
            </tr>
        `;
        return;
    }

    tableBody.innerHTML = records.map(record => {
        const ruleId = Number(record.id || 0);
        const name = String(record.name || '').trim();
        const patterns = Array.isArray(record.patterns) ? record.patterns : [];
        const patternPreview = patterns.slice(0, 3).join(' / ');
        const cookieId = String(record.cookie_id || '').trim();
        const itemId = String(record.item_id || '').trim();
        const enabled = Boolean(record.is_enabled);
        const updatedAt = formatDateTime(record.updated_at || record.created_at || '');
        const targetParts = [];
        targetParts.push(cookieId ? `账号 ${cookieId}` : '全部账号');
        if (itemId) targetParts.push(`商品 ${itemId}`);
        const encodedRecord = encodeURIComponent(JSON.stringify(record));
        return `
            <tr>
                <td>
                    ${getMessageFilterScopeBadge(record.scope)}
                    <div class="small text-muted mt-1">${targetParts.map(part => escapeHtml(part)).join('<br>')}</div>
                </td>
                <td>
                    <div class="fw-semibold" title="${escapeHtml(name)}">${escapeHtml(name || '-')}</div>
                    <div class="small text-muted">${getMessageFilterSourceLabel(record.message_source)}消息</div>
                </td>
                <td style="max-width: 220px;">
                    <div class="small"><span class="badge bg-light text-dark border">${getMessageFilterMatchTypeLabel(record.match_type)}</span></div>
                    <div class="text-truncate mt-1" title="${escapeHtml(patterns.join('\n'))}">${escapeHtml(patternPreview || '-')}</div>
                </td>
                <td style="max-width: 260px;">${getMessageFilterActionsHtml(record)}</td>
                <td>
                    <div class="form-check form-switch m-0" title="${enabled ? '点击禁用' : '点击启用'}">
                        <input class="form-check-input" type="checkbox" ${enabled ? 'checked' : ''} onchange="toggleMessageFilter(${ruleId}, this.checked)">
                    </div>
                </td>
                <td><small class="text-muted text-nowrap">${escapeHtml(updatedAt)}</small></td>
                <td>
                    <div class="btn-group btn-group-sm" role="group">
                        <button type="button" class="btn btn-outline-primary" onclick="editMessageFilterRule('${encodedRecord}')" title="编辑">
                            <i class="bi bi-pencil"></i>
                        </button>
                        <button type="button" class="btn btn-outline-danger" onclick="deleteMessageFilter(${ruleId})" title="删除">
                            <i class="bi bi-trash"></i>
                        </button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function renderMessageFilterPagination() {
    const pagination = document.getElementById('messageFilterPagination');
    const pageText = document.getElementById('messageFilterPageText');
    if (!pagination) return;

    const pageSize = Math.max(1, Number(messageFilterState.pageSize || 20));
    const total = Math.max(0, Number(messageFilterState.total || 0));
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const currentPage = Math.min(Math.max(1, Number(messageFilterState.page || 1)), totalPages);

    if (currentPage !== messageFilterState.page && total > 0) {
        loadMessageFilters(currentPage);
        return;
    }

    if (pageText) {
        pageText.textContent = `第 ${currentPage} / ${totalPages} 页`;
    }

    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, startPage + 4);
    const buttons = [];
    const addButton = (label, targetPage, disabled = false, active = false, title = '') => {
        buttons.push(`
            <button type="button" class="btn btn-sm ${active ? 'btn-primary' : 'btn-outline-secondary'}" ${disabled ? 'disabled' : ''} onclick="loadMessageFilters(${targetPage})" title="${escapeHtml(title || label)}">
                ${label}
            </button>
        `);
    };

    addButton('<i class="bi bi-chevron-left"></i>', currentPage - 1, currentPage <= 1, false, '上一页');
    for (let page = startPage; page <= endPage; page += 1) {
        addButton(String(page), page, false, page === currentPage);
    }
    addButton('<i class="bi bi-chevron-right"></i>', currentPage + 1, currentPage >= totalPages, false, '下一页');
    pagination.innerHTML = buttons.join('');
}

function getMessageFilterPayload() {
    const name = document.getElementById('messageFilterName')?.value?.trim() || '';
    const patterns = document.getElementById('messageFilterPatterns')?.value?.trim() || '';
    if (!name) {
        showToast('请填写规则名称', 'warning');
        return null;
    }
    if (!patterns) {
        showToast('请填写匹配内容', 'warning');
        return null;
    }
    const pauseMinutes = Math.max(0, parseInt(document.getElementById('messageFilterPauseMinutes')?.value || '0', 10) || 0);
    return {
        name,
        cookie_id: document.getElementById('messageFilterCookieId')?.value?.trim() || null,
        item_id: document.getElementById('messageFilterItemId')?.value?.trim() || null,
        match_type: document.getElementById('messageFilterMatchType')?.value || 'contains',
        message_source: document.getElementById('messageFilterSource')?.value || 'user',
        patterns,
        is_enabled: Boolean(document.getElementById('messageFilterEnabled')?.checked),
        action_skip_auto_reply: Boolean(document.getElementById('messageFilterSkipAutoReply')?.checked),
        action_skip_ai_reply: Boolean(document.getElementById('messageFilterSkipAiReply')?.checked),
        action_pause_minutes: Math.min(pauseMinutes, 1440),
        action_notify: Boolean(document.getElementById('messageFilterNotify')?.checked)
    };
}

async function saveMessageFilterRule() {
    const payload = getMessageFilterPayload();
    if (!payload) return;

    const editingId = Number(messageFilterState.editingId || 0);
    const url = editingId > 0
        ? `${apiBase}/api/message-filters/${editingId}`
        : `${apiBase}/api/message-filters`;
    const method = editingId > 0 ? 'PUT' : 'POST';

    try {
        const result = await fetchJSON(url, {
            method,
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        showToast(result?.message || '消息过滤规则已保存', 'success');
        resetMessageFilterForm();
        await loadMessageFilters(editingId > 0 ? (messageFilterState.page || 1) : 1);
    } catch (error) {
        console.error('保存消息过滤规则失败:', error);
    }
}

function editMessageFilterRule(encodedRecord) {
    try {
        const record = JSON.parse(decodeURIComponent(encodedRecord));
        messageFilterState.editingId = Number(record.id || 0);
        const formTitle = document.getElementById('messageFilterFormTitle');
        if (formTitle) formTitle.textContent = '编辑过滤规则';
        const fields = {
            messageFilterRuleId: record.id || '',
            messageFilterName: record.name || '',
            messageFilterCookieId: record.cookie_id || '',
            messageFilterItemId: record.item_id || '',
            messageFilterMatchType: record.match_type || 'contains',
            messageFilterSource: record.message_source || 'user',
            messageFilterPatterns: Array.isArray(record.patterns) ? record.patterns.join('\n') : (record.patterns_text || ''),
            messageFilterPauseMinutes: record.action_pause_minutes || 0
        };
        Object.entries(fields).forEach(([id, value]) => {
            const element = document.getElementById(id);
            if (element) element.value = value;
        });
        const checks = {
            messageFilterEnabled: record.is_enabled,
            messageFilterSkipAutoReply: record.action_skip_auto_reply,
            messageFilterSkipAiReply: record.action_skip_ai_reply,
            messageFilterNotify: record.action_notify
        };
        Object.entries(checks).forEach(([id, checked]) => {
            const element = document.getElementById(id);
            if (element) element.checked = Boolean(checked);
        });
        document.getElementById('messageFilterName')?.focus();
    } catch (error) {
        console.error('编辑消息过滤规则失败:', error);
        showToast('加载规则失败', 'danger');
    }
}

function resetMessageFilterForm() {
    const form = document.getElementById('messageFilterForm');
    if (form) form.reset();
    messageFilterState.editingId = null;
    const ruleId = document.getElementById('messageFilterRuleId');
    if (ruleId) ruleId.value = '';
    const formTitle = document.getElementById('messageFilterFormTitle');
    if (formTitle) formTitle.textContent = '新增过滤规则';
    const enabled = document.getElementById('messageFilterEnabled');
    const skipAuto = document.getElementById('messageFilterSkipAutoReply');
    const skipAi = document.getElementById('messageFilterSkipAiReply');
    const notify = document.getElementById('messageFilterNotify');
    const pause = document.getElementById('messageFilterPauseMinutes');
    const matchType = document.getElementById('messageFilterMatchType');
    const source = document.getElementById('messageFilterSource');
    if (enabled) enabled.checked = true;
    if (skipAuto) skipAuto.checked = true;
    if (skipAi) skipAi.checked = false;
    if (notify) notify.checked = false;
    if (pause) pause.value = '0';
    if (matchType) matchType.value = 'contains';
    if (source) source.value = 'user';
}

async function toggleMessageFilter(ruleId, isEnabled) {
    try {
        await fetchJSON(`${apiBase}/api/message-filters/${ruleId}/toggle`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_enabled: Boolean(isEnabled) })
        });
        showToast(isEnabled ? '规则已启用' : '规则已禁用', 'success');
    } catch (error) {
        console.error('更新消息过滤规则状态失败:', error);
        await loadMessageFilters(messageFilterState.page || 1);
    }
}

async function deleteMessageFilter(ruleId) {
    if (!confirm('确定删除这条消息过滤规则吗？')) return;
    try {
        const result = await fetchJSON(`${apiBase}/api/message-filters/${ruleId}`, {
            method: 'DELETE'
        });
        showToast(result?.message || '消息过滤规则已删除', 'success');
        if (Number(messageFilterState.editingId || 0) === Number(ruleId || 0)) {
            resetMessageFilterForm();
        }
        await loadMessageFilters(messageFilterState.page || 1);
    } catch (error) {
        console.error('删除消息过滤规则失败:', error);
    }
}

function resetMessageFilterSearch() {
    const keyword = document.getElementById('messageFilterKeyword');
    if (keyword) keyword.value = '';
    loadMessageFilters(1);
}

// ================================
// 【黑名单管理菜单】相关功能
// ================================

async function loadBlacklistPage() {
    await loadBlacklistAccountOptions();
    await loadPersonalBlacklist(blacklistState.page || 1);
}

async function loadBlacklistAccountOptions(force = false) {
    const accountSelect = document.getElementById('blacklistCookieId');
    if (!accountSelect) return;
    if (blacklistState.accountsLoaded && !force) return;

    try {
        const currentValue = accountSelect.value;
        const accounts = await fetchJSON(`${apiBase}/cookies/details`);
        const safeAccounts = Array.isArray(accounts) ? accounts : [];
        accountSelect.innerHTML = '<option value="">全部账号</option>' + safeAccounts.map(account => {
            const accountId = String(account.id || '').trim();
            const remark = String(account.remark || '').trim();
            const label = remark ? `${accountId}（${remark}）` : accountId;
            return `<option value="${escapeHtml(accountId)}">${escapeHtml(label)}</option>`;
        }).join('');
        if (currentValue && safeAccounts.some(account => String(account.id || '') === currentValue)) {
            accountSelect.value = currentValue;
        }
        blacklistState.accountsLoaded = true;
    } catch (error) {
        console.error('加载黑名单账号选项失败:', error);
    }
}

function handleBlacklistFilterKeydown(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        loadPersonalBlacklist(1);
    }
}

async function loadPersonalBlacklist(page = 1) {
    const tableBody = document.getElementById('blacklistTableBody');
    if (!tableBody) return;

    const pageSizeSelect = document.getElementById('blacklistPageSize');
    const pageSize = Math.max(1, parseInt(pageSizeSelect?.value || '20', 10) || 20);
    const safePage = Math.max(1, parseInt(page, 10) || 1);
    const params = new URLSearchParams({
        page: String(safePage),
        page_size: String(pageSize)
    });

    const buyerId = document.getElementById('blacklistFilterBuyerId')?.value?.trim();
    const buyerNick = document.getElementById('blacklistFilterBuyerNick')?.value?.trim();
    if (buyerId) params.set('buyer_id', buyerId);
    if (buyerNick) params.set('buyer_nick', buyerNick);

    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal?${params.toString()}`);
        const records = Array.isArray(result?.data) ? result.data : [];
        blacklistState.page = Number(result?.page || safePage);
        blacklistState.pageSize = Number(result?.page_size || pageSize);
        blacklistState.total = Number(result?.total || 0);
        renderPersonalBlacklist(records);
        renderBlacklistPagination();
    } catch (error) {
        console.error('加载个人黑名单失败:', error);
        tableBody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-4 text-danger">
                    <i class="bi bi-exclamation-triangle fs-1 d-block mb-3"></i>
                    加载黑名单失败
                </td>
            </tr>
        `;
    }
}

function getBlacklistScopeBadge(scope) {
    const normalizedScope = String(scope || 'user');
    const config = {
        item: { text: '商品级', cls: 'bg-warning text-dark' },
        account: { text: '账号级', cls: 'bg-info text-dark' },
        user: { text: '用户级', cls: 'bg-secondary' }
    }[normalizedScope] || { text: normalizedScope || '未知', cls: 'bg-secondary' };
    return `<span class="badge ${config.cls}">${escapeHtml(config.text)}</span>`;
}

function getBlacklistTargetHtml(record) {
    const cookieId = String(record?.cookie_id || '').trim();
    const itemId = String(record?.item_id || '').trim();
    const parts = [];
    parts.push(cookieId ? `账号 ${cookieId}` : '全部账号');
    if (itemId) parts.push(`商品 ${itemId}`);
    return parts.map(part => `<div class="small text-muted text-nowrap" title="${escapeHtml(part)}">${escapeHtml(part)}</div>`).join('');
}

function renderPersonalBlacklist(records) {
    const tableBody = document.getElementById('blacklistTableBody');
    const totalText = document.getElementById('blacklistTotalText');
    const selectAll = document.getElementById('blacklistSelectAll');
    if (!tableBody) return;

    if (totalText) {
        totalText.textContent = `共 ${blacklistState.total || 0} 条`;
    }
    if (selectAll) {
        selectAll.checked = false;
        selectAll.indeterminate = false;
    }

    if (!Array.isArray(records) || records.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-4 text-muted">
                    <i class="bi bi-person-x fs-1 d-block mb-3"></i>
                    暂无黑名单记录
                </td>
            </tr>
        `;
        updateBlacklistBatchDeleteState();
        return;
    }

    tableBody.innerHTML = records.map(record => {
        const recordId = Number(record.id || 0);
        const buyerId = String(record.buyer_id || '').trim();
        const buyerNick = String(record.buyer_nick || '').trim();
        const reason = String(record.reason || '').trim();
        const enabled = Boolean(record.is_enabled);
        const createdAt = formatDateTime(record.created_at || record.updated_at || '');
        return `
            <tr>
                <td>
                    <input class="form-check-input blacklist-row-check" type="checkbox" data-id="${recordId}" onchange="updateBlacklistBatchDeleteState()">
                </td>
                <td>${getBlacklistScopeBadge(record.scope)}</td>
                <td>
                    <div class="fw-semibold" title="${escapeHtml(buyerId)}">${escapeHtml(buyerId)}</div>
                    ${buyerNick ? `<div class="small text-muted" title="${escapeHtml(buyerNick)}">${escapeHtml(buyerNick)}</div>` : ''}
                </td>
                <td>${getBlacklistTargetHtml(record)}</td>
                <td style="max-width: 220px;">
                    <span class="d-inline-block text-truncate" style="max-width: 100%;" title="${escapeHtml(reason)}">${escapeHtml(reason || '-')}</span>
                </td>
                <td>
                    <div class="form-check form-switch m-0" title="${enabled ? '点击禁用' : '点击启用'}">
                        <input class="form-check-input" type="checkbox" ${enabled ? 'checked' : ''} onchange="togglePersonalBlacklist(${recordId}, this.checked)">
                    </div>
                </td>
                <td><small class="text-muted text-nowrap">${escapeHtml(createdAt)}</small></td>
                <td>
                    <button type="button" class="btn btn-outline-danger btn-sm" onclick="deletePersonalBlacklist(${recordId})" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
    updateBlacklistBatchDeleteState();
}

function renderBlacklistPagination() {
    const pagination = document.getElementById('blacklistPagination');
    const pageText = document.getElementById('blacklistPageText');
    if (!pagination) return;

    const pageSize = Math.max(1, Number(blacklistState.pageSize || 20));
    const total = Math.max(0, Number(blacklistState.total || 0));
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const currentPage = Math.min(Math.max(1, Number(blacklistState.page || 1)), totalPages);

    if (currentPage !== blacklistState.page && total > 0) {
        loadPersonalBlacklist(currentPage);
        return;
    }

    if (pageText) {
        pageText.textContent = `第 ${currentPage} / ${totalPages} 页`;
    }

    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, startPage + 4);
    const buttons = [];
    const addButton = (label, targetPage, disabled = false, active = false, title = '') => {
        buttons.push(`
            <button type="button" class="btn btn-sm ${active ? 'btn-primary' : 'btn-outline-secondary'}" ${disabled ? 'disabled' : ''} onclick="loadPersonalBlacklist(${targetPage})" title="${escapeHtml(title || label)}">
                ${label}
            </button>
        `);
    };

    addButton('<i class="bi bi-chevron-left"></i>', currentPage - 1, currentPage <= 1, false, '上一页');
    for (let page = startPage; page <= endPage; page += 1) {
        addButton(String(page), page, false, page === currentPage);
    }
    addButton('<i class="bi bi-chevron-right"></i>', currentPage + 1, currentPage >= totalPages, false, '下一页');
    pagination.innerHTML = buttons.join('');
}

async function createPersonalBlacklist() {
    const buyerIds = document.getElementById('blacklistBuyerIds')?.value?.trim() || '';
    if (!buyerIds) {
        showToast('请填写买家ID', 'warning');
        return;
    }

    const payload = {
        buyer_ids: buyerIds,
        cookie_id: document.getElementById('blacklistCookieId')?.value?.trim() || null,
        item_id: document.getElementById('blacklistItemId')?.value?.trim() || null,
        buyer_nick: document.getElementById('blacklistBuyerNick')?.value?.trim() || '',
        reason: document.getElementById('blacklistReason')?.value?.trim() || '',
        is_enabled: Boolean(document.getElementById('blacklistEnabled')?.checked)
    };

    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        showToast(result?.message || '黑名单已保存', 'success');
        resetPersonalBlacklistForm();
        await loadPersonalBlacklist(1);
    } catch (error) {
        console.error('新增个人黑名单失败:', error);
    }
}

function resetPersonalBlacklistForm() {
    const form = document.getElementById('personalBlacklistForm');
    if (form) form.reset();
    const enabled = document.getElementById('blacklistEnabled');
    if (enabled) enabled.checked = true;
}

async function togglePersonalBlacklist(recordId, isEnabled) {
    try {
        await fetchJSON(`${apiBase}/api/blacklist/personal/${recordId}/toggle`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_enabled: Boolean(isEnabled) })
        });
        showToast(isEnabled ? '黑名单已启用' : '黑名单已禁用', 'success');
    } catch (error) {
        console.error('更新黑名单状态失败:', error);
        await loadPersonalBlacklist(blacklistState.page || 1);
    }
}

async function deletePersonalBlacklist(recordId) {
    if (!confirm('确定删除这条黑名单记录吗？')) return;
    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal/${recordId}`, {
            method: 'DELETE'
        });
        showToast(result?.message || '黑名单已删除', 'success');
        await loadPersonalBlacklist(blacklistState.page || 1);
    } catch (error) {
        console.error('删除个人黑名单失败:', error);
    }
}

function getSelectedBlacklistIds() {
    return Array.from(document.querySelectorAll('.blacklist-row-check:checked'))
        .map(checkbox => parseInt(checkbox.dataset.id || '0', 10))
        .filter(id => id > 0);
}

function toggleBlacklistSelectAll(checked) {
    document.querySelectorAll('.blacklist-row-check').forEach(checkbox => {
        checkbox.checked = Boolean(checked);
    });
    updateBlacklistBatchDeleteState();
}

function updateBlacklistBatchDeleteState() {
    const selectedIds = getSelectedBlacklistIds();
    const batchButton = document.getElementById('blacklistBatchDeleteBtn');
    const selectAll = document.getElementById('blacklistSelectAll');
    const rowChecks = Array.from(document.querySelectorAll('.blacklist-row-check'));

    if (batchButton) {
        batchButton.disabled = selectedIds.length === 0;
        batchButton.innerHTML = selectedIds.length > 0
            ? `<i class="bi bi-trash me-1"></i>批量删除 (${selectedIds.length})`
            : '<i class="bi bi-trash me-1"></i>批量删除';
    }

    if (selectAll) {
        selectAll.checked = rowChecks.length > 0 && selectedIds.length === rowChecks.length;
        selectAll.indeterminate = selectedIds.length > 0 && selectedIds.length < rowChecks.length;
    }
}

async function batchDeletePersonalBlacklist() {
    const selectedIds = getSelectedBlacklistIds();
    if (selectedIds.length === 0) {
        showToast('请先选择要删除的黑名单', 'warning');
        return;
    }
    if (!confirm(`确定删除选中的 ${selectedIds.length} 条黑名单记录吗？`)) return;

    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal/batch-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: selectedIds })
        });
        showToast(result?.message || '批量删除完成', 'success');
        await loadPersonalBlacklist(blacklistState.page || 1);
    } catch (error) {
        console.error('批量删除个人黑名单失败:', error);
    }
}

function resetBlacklistFilters() {
    const buyerId = document.getElementById('blacklistFilterBuyerId');
    const buyerNick = document.getElementById('blacklistFilterBuyerNick');
    if (buyerId) buyerId.value = '';
    if (buyerNick) buyerNick.value = '';
    loadPersonalBlacklist(1);
}

async function exportPersonalBlacklist() {
    toggleLoading(true);
    try {
        const response = await fetch(`${apiBase}/api/blacklist/personal/export`, {
            headers: { 'Authorization': `Bearer ${getAuthToken()}` }
        });
        if (response.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }
        if (!response.ok) {
            let message = `导出失败: HTTP ${response.status}`;
            try {
                const errorText = await response.text();
                if (errorText) message = errorText;
            } catch {}
            throw new Error(message);
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
        const filename = filenameMatch ? filenameMatch[1] : `personal_blacklist_${Date.now()}.xlsx`;
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast('黑名单已导出', 'success');
    } catch (error) {
        console.error('导出个人黑名单失败:', error);
        showToast(error.message || '导出个人黑名单失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

async function importPersonalBlacklistFile() {
    const input = document.getElementById('blacklistImportFile');
    const file = input?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
        showToast('仅支持 .xlsx 文件', 'warning');
        input.value = '';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal/import`, {
            method: 'POST',
            body: formData
        });
        showToast(result?.message || '黑名单导入完成', 'success');
        await loadPersonalBlacklist(1);
    } catch (error) {
        console.error('导入个人黑名单失败:', error);
    } finally {
        input.value = '';
    }
}



function getAboutDiagnosticsElements() {
    return {
        accountSelect: document.getElementById('aboutDiagnosticsAccount'),
        accountMeta: document.getElementById('aboutDiagnosticsAccountMeta'),
        refreshButton: document.getElementById('aboutDiagnosticsRefreshBtn'),
        keepaliveButton: document.getElementById('aboutDiagnosticsKeepaliveBtn'),
        historyButton: document.getElementById('aboutDiagnosticsHistoryBtn'),
        conversationInput: document.getElementById('aboutDiagnosticsConversationId'),
        statusContainer: document.getElementById('aboutDiagnosticsStatus'),
        historyContainer: document.getElementById('aboutConversationHistory'),
    };
}

function getAboutSelectedAccountId() {
    return document.getElementById('aboutDiagnosticsAccount')?.value?.trim() || '';
}

function getAboutStatusText(type, value) {
    const normalized = String(value || '').trim();
    if (!normalized) {
        return '暂无';
    }

    const maps = {
        connection: {
            connected: '已连接',
            reconnecting: '重连中',
            connecting: '连接中',
            disconnected: '未连接',
            failed: '失败',
            closed: '已关闭',
            not_running: '未运行',
            unknown: '未知',
        },
        keepalive: {
            started: '执行中',
            success: '成功',
            recovered: '已恢复',
            auth_failed: '鉴权失败',
            api_failed: '接口失败',
            network_failed: '网络异常',
            response_parse_failed: '响应解析失败',
            exception: '执行异常',
        },
        token: {
            started: '执行中',
            success: '成功',
            skipped_cooldown: '冷却跳过',
            manual_refresh_active: '手动刷新进行中',
            manual_refresh_browser_stabilizing: '浏览器稳定中',
            post_slider_session_settling: '滑块后稳定中',
            restarted_after_cookie_refresh: '已触发重连',
            captcha_max_retries_exceeded: '滑块重试超限',
            token_expired_recovery_failed: '过期恢复失败',
            token_refresh_failed: '刷新失败',
            token_refresh_exception: '刷新异常',
            token_init_failed: '初始化失败',
            token_missing_after_refresh: '刷新后无 Token',
            token_missing: '无 Token',
            failed: '失败',
        },
        stream: {
            healthy: '正常',
            recovered: '已恢复',
            warming_up: '预热中',
            watching: '观察中',
            recovering: '恢复中',
            suspected_stale: '疑似停滞',
            connection_unready: '连接未就绪',
            not_running: '未运行',
        },
    };

    return maps[type]?.[normalized] || normalized;
}

function getAboutStatusVariant(type, value) {
    const normalized = String(value || '').trim();
    if (!normalized) {
        return 'secondary';
    }

    if (type === 'connection') {
        if (normalized === 'connected') return 'success';
        if (normalized === 'connecting' || normalized === 'reconnecting') return 'warning';
        if (normalized === 'failed') return 'danger';
        if (normalized === 'not_running' || normalized === 'disconnected' || normalized === 'closed') return 'secondary';
        return 'info';
    }

    if (type === 'stream') {
        if (normalized === 'healthy' || normalized === 'recovered') return 'success';
        if (normalized === 'warming_up' || normalized === 'watching' || normalized === 'recovering') return 'info';
        if (normalized === 'suspected_stale') return 'warning';
        if (normalized === 'connection_unready' || normalized === 'not_running') return 'secondary';
        return 'secondary';
    }

    if (normalized === 'success' || normalized === 'recovered') return 'success';
    if (normalized === 'started' || normalized === 'connecting' || normalized === 'reconnecting') return 'info';
    if (normalized.includes('failed') || normalized.includes('exception') || normalized.includes('error')) return 'danger';
    if (normalized.includes('skipped') || normalized.includes('retry') || normalized.includes('restarted')) return 'warning';
    return 'secondary';
}

function buildAboutStatusBadge(type, value) {
    const text = getAboutStatusText(type, value);
    const variant = getAboutStatusVariant(type, value);
    return `<span class="about-status-badge is-${variant}">${escapeHtml(text)}</span>`;
}

function buildAboutMetaCard({ label, value, supporting = '' }) {
    return `
        <div class="account-diagnostics-summary-item">
            <div class="account-diagnostics-summary-label">${escapeHtml(label)}</div>
            <div class="account-diagnostics-summary-value">${escapeHtml(value)}</div>
            ${supporting ? `<div class="account-diagnostics-summary-support">${escapeHtml(supporting)}</div>` : ''}
        </div>
    `;
}

function buildAboutRuntimeStatusItem({ label, value, note = '', tone = '', richValue = false, accent = '', icon = '' }) {
    return `
        <div class="account-diagnostics-status-item ${tone ? `is-${tone}` : ''} ${accent ? `is-${accent}` : ''}">
            <div class="account-diagnostics-status-item-head">
                <div class="account-diagnostics-status-item-icon">
                    ${icon ? `<i class="bi bi-${icon}"></i>` : ''}
                </div>
                <div class="account-diagnostics-status-item-label">${escapeHtml(label)}</div>
            </div>
            <div class="account-diagnostics-status-item-value">${richValue ? value : escapeHtml(value)}</div>
            ${note ? `<div class="account-diagnostics-status-item-note">${escapeHtml(note)}</div>` : ''}
        </div>
    `;
}

function buildAboutRuntimeMetaItem(label, value) {
    return `
        <div class="account-diagnostics-status-meta-item">
            <span class="account-diagnostics-status-meta-label">${escapeHtml(label)}</span>
            <span class="account-diagnostics-status-meta-value">${escapeHtml(value)}</span>
        </div>
    `;
}

function buildAboutReadinessValue(items) {
    const normalizedItems = Array.isArray(items) ? items : [];
    const totalCount = normalizedItems.length;
    const readyCount = normalizedItems.filter(item => item.ready).length;
    const progressPercent = totalCount
        ? Math.max(0, Math.min(100, Math.round((readyCount / totalCount) * 100)))
        : 0;
    const pendingLabels = normalizedItems
        .filter(item => !item.ready)
        .map(item => item.label);

    let summaryNote = '暂无链路状态';
    if (totalCount > 0 && pendingLabels.length === 0) {
        summaryNote = '四条关键链路均已就绪';
    } else if (totalCount > 0 && pendingLabels.length === totalCount) {
        summaryNote = '四条关键链路均未就绪';
    } else if (pendingLabels.length > 0) {
        summaryNote = `待处理：${pendingLabels.join(' / ')}`;
    }

    return `
        <div class="account-diagnostics-readiness-summary">
            <div class="account-diagnostics-readiness-hero">
                <div class="account-diagnostics-readiness-ratio">
                    <span class="account-diagnostics-readiness-ratio-current">${readyCount}</span>
                    <span class="account-diagnostics-readiness-ratio-total">/ ${totalCount}</span>
                </div>
                <div class="account-diagnostics-readiness-caption">关键链路已就绪</div>
            </div>
            <div class="account-diagnostics-readiness-progress" aria-hidden="true">
                <span class="account-diagnostics-readiness-progress-bar" style="width: ${progressPercent}%"></span>
            </div>
            <div class="account-diagnostics-readiness-percent">${progressPercent}% 就绪</div>
            <div class="account-diagnostics-readiness-list">
                ${normalizedItems.map(item => `
                <span class="account-diagnostics-readiness-chip ${item.ready ? 'is-ready' : 'is-pending'}">
                    <span class="account-diagnostics-readiness-name-wrap">
                        <span class="account-diagnostics-readiness-dot"></span>
                        <span class="account-diagnostics-readiness-name">${escapeHtml(item.label)}</span>
                    </span>
                    <span class="account-diagnostics-readiness-state">${item.ready ? '已就绪' : '未就绪'}</span>
                </span>
                `).join('')}
            </div>
            <div class="account-diagnostics-readiness-summary-note">${escapeHtml(summaryNote)}</div>
        </div>
    `;
}

function renderAboutAccountMeta(account) {
    const { accountMeta } = getAboutDiagnosticsElements();
    if (!accountMeta) return;

    if (!account) {
        accountMeta.innerHTML = '';
        return;
    }

    const metaParts = [
        buildAboutMetaCard({
            label: '账号 ID',
            value: account.id,
        }),
        buildAboutMetaCard({
            label: '登录名',
            value: account.username || '未设置用户名',
            supporting: account.username ? '用于账号识别与后续 Cookie 刷新' : '建议补充用户名，便于后续维护',
        }),
        buildAboutMetaCard({
            label: '备注',
            value: account.remark || '未设置备注',
            supporting: account.remark ? '' : '可在账号管理中补充备注',
        }),
    ];

    accountMeta.innerHTML = metaParts.join('');
}

function renderAboutDiagnosticsPlaceholder(container, icon, title, subtitle) {
    if (!container) return;

    container.innerHTML = `
        <div class="about-placeholder">
            <i class="bi bi-${icon}"></i>
            <div>
                <div class="about-placeholder-title">${escapeHtml(title)}</div>
                <div class="about-placeholder-sub">${escapeHtml(subtitle)}</div>
            </div>
        </div>
    `;
}

function renderAboutRuntimePlaceholder(title, subtitle) {
    const { statusContainer } = getAboutDiagnosticsElements();
    renderAboutDiagnosticsPlaceholder(statusContainer, 'hdd-network', title, subtitle);
}

function renderAboutHistoryPlaceholder(title, subtitle) {
    const { historyContainer } = getAboutDiagnosticsElements();
    renderAboutDiagnosticsPlaceholder(historyContainer, 'clock-history', title, subtitle);
}

function getAboutRuntimeOverview(runtimeStatus, readinessCount = 0) {
    if (isConnectorManualVerificationRequired(runtimeStatus)) {
        return {
            tone: 'warning',
            title: '账号需要重新认证',
            note: String(runtimeStatus?.connector_reason_message || '连接器已停止自动恢复，请点击“重新扫码验证”。'),
        };
    }
    if (!runtimeStatus?.running) {
        return {
            tone: 'danger',
            title: '实例未启动',
            note: '轻保活和历史消息查询都依赖账号实例，当前应先启动实例。',
        };
    }

    if (runtimeStatus?.connection_state === 'connecting' || runtimeStatus?.connection_state === 'reconnecting') {
        return {
            tone: 'info',
            title: '连接正在恢复',
            note: '主链路还在波动，先观察连接状态与最近消息时间是否继续推进。',
        };
    }

    if (!runtimeStatus?.ws_ready || !runtimeStatus?.session_ready || !runtimeStatus?.has_current_token || !runtimeStatus?.message_stream_ready) {
        return {
            tone: 'warning',
            title: `${readinessCount} / 4 关键链路已就绪`,
            note: '链路部分可用，优先处理未就绪项，再观察保活与消息链路。',
        };
    }

    return {
        tone: 'success',
        title: '链路稳定可用',
        note: '连接、轻保活、Token 与业务消息流四条主信号都处于正常状态。',
    };
}

function renderAboutRuntimeStatus(runtimeStatus) {
    const { statusContainer } = getAboutDiagnosticsElements();
    if (!statusContainer) return;

    if (!runtimeStatus) {
        renderAboutRuntimePlaceholder('暂无运行态', '当前账号还没有可用的运行态信息。');
        return;
    }

    const lastConnectionDisplay = formatAboutRuntimeTime(
        runtimeStatus.last_successful_connection_at_display,
        runtimeStatus.last_successful_connection_at
    );
    const keepaliveDisplay = formatAboutRuntimeTime(
        runtimeStatus.session_keepalive_at_display,
        runtimeStatus.session_keepalive_at
    );
    const tokenRefreshDisplay = formatAboutRuntimeTime(
        runtimeStatus.token_last_refreshed_at_display,
        runtimeStatus.token_last_refreshed_at
    );
    const lastMessageDisplay = formatAboutRuntimeTime(
        runtimeStatus.last_message_received_at_display,
        runtimeStatus.last_message_received_at
    );
    const stateChangedDisplay = formatAboutRuntimeTime(
        runtimeStatus.state_last_changed_at_display,
        runtimeStatus.state_last_changed_at
    );
    const messageStreamDisplay = getMessageStreamRuntimeDisplay(runtimeStatus);
    const messageStreamStatus = messageStreamDisplay.status;
    const connectorNeedsManual = isConnectorManualVerificationRequired(runtimeStatus);
    const readinessItems = [
        { label: '实例', ready: !!runtimeStatus.running && !connectorNeedsManual },
        { label: 'WS', ready: !!runtimeStatus.ws_ready },
        { label: 'Session', ready: !!runtimeStatus.session_ready },
        { label: 'Token', ready: !!runtimeStatus.has_current_token },
        { label: '业务流', ready: !!runtimeStatus.message_stream_ready },
    ];
    const readinessSignalItems = readinessItems.slice(1);
    const readinessSignalCount = readinessSignalItems.filter(item => item.ready).length;
    const overview = getAboutRuntimeOverview(runtimeStatus, readinessSignalCount);
    const connectionTone = getAboutStatusVariant('connection', runtimeStatus.connection_state);
    const keepaliveDisplayStatus = runtimeStatus.session_keepalive_display_status || runtimeStatus.session_keepalive_status;
    const keepaliveTone = getAboutStatusVariant('keepalive', keepaliveDisplayStatus);
    const tokenTone = getAboutStatusVariant('token', runtimeStatus.token_refresh_status);
    const messageStreamTone = getAboutStatusVariant('stream', messageStreamStatus);
    const readinessTone = readinessSignalItems.every(item => item.ready)
        ? 'success'
        : readinessSignalItems.some(item => item.ready)
            ? 'warning'
            : 'danger';
    const selectedAccount = aboutDiagnosticsAccounts.find(account => account.id === getAboutSelectedAccountId()) || null;

    statusContainer.innerHTML = `
        <div class="account-diagnostics-status-shell">
            <div class="account-diagnostics-status-note-bar is-${overview.tone}">
                <div class="account-diagnostics-status-note-title">${escapeHtml(overview.title)}</div>
                <div class="account-diagnostics-status-note-text">${escapeHtml(overview.note)}</div>
            </div>
            ${buildManualInterventionAlert(selectedAccount?.status_note || '', runtimeStatus, { accountId: selectedAccount?.id })}
            <div class="account-diagnostics-status-body">
                <div class="account-diagnostics-status-primary">
                    <div class="account-diagnostics-status-grid">
                        ${buildAboutRuntimeStatusItem({
                            label: '连接状态',
                            value: buildAboutStatusBadge('connection', runtimeStatus.connection_state),
                            note: `最近连接成功：${lastConnectionDisplay}`,
                            tone: connectionTone,
                            richValue: true,
                            accent: 'connection',
                            icon: 'hdd-network',
                        })}
                        ${buildAboutRuntimeStatusItem({
                            label: '轻保活状态',
                            value: buildAboutStatusBadge('keepalive', keepaliveDisplayStatus),
                            note: runtimeStatus.session_keepalive_display_note
                                ? `最近执行：${keepaliveDisplay} · ${runtimeStatus.session_keepalive_display_note}`
                                : `最近执行：${keepaliveDisplay}`,
                            tone: keepaliveTone,
                            richValue: true,
                            accent: 'keepalive',
                            icon: 'heart-pulse',
                        })}
                        ${buildAboutRuntimeStatusItem({
                            label: 'Token 刷新状态',
                            value: buildAboutStatusBadge('token', runtimeStatus.token_refresh_status),
                            note: `最近刷新：${tokenRefreshDisplay}`,
                            tone: tokenTone,
                            richValue: true,
                            accent: 'token',
                            icon: 'key',
                        })}
                        ${buildAboutRuntimeStatusItem({
                            label: '业务消息流',
                            value: buildAboutStatusBadge('stream', messageStreamStatus),
                            note: messageStreamDisplay.note,
                            tone: messageStreamTone,
                            richValue: true,
                            accent: 'readiness',
                            icon: 'broadcast-pin',
                        })}
                    </div>
                </div>
                <div class="account-diagnostics-status-sidebar">
                    ${buildAboutRuntimeStatusItem({
                        label: '链路就绪情况',
                        value: buildAboutReadinessValue(readinessSignalItems),
                        tone: readinessTone,
                        richValue: true,
                        accent: 'readiness',
                        icon: 'diagram-3',
                    })}
                </div>
            </div>
            <div class="account-diagnostics-status-meta">
                ${buildAboutRuntimeMetaItem('最近收到消息', lastMessageDisplay)}
                ${buildAboutRuntimeMetaItem('状态变化时间', stateChangedDisplay)}
            </div>
        </div>
    `;
}

function getAboutHistoryMessageText(message) {
    if (message == null) {
        return '空消息';
    }

    if (typeof message === 'string') {
        return message;
    }

    if (typeof message?.text?.text === 'string' && message.text.text.trim()) {
        return message.text.text;
    }

    if (typeof message?.raw === 'string' && message.raw.trim()) {
        return message.raw;
    }

    try {
        return JSON.stringify(message, null, 2);
    } catch (error) {
        return String(message);
    }
}

function getAboutHistorySenderInitial(senderName) {
    const normalized = String(senderName || '').trim();
    if (!normalized) {
        return 'U';
    }
    return normalized.charAt(0).toUpperCase();
}

function renderAboutConversationHistory(messages, meta = {}) {
    const { historyContainer } = getAboutDiagnosticsElements();
    if (!historyContainer) return;

    if (!Array.isArray(messages) || messages.length === 0) {
        renderAboutHistoryPlaceholder('未查询到历史消息', '确认会话 ID 是否正确，以及该账号实例是否正在运行。');
        return;
    }

    const summaryText = `共查询到 ${messages.length} 条消息`;
    const conversationIdText = meta.conversationId ? `会话 ID: ${meta.conversationId}` : '';

    historyContainer.innerHTML = `
        <div class="about-history-summary">
            <span class="about-history-summary-main">${escapeHtml(summaryText)}</span>
            ${conversationIdText ? `<span class="about-history-summary-meta">${escapeHtml(conversationIdText)}</span>` : ''}
        </div>
        <div class="about-history-items">
            ${messages.map((item, index) => {
                const senderName = item?.send_user_name || '未知用户';
                const senderId = item?.send_user_id || '-';
                const senderInitial = getAboutHistorySenderInitial(senderName);
                const messageText = getAboutHistoryMessageText(item?.message);
                const rawText = typeof item?.message === 'object'
                    ? (() => {
                        try {
                            return JSON.stringify(item.message, null, 2);
                        } catch (error) {
                            return messageText;
                        }
                    })()
                    : messageText;

                return `
                    <div class="about-history-item">
                        <div class="about-history-item-header">
                            <div class="about-history-sender-block">
                                <div class="about-history-sender-row">
                                    <span class="about-history-sender-avatar">${escapeHtml(senderInitial)}</span>
                                    <div class="about-history-sender-meta">
                                        <div class="about-history-sender">${escapeHtml(senderName)}</div>
                                        <div class="about-history-sender-id">发送者 ID: ${escapeHtml(senderId)}</div>
                                    </div>
                                </div>
                            </div>
                            <div class="about-history-index">第 ${index + 1} 条</div>
                        </div>
                        <div class="about-history-message-shell">
                            <div class="about-history-message">${escapeHtml(messageText)}</div>
                        </div>
                        ${rawText !== messageText ? `
                            <details class="about-history-raw">
                                <summary>查看原始内容</summary>
                                <pre>${escapeHtml(rawText)}</pre>
                            </details>
                        ` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function populateAboutAccountOptions(accounts) {
    const { accountSelect } = getAboutDiagnosticsElements();
    if (!accountSelect) return;

    if (!Array.isArray(accounts) || accounts.length === 0) {
        accountSelect.innerHTML = '<option value="">暂无账号</option>';
        accountSelect.disabled = true;
        return;
    }

    accountSelect.disabled = false;
    accountSelect.innerHTML = `
        <option value="">请选择账号</option>
        ${accounts.map(account => {
            const runtimeStatus = account.runtime_status || {};
            const statusSuffix = isConnectorManualVerificationRequired(runtimeStatus)
                ? ' · 需重新认证'
                : (getConnectorRuntimeState(runtimeStatus) === 'online'
                    ? ' · 在线'
                    : (runtimeStatus.running ? ' · 运行中' : ''));
            return `<option value="${escapeHtml(account.id)}">${escapeHtml(account.id + statusSuffix)}</option>`;
        }).join('')}
    `;
}

async function loadAboutRuntimeStatus(accountId = '') {
    const normalizedAccountId = String(accountId || getAboutSelectedAccountId()).trim();
    if (!normalizedAccountId) {
        renderAboutAccountMeta(null);
        renderAboutRuntimePlaceholder('请选择账号', '选择账号后会显示当前连接状态、轻保活结果和最近活动时间。');
        return;
    }

    const selectedAccount = aboutDiagnosticsAccounts.find(account => account.id === normalizedAccountId) || null;
    renderAboutAccountMeta(selectedAccount);
    renderAboutRuntimeStatus(selectedAccount?.runtime_status || null);

    try {
        const result = await fetchJSON(`${apiBase}/cookies/${encodeURIComponent(normalizedAccountId)}/runtime-status`);
        const runtimeStatus = result?.runtime_status || null;
        const targetAccount = aboutDiagnosticsAccounts.find(account => account.id === normalizedAccountId);
        if (targetAccount) {
            targetAccount.runtime_status = runtimeStatus;
            renderAboutAccountMeta(targetAccount);
        }
        renderAboutRuntimeStatus(runtimeStatus);
        scheduleAboutRuntimeAutoRetry(normalizedAccountId, runtimeStatus);
    } catch (error) {
        console.error('加载账号运行态失败:', error);
    }
}

async function loadAboutDiagnostics() {
    initAboutDiagnosticsEvents();

    try {
        const previousAccountId = getAboutSelectedAccountId();
        const accounts = await fetchJSON(`${apiBase}/cookies/details`);
        aboutDiagnosticsAccounts = Array.isArray(accounts) ? accounts : [];
        populateAboutAccountOptions(aboutDiagnosticsAccounts);

        const { accountSelect } = getAboutDiagnosticsElements();
        if (!accountSelect || aboutDiagnosticsAccounts.length === 0) {
            renderAboutAccountMeta(null);
            renderAboutRuntimePlaceholder('暂无账号', '请先在账号管理中添加闲鱼账号。');
            renderAboutHistoryPlaceholder('暂无历史消息', '请先添加账号并确保实例已启动。');
            return;
        }

        const nextAccountId = aboutDiagnosticsAccounts.some(account => account.id === previousAccountId)
            ? previousAccountId
            : (aboutDiagnosticsAccounts.find(account => account.runtime_status?.running)?.id || aboutDiagnosticsAccounts[0]?.id || '');

        accountSelect.value = nextAccountId;
        await loadAboutRuntimeStatus(nextAccountId);
    } catch (error) {
        console.error('加载账号保活诊断失败:', error);
    }
}

async function refreshAboutDiagnosticsStatus() {
    const { refreshButton } = getAboutDiagnosticsElements();
    const accountId = getAboutSelectedAccountId();
    if (!accountId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    const originalHtml = refreshButton?.innerHTML;
    if (refreshButton) {
        refreshButton.disabled = true;
        refreshButton.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>刷新中...';
    }

    try {
        await loadAboutRuntimeStatus(accountId);
        showToast(`账号 "${accountId}" 运行态已刷新`, 'success');
    } finally {
        if (refreshButton) {
            refreshButton.disabled = false;
            refreshButton.innerHTML = originalHtml;
        }
    }
}

async function triggerAboutSessionKeepalive() {
    const { keepaliveButton } = getAboutDiagnosticsElements();
    const accountId = getAboutSelectedAccountId();
    if (!accountId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    const originalHtml = keepaliveButton?.innerHTML;
    if (keepaliveButton) {
        keepaliveButton.disabled = true;
        keepaliveButton.innerHTML = '<i class="bi bi-lightning-charge-fill me-1"></i>执行中...';
    }

    try {
        const result = await fetchJSON(`${apiBase}/cookies/${encodeURIComponent(accountId)}/session-keepalive`, {
            method: 'POST',
        });
        const targetAccount = aboutDiagnosticsAccounts.find(account => account.id === accountId);
        if (targetAccount) {
            targetAccount.runtime_status = result?.runtime_status || null;
            renderAboutAccountMeta(targetAccount);
        }
        renderAboutRuntimeStatus(result?.runtime_status || null);
        showToast(result?.message || '轻保活已执行', result?.success ? 'success' : 'warning');
    } catch (error) {
        console.error('执行轻保活失败:', error);
    } finally {
        if (keepaliveButton) {
            keepaliveButton.disabled = false;
            keepaliveButton.innerHTML = originalHtml;
        }
    }
}

async function loadAboutConversationHistory() {
    const { historyButton, conversationInput } = getAboutDiagnosticsElements();
    const accountId = getAboutSelectedAccountId();
    const conversationId = conversationInput?.value?.trim() || '';

    if (!accountId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    if (!conversationId) {
        showToast('请输入会话 ID', 'warning');
        return;
    }

    const originalHtml = historyButton?.innerHTML;
    if (historyButton) {
        historyButton.disabled = true;
        historyButton.innerHTML = '<i class="bi bi-chat-left-text-fill me-1"></i>查询中...';
    }

    renderAboutHistoryPlaceholder('正在查询历史消息', '请稍候，系统正在尝试拉取最近的会话消息。');

    try {
        const result = await fetchJSON(
            `${apiBase}/cookies/${encodeURIComponent(accountId)}/conversations/${encodeURIComponent(conversationId)}/history`
        );
        renderAboutConversationHistory(result?.messages || [], {
            conversationId: result?.conversation_id || conversationId,
        });
        showToast(`账号 "${accountId}" 历史消息查询完成`, 'success');
    } catch (error) {
        console.error('查询历史消息失败:', error);
        renderAboutHistoryPlaceholder('历史消息查询失败', error?.message || '请稍后重试。');
    } finally {
        if (historyButton) {
            historyButton.disabled = false;
            historyButton.innerHTML = originalHtml;
        }
    }
}

function initAboutDiagnosticsEvents() {
    if (aboutDiagnosticsInitialized) {
        return;
    }

    const {
        accountSelect,
        refreshButton,
        keepaliveButton,
        historyButton,
        conversationInput,
    } = getAboutDiagnosticsElements();

    accountSelect?.addEventListener('change', async () => {
        renderAboutHistoryPlaceholder('暂无历史消息', '切换账号后，请重新输入会话 ID 并查询历史消息。');
        await loadAboutRuntimeStatus(accountSelect.value);
    });

    refreshButton?.addEventListener('click', refreshAboutDiagnosticsStatus);
    keepaliveButton?.addEventListener('click', triggerAboutSessionKeepalive);
    historyButton?.addEventListener('click', loadAboutConversationHistory);
    conversationInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            loadAboutConversationHistory();
        }
    });

    aboutDiagnosticsInitialized = true;
}

// ================================
// 【账号管理菜单】相关功能
// ================================

// 加载Cookie列表
async function loadCookies() {
    try {
    toggleLoading(true);
    const tbody = document.querySelector('#cookieTable tbody');
    tbody.innerHTML = '';

    const cookieDetails = await fetchJSON(apiBase + '/cookies/details');

    if (cookieDetails.length === 0) {
        tbody.innerHTML = `
        <tr>
            <td colspan="11" class="text-center py-4 text-muted empty-state">
            <i class="bi bi-inbox fs-1 d-block mb-3"></i>
            <h5>暂无账号</h5>
            <p class="mb-0">请添加新的闲鱼账号开始使用</p>
            </td>
        </tr>
        `;
        return;
    }

    // 为每个账号获取关键词数量和默认回复设置并渲染
    const accountsWithKeywords = await Promise.all(
        cookieDetails.map(async (cookie) => {
        try {
            // 获取关键词数量
            const keywordsResponse = await fetch(`${apiBase}/keywords/${cookie.id}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
            });

            let keywordCount = 0;
            if (keywordsResponse.ok) {
            const keywordsData = await keywordsResponse.json();
            keywordCount = keywordsData.length;
            }

            // 获取默认回复设置
            const defaultReplyResponse = await fetch(`${apiBase}/default-replies/${cookie.id}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
            });

            let defaultReply = { enabled: false, reply_content: '' };
            if (defaultReplyResponse.ok) {
            defaultReply = await defaultReplyResponse.json();
            }

            // 获取AI回复设置
            const aiReplyResponse = await fetch(`${apiBase}/ai-reply-settings/${cookie.id}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
            });

            let aiReply = { ai_enabled: false, model_name: 'qwen-plus' };
            if (aiReplyResponse.ok) {
            aiReply = await aiReplyResponse.json();
            }

            return {
            ...cookie,
            keywordCount: keywordCount,
            defaultReply: defaultReply,
            aiReply: aiReply
            };
        } catch (error) {
            return {
            ...cookie,
            keywordCount: 0,
            defaultReply: { enabled: false, reply_content: '' },
            aiReply: { ai_enabled: false, model_name: 'qwen-plus' }
            };
        }
        })
    );

    accountsWithKeywords.forEach(cookie => {
        // 使用数据库中的实际状态，默认为启用
        const isEnabled = cookie.enabled === undefined ? true : cookie.enabled;
        const statusNoteBadge = renderStatusNoteBadge(cookie.status_note, 'account-status-note-badge');

        console.log(`账号 ${cookie.id} 状态: enabled=${cookie.enabled}, isEnabled=${isEnabled}`); // 调试信息

        const tr = document.createElement('tr');
        tr.className = `account-row ${isEnabled ? 'enabled' : 'disabled'}`;
        tr.dataset.accountId = cookie.id;
        // 默认回复状态标签
        const defaultReplyBadge = cookie.defaultReply.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

        // AI回复状态标签
        const aiReplyBadge = cookie.aiReply.ai_enabled ?
        '<span class="badge bg-primary">AI启用</span>' :
        '<span class="badge bg-secondary">AI禁用</span>';

        // 自动确认发货状态（默认开启）
        const autoConfirm = cookie.auto_confirm === undefined ? true : cookie.auto_confirm;
        
        // 自动好评状态（默认关闭）
        const autoComment = cookie.auto_comment === undefined ? false : cookie.auto_comment;

        // 自动求小红花状态（默认关闭）
        const autoRedFlower = cookie.auto_red_flower === undefined ? false : cookie.auto_red_flower;

        tr.innerHTML = `
        <td class="align-middle">
            <div class="cookie-id">
            <strong class="text-primary">${cookie.id}</strong>
            ${renderConnectorReadiness(cookie.runtime_status)}
            </div>
        </td>
        <td class="align-middle">
            <div class="cookie-value" title="点击复制Cookie" style="font-family: monospace; font-size: 0.875rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
            ${cookie.value || '未设置'}
            </div>
        </td>
        <td class="align-middle">
            <span class="badge ${cookie.keywordCount > 0 ? 'bg-success' : 'bg-secondary'}">
            ${cookie.keywordCount} 个关键词
            </span>
        </td>
        <td class="align-middle">
            <div class="account-status-cell">
            <div class="account-status-main">
                <label class="status-toggle" title="${isEnabled ? '点击禁用' : '点击启用'}">
                    <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleAccountStatus('${cookie.id}', this.checked)">
                    <span class="status-slider"></span>
                </label>
                <span class="status-badge ${isEnabled ? 'enabled' : 'disabled'}" title="${isEnabled ? '账号已启用' : '账号已禁用'}">
                    <i class="bi bi-${isEnabled ? 'check-circle-fill' : 'x-circle-fill'}"></i>
                </span>
            </div>
            ${statusNoteBadge}
            </div>
        </td>
        <td class="align-middle">
            ${defaultReplyBadge}
        </td>
        <td class="align-middle">
            ${aiReplyBadge}
        </td>
        <td class="align-middle">
            <div class="d-flex align-items-center gap-2">
            <label class="status-toggle" title="${autoConfirm ? '点击关闭自动确认发货' : '点击开启自动确认发货'}">
                <input type="checkbox" ${autoConfirm ? 'checked' : ''} onchange="toggleAutoConfirm('${cookie.id}', this.checked)">
                <span class="status-slider"></span>
            </label>
            <span class="status-badge ${autoConfirm ? 'enabled' : 'disabled'}" title="${autoConfirm ? '自动确认发货已开启' : '自动确认发货已关闭'}">
                <i class="bi bi-${autoConfirm ? 'truck' : 'truck-flatbed'}"></i>
            </span>
            </div>
        </td>
        <td class="align-middle">
            <div class="d-flex align-items-center gap-2">
            <label class="status-toggle" title="${autoComment ? '点击关闭自动好评' : '点击开启自动好评'}">
                <input type="checkbox" ${autoComment ? 'checked' : ''} onchange="toggleAutoComment('${cookie.id}', this.checked)">
                <span class="status-slider"></span>
            </label>
            <span class="status-badge ${autoComment ? 'enabled' : 'disabled'}" title="${autoComment ? '自动好评已开启' : '自动好评已关闭'}">
                <i class="bi bi-${autoComment ? 'star-fill' : 'star'}"></i>
            </span>
            <button class="btn btn-sm btn-outline-warning ms-1" onclick="showCommentTemplates('${cookie.id}')" title="管理好评模板">
                <i class="bi bi-card-text"></i>
            </button>
            </div>
        </td>
        <td class="align-middle">
            <div class="remark-cell" data-cookie-id="${cookie.id}">
                <span class="remark-display" onclick="editRemark('${cookie.id}', '${(cookie.remark || '').replace(/'/g, '&#39;')}')" title="点击编辑备注" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                    ${cookie.remark || '<i class="bi bi-plus-circle text-muted"></i> 添加备注'}
                </span>
            </div>
        </td>
        <td class="align-middle">
            <div class="pause-duration-cell" data-cookie-id="${cookie.id}">
                <span class="pause-duration-display" onclick="editPauseDuration('${cookie.id}', ${cookie.pause_duration !== undefined ? cookie.pause_duration : 10})" title="点击编辑暂停时间" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                    <i class="bi bi-clock me-1"></i>${cookie.pause_duration === 0 ? '不暂停' : (cookie.pause_duration || 10) + '分钟'}
                </span>
            </div>
        </td>
        <td class="align-middle account-actions-cell">
            <div class="account-actions-toolbar" role="group" aria-label="账号操作">
            <div class="account-action-group account-action-group-basic" aria-label="基础操作">
                <span class="account-action-group-label">基础</span>
                <button class="btn btn-sm btn-outline-info account-action-btn account-action-qr-relogin" onclick="showAccountQRRelogin('${cookie.id}', 'lite')" title="重新扫码登录账号" data-action="qr-relogin">
                    <i class="bi bi-qr-code-scan"></i><span class="action-text">重登</span>
                </button>
                <button class="btn btn-sm btn-outline-secondary account-action-btn" onclick="openRemoteVerificationConsole('${cookie.id}')" title="打开实时人工验证" data-action="remote-verification">
                    <i class="bi bi-shield-check"></i><span class="action-text">验证</span>
                </button>
                <button class="btn btn-sm btn-outline-primary account-action-btn" onclick="editCookieInline('${cookie.id}', '${cookie.value}')" title="修改账号信息与Cookie" data-action="edit-cookie" data-requires-enabled="true" ${!isEnabled ? 'disabled' : ''}>
                    <i class="bi bi-pencil"></i><span class="action-text">编辑</span>
                </button>
            </div>
            <div class="account-action-group account-action-group-reply" aria-label="回复配置">
                <span class="account-action-group-label">回复</span>
                <button class="btn btn-sm btn-outline-success account-action-btn" onclick="goToAutoReply('${cookie.id}')" title="${isEnabled ? '设置自动回复' : '配置关键词 (账号已禁用)'}" data-action="auto-reply">
                    <i class="bi bi-chat-dots"></i><span class="action-text">规则</span>
                </button>
                <button class="btn btn-sm btn-outline-warning account-action-btn" onclick="configAIReply('${cookie.id}')" title="配置AI回复" data-action="ai-reply" data-requires-enabled="true" ${!isEnabled ? 'disabled' : ''}>
                    <i class="bi bi-robot"></i><span class="action-text">AI</span>
                </button>
                <button class="btn btn-sm btn-outline-danger account-action-btn" onclick="runHistoricalAutoCommentForAccount('${cookie.id}')" title="历史订单补评价" data-action="history-rate" data-requires-enabled="true" ${!isEnabled ? 'disabled' : ''}>
                    <i class="bi bi-star-fill"></i><span class="action-text">补评</span>
                </button>
            </div>
            <div class="account-action-group account-action-group-item" aria-label="商品操作">
                <span class="account-action-group-label">商品</span>
                <button class="btn btn-sm btn-outline-secondary account-action-btn" onclick="polishAccountItems('${cookie.id}')" title="立即擦亮全部商品" data-action="polish-items" data-requires-enabled="true" ${!isEnabled ? 'disabled' : ''}>
                    <i class="bi bi-stars"></i><span class="action-text">擦亮</span>
                </button>
                <button class="btn btn-sm btn-outline-info account-action-btn" onclick="openPolishScheduleModal('${cookie.id}')" title="设置定时擦亮" data-action="polish-schedule" data-requires-enabled="true" ${!isEnabled ? 'disabled' : ''}>
                    <i class="bi bi-clock"></i><span class="action-text">定时</span>
                </button>
            </div>
            <div class="account-action-group account-action-group-flower" aria-label="小红花操作">
                <span class="account-action-group-label">小红花</span>
                <button class="btn btn-sm ${autoRedFlower ? 'btn-outline-danger' : 'btn-outline-secondary'} account-action-btn" onclick="toggleAutoRedFlower('${cookie.id}', ${!autoRedFlower})" title="${autoRedFlower ? '关闭自动求小红花' : '开启自动求小红花'}" data-auto-red-flower-toggle="${cookie.id}" data-auto-red-flower-active="${autoRedFlower ? 'true' : 'false'}">
                    <i class="bi bi-flower${autoRedFlower ? '1' : '2'}"></i><span class="action-text">${autoRedFlower ? '已开' : '开启'}</span>
                </button>
                <button class="btn btn-sm btn-outline-danger account-action-btn" onclick="runAutoRedFlowerForAccount('${cookie.id}')" title="立即执行求小红花" data-red-flower-run="${cookie.id}" data-red-flower-active="${autoRedFlower ? 'true' : 'false'}" ${(!isEnabled || !autoRedFlower) ? 'disabled' : ''}>
                    <i class="bi bi-send-fill"></i><span class="action-text">执行</span>
                </button>
            </div>
            <div class="account-action-group account-action-group-danger" aria-label="危险操作">
                <button class="btn btn-sm btn-outline-danger account-action-btn account-action-delete" onclick="delCookie('${cookie.id}')" title="删除账号" data-action="delete-account">
                    <i class="bi bi-trash"></i><span class="action-text">删除</span>
                </button>
            </div>
            </div>
        </td>
        `;
        tbody.appendChild(tr);
    });

    // 为Cookie值添加点击复制功能
    document.querySelectorAll('.cookie-value').forEach(element => {
        element.style.cursor = 'pointer';
        element.addEventListener('click', function() {
        const row = this.closest('tr');
        const cookieId = row?.querySelector('.cookie-id strong')?.textContent;
        if (cookieId) {
            copyCookie(cookieId);
        }
        });
    });

    // 重新初始化工具提示
    initTooltips();
    focusPendingAccountManagementRow();

    } catch (err) {
    // 错误已在fetchJSON中处理
    } finally {
    toggleLoading(false);
    if (document.getElementById('accounts-section')?.classList.contains('active')) {
        loadAboutDiagnostics();
    }
    }
}

// 复制Cookie
async function copyCookie(id) {
    try {
    const details = await fetchJSON(`${apiBase}/cookie/${encodeURIComponent(id)}/details?include_secrets=true`);
    const value = details?.value || '';

    if (!value || value === '未设置') {
        showToast('该账号暂无Cookie值', 'warning');
        return;
    }

    navigator.clipboard.writeText(value).then(() => {
        showToast(`账号 "${id}" 的Cookie已复制到剪贴板`, 'success');
    }).catch(() => {
        const textArea = document.createElement('textarea');
        textArea.value = value;
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            showToast(`账号 "${id}" 的Cookie已复制到剪贴板`, 'success');
        } catch (err) {
            showToast('复制失败，请手动复制', 'error');
        }
        document.body.removeChild(textArea);
    });
    } catch (error) {
    console.error('获取Cookie详情失败:', error);
    showToast('获取Cookie详情失败，请稍后重试', 'danger');
    }
}

// 一键擦亮
async function polishAccountItems(accountId) {
    toggleLoading(true);
    showToast('正在擦亮所有商品，请稍候...', 'info');
    try {
        const response = await fetch(`${apiBase}/accounts/${encodeURIComponent(accountId)}/polish-items`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        const data = await response.json();
        if (data.success) {
            showToast(`擦亮完成: ${data.polished}/${data.total} 个商品成功`, 'success');
        } else {
            showToast(`擦亮失败: ${data.message}`, 'danger');
        }
    } catch (error) {
        showToast(`擦亮请求异常: ${error.message}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 刷新真实Cookie
async function refreshRealCookie(cookieId) {
    if (!cookieId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    // 获取当前cookie值
    try {
        const currentCookie = await fetchJSON(`${apiBase}/cookie/${encodeURIComponent(cookieId)}/details?include_secrets=true`);

        if (!currentCookie || !currentCookie.value) {
            showToast('未找到有效的Cookie信息', 'warning');
            return;
        }

        // 确认操作
        if (!confirm(`确定要刷新账号 "${cookieId}" 的真实Cookie吗？\n\n此操作将使用当前Cookie访问闲鱼IM界面获取最新的真实Cookie。`)) {
            return;
        }

        // 显示加载状态
        const button = event.target.closest('button');
        const originalContent = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<i class="bi bi-arrow-clockwise spin"></i>';

        // 调用刷新API
        const response = await fetch(`${apiBase}/qr-login/refresh-cookies`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                qr_cookies: currentCookie.value,
                cookie_id: cookieId
            })
        });

        const result = await response.json();

        if (result.success) {
            showToast(`账号 "${cookieId}" 真实Cookie刷新成功`, 'success');
            // 刷新账号列表以显示更新后的cookie
            loadCookies();
        } else {
            showToast(`真实Cookie刷新失败: ${result.message}`, 'danger');
        }

    } catch (error) {
        console.error('刷新真实Cookie失败:', error);
        showToast(`刷新真实Cookie失败: ${error.message || '未知错误'}`, 'danger');
    } finally {
        // 恢复按钮状态
        const button = event.target.closest('button');
        if (button) {
            button.disabled = false;
            button.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
        }
    }
}

// 显示冷却状态
async function showCooldownStatus(cookieId) {
    if (!cookieId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/qr-login/cooldown-status/${cookieId}`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            const { remaining_time, cooldown_duration, is_in_cooldown, remaining_minutes, remaining_seconds } = result;

            let statusMessage = `账号: ${cookieId}\n`;
            statusMessage += `冷却时长: ${cooldown_duration / 60}分钟\n`;

            if (is_in_cooldown) {
                statusMessage += `冷却状态: 进行中\n`;
                statusMessage += `剩余时间: ${remaining_minutes}分${remaining_seconds}秒\n\n`;
                statusMessage += `在冷却期间，_refresh_cookies_via_browser 方法将被跳过。\n\n`;
                statusMessage += `是否要重置冷却时间？`;

                if (confirm(statusMessage)) {
                    await resetCooldownTime(cookieId);
                }
            } else {
                statusMessage += `冷却状态: 无冷却\n`;
                statusMessage += `可以正常执行 _refresh_cookies_via_browser 方法`;
                alert(statusMessage);
            }
        } else {
            showToast(`获取冷却状态失败: ${result.message}`, 'danger');
        }

    } catch (error) {
        console.error('获取冷却状态失败:', error);
        showToast(`获取冷却状态失败: ${error.message || '未知错误'}`, 'danger');
    }
}

// 重置冷却时间
async function resetCooldownTime(cookieId) {
    if (!cookieId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/qr-login/reset-cooldown/${cookieId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            const previousTime = result.previous_remaining_time || 0;
            const previousMinutes = Math.floor(previousTime / 60);
            const previousSeconds = previousTime % 60;

            let message = `账号 "${cookieId}" 的扫码登录冷却时间已重置`;
            if (previousTime > 0) {
                message += `\n原剩余时间: ${previousMinutes}分${previousSeconds}秒`;
            }

            showToast(message, 'success');
        } else {
            showToast(`重置冷却时间失败: ${result.message}`, 'danger');
        }

    } catch (error) {
        console.error('重置冷却时间失败:', error);
        showToast(`重置冷却时间失败: ${error.message || '未知错误'}`, 'danger');
    }
}

// 删除Cookie
async function delCookie(id) {
    if (!confirm(`确定要删除账号 "${id}" 吗？此操作不可恢复。`)) return;

    try {
    await fetchJSON(apiBase + `/cookies/${id}`, { method: 'DELETE' });
    showToast(`账号 "${id}" 已删除`, 'success');
    loadCookies();
    } catch (err) {
    // 错误已在fetchJSON中处理
    }
}

// 内联编辑Cookie
async function editCookieInline(id, currentValue) {
    try {
        toggleLoading(true);
        
        // 获取账号详细信息
        const details = await fetchJSON(apiBase + `/cookie/${id}/details?include_secrets=true`);
        
        // 打开编辑模态框
        openAccountEditModal(details);
    } catch (err) {
        console.error('获取账号详情失败:', err);
        showToast(`获取账号详情失败: ${err.message || '未知错误'}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 打开账号编辑模态框
async function openAccountEditModal(accountData) {
    // 设置模态框数据
    document.getElementById('accountEditId').value = accountData.id;
    document.getElementById('editAccountCookie').value = accountData.value || '';
    document.getElementById('editAccountUsername').value = accountData.username || '';
    document.getElementById('editAccountPassword').value = accountData.password || '';
    document.getElementById('editAccountShowBrowser').checked = accountData.show_browser || false;
    
    // 显示账号ID
    document.getElementById('accountEditIdDisplay').textContent = accountData.id;
    
    // 加载代理配置
    try {
        const proxyData = await fetchJSON(apiBase + `/cookie/${accountData.id}/proxy?include_secret=true`);
        if (proxyData && proxyData.data) {
            document.getElementById('editProxyType').value = proxyData.data.proxy_type || 'none';
            document.getElementById('editProxyHost').value = proxyData.data.proxy_host || '';
            document.getElementById('editProxyPort').value = proxyData.data.proxy_port || '';
            document.getElementById('editProxyUser').value = proxyData.data.proxy_user || '';
            document.getElementById('editProxyPass').value = proxyData.data.proxy_pass || '';
        } else {
            // 设置默认值
            document.getElementById('editProxyType').value = 'none';
            document.getElementById('editProxyHost').value = '';
            document.getElementById('editProxyPort').value = '';
            document.getElementById('editProxyUser').value = '';
            document.getElementById('editProxyPass').value = '';
        }
        // 更新代理字段显示状态
        toggleProxyFields();
    } catch (err) {
        console.error('加载代理配置失败:', err);
        // 设置默认值
        document.getElementById('editProxyType').value = 'none';
        toggleProxyFields();
    }
    
    // 打开模态框
    const modal = new bootstrap.Modal(document.getElementById('accountEditModal'));
    modal.show();
    
    // 初始化模态框中的 tooltips
    setTimeout(() => {
        initTooltips();
    }, 100);
}

// 切换代理配置字段显示
function toggleProxyFields() {
    const proxyType = document.getElementById('editProxyType').value;
    const showProxy = proxyType !== 'none';
    
    document.getElementById('proxyHostGroup').style.display = showProxy ? 'block' : 'none';
    document.getElementById('proxyPortGroup').style.display = showProxy ? 'block' : 'none';
    document.getElementById('proxyAuthGroup').style.display = showProxy ? 'flex' : 'none';
}

// 保存账号编辑
async function saveAccountEdit() {
    const id = document.getElementById('accountEditId').value;
    const cookie = document.getElementById('editAccountCookie').value.trim();
    const username = document.getElementById('editAccountUsername').value.trim();
    const password = document.getElementById('editAccountPassword').value.trim();
    const showBrowser = document.getElementById('editAccountShowBrowser').checked;
    
    // 代理配置
    const proxyType = document.getElementById('editProxyType').value;
    const proxyHost = document.getElementById('editProxyHost').value.trim();
    const proxyPort = parseInt(document.getElementById('editProxyPort').value) || 0;
    const proxyUser = document.getElementById('editProxyUser').value.trim();
    const proxyPass = document.getElementById('editProxyPass').value.trim();
    
    if (!cookie) {
        showToast('Cookie值不能为空', 'warning');
        return;
    }
    
    // 如果选择了代理，验证必要字段
    if (proxyType !== 'none') {
        if (!proxyHost) {
            showToast('请输入代理服务器地址', 'warning');
            return;
        }
        if (!proxyPort || proxyPort <= 0) {
            showToast('请输入有效的代理端口', 'warning');
            return;
        }
    }
    
    try {
        toggleLoading(true);
        
        // 保存账号基本信息
        await fetchJSON(apiBase + `/cookie/${id}/account-info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                value: cookie,
                username: username,
                password: password,
                show_browser: showBrowser
            })
        });
        
        // 保存代理配置
        await fetchJSON(apiBase + `/cookie/${id}/proxy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                proxy_type: proxyType,
                proxy_host: proxyHost,
                proxy_port: proxyPort,
                proxy_user: proxyUser,
                proxy_pass: proxyPass
            })
        });
        
        showToast(`账号 "${id}" 信息已更新`, 'success');
        
        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('accountEditModal'));
        modal.hide();
        
        // 重新加载账号列表
        loadCookies();
    } catch (err) {
        console.error('保存账号信息失败:', err);
        showToast(`保存失败: ${err.message || '未知错误'}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 保存内联编辑的Cookie
async function saveCookieInline(id) {
    const input = document.getElementById(`edit-${id}`);
    const newValue = input.value.trim();

    if (!newValue) {
    showToast('Cookie值不能为空', 'warning');
    return;
    }

    try {
    toggleLoading(true);

    await fetchJSON(apiBase + `/cookies/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
        id: id,
        value: newValue
        })
    });

    showToast(`账号 "${id}" Cookie已更新`, 'success');
    loadCookies(); // 重新加载列表

    } catch (err) {
    console.error('Cookie更新失败:', err);
    showToast(`Cookie更新失败: ${err.message || '未知错误'}`, 'danger');
    // 恢复原内容
    cancelCookieEdit(id);
    } finally {
    toggleLoading(false);
    }
}

// 取消Cookie编辑
function cancelCookieEdit(id) {
    if (!window.editingCookieData || window.editingCookieData.id !== id) {
    console.error('编辑数据不存在');
    return;
    }

    const row = document.querySelector(`#edit-${id}`).closest('tr');
    const cookieValueCell = row.querySelector('.cookie-value');

    // 恢复原内容
    cookieValueCell.innerHTML = window.editingCookieData.originalContent;

    // 恢复按钮状态
    const actionButtons = row.querySelectorAll('.account-actions-toolbar button, .btn-group button');
    actionButtons.forEach(btn => btn.disabled = false);

    // 清理全局数据
    delete window.editingCookieData;
}



// 切换账号启用/禁用状态
async function toggleAccountStatus(accountId, enabled) {
    try {
    toggleLoading(true);

    // 这里需要调用后端API来更新账号状态
    // 由于当前后端可能没有enabled字段，我们先在前端模拟
    // 实际项目中需要后端支持

    const response = await fetch(`${apiBase}/cookies/${accountId}/status`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ enabled: enabled })
    });

    if (response.ok) {
        const result = await response.json();
        showToast(`账号 "${accountId}" 已${enabled ? '启用' : '禁用'}`, 'success');

        // 清除相关缓存，确保数据一致性
        clearKeywordCache();

        // 更新界面显示
        updateAccountRowStatus(accountId, enabled, result.status_note || '');

        // 刷新自动回复页面的账号列表
        refreshAccountList();
        if (dashboardData.accounts.length) {
            await refreshDashboardRuntimeSnapshots();
        }

        // 如果禁用的账号在自动回复页面被选中，更新显示
        const accountSelect = document.getElementById('accountSelect');
        if (accountSelect && accountSelect.value === accountId) {
        if (!enabled) {
            // 更新徽章显示禁用状态
            updateAccountBadge(accountId, false);
            showToast('账号已禁用，配置的关键词不会参与自动回复', 'warning');
        } else {
            // 更新徽章显示启用状态
            updateAccountBadge(accountId, true);
            showToast('账号已启用，配置的关键词将参与自动回复', 'success');
        }
        }

    } else {
        // 如果后端不支持，先在前端模拟
        console.warn('后端暂不支持账号状态切换，使用前端模拟');
        showToast(`账号 "${accountId}" 已${enabled ? '启用' : '禁用'} (前端模拟)`, enabled ? 'success' : 'warning');
        updateAccountRowStatus(accountId, enabled);
    }

    } catch (error) {
    console.error('切换账号状态失败:', error);

    // 后端不支持时的降级处理
    showToast(`账号 "${accountId}" 已${enabled ? '启用' : '禁用'} (本地模拟)`, enabled ? 'success' : 'warning');
    updateAccountRowStatus(accountId, enabled);

    // 恢复切换按钮状态
    const toggle = document.querySelector(`input[onchange*="${accountId}"]`);
    if (toggle) {
        toggle.checked = enabled;
    }
    } finally {
    toggleLoading(false);
    }
}

// 更新账号行的状态显示
function updateAccountRowStatus(accountId, enabled, statusNote = '') {
    const toggle = document.querySelector(`input[onchange*="${accountId}"]`);
    if (!toggle) return;

    const row = toggle.closest('tr');
    const statusBadge = row.querySelector('.status-badge');
    const statusCell = row.querySelector('.account-status-cell');
    const actionButtons = row.querySelectorAll('.account-actions-toolbar .btn[data-requires-enabled="true"], .btn-group .btn:not(.btn-outline-info):not(.btn-outline-danger)');

    // 更新行样式
    row.className = `account-row ${enabled ? 'enabled' : 'disabled'}`;

    // 更新状态徽章
    statusBadge.className = `status-badge ${enabled ? 'enabled' : 'disabled'}`;
    statusBadge.title = enabled ? '账号已启用' : '账号已禁用';
    statusBadge.innerHTML = `
    <i class="bi bi-${enabled ? 'check-circle-fill' : 'x-circle-fill'}"></i>
    `;

    const existingStatusNote = statusCell?.querySelector('.account-status-note-badge');
    const renderedStatusNote = renderStatusNoteBadge(statusNote, 'account-status-note-badge').trim();
    if (existingStatusNote) {
        existingStatusNote.remove();
    }
    if (statusCell && renderedStatusNote) {
        statusCell.insertAdjacentHTML('beforeend', renderedStatusNote);
    }

    // 更新依赖账号启用状态的按钮；自动回复规则入口始终可用
    actionButtons.forEach(btn => {
    if (btn.dataset.requiresEnabled === 'true') {
        btn.disabled = !enabled;
    }
    if (btn.onclick && btn.onclick.toString().includes('goToAutoReply')) {
        btn.title = enabled ? '设置自动回复' : '配置关键词 (账号已禁用)';
    }
    });

    const redFlowerRunButton = row.querySelector('[data-red-flower-run]');
    if (redFlowerRunButton) {
        const redFlowerEnabled = redFlowerRunButton.dataset.redFlowerActive === 'true';
        redFlowerRunButton.disabled = !enabled || !redFlowerEnabled;
    }

    // 更新切换按钮的提示
    const label = toggle.closest('.status-toggle');
    label.title = enabled ? '点击禁用' : '点击启用';
}

// 切换自动确认发货状态
async function toggleAutoConfirm(accountId, enabled) {
    try {
    toggleLoading(true);

    const response = await fetch(`${apiBase}/cookies/${accountId}/auto-confirm`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ auto_confirm: enabled })
    });

    if (response.ok) {
        const result = await response.json();
        showToast(result.message, 'success');

        // 更新界面显示
        updateAutoConfirmRowStatus(accountId, enabled);
    } else {
        const error = await response.json();
        showToast(error.detail || '更新自动确认发货设置失败', 'error');

        // 恢复切换按钮状态
        const toggle = document.querySelector(`input[onchange*="toggleAutoConfirm('${accountId}'"]`);
        if (toggle) {
        toggle.checked = !enabled;
        }
    }

    } catch (error) {
    console.error('切换自动确认发货状态失败:', error);
    showToast('网络错误，请稍后重试', 'error');

    // 恢复切换按钮状态
    const toggle = document.querySelector(`input[onchange*="toggleAutoConfirm('${accountId}'"]`);
    if (toggle) {
        toggle.checked = !enabled;
    }
    } finally {
    toggleLoading(false);
    }
}

// 更新自动确认发货行状态
function updateAutoConfirmRowStatus(accountId, enabled) {
    const row = document.querySelector(`tr:has(input[onchange*="toggleAutoConfirm('${accountId}'"])`);
    if (!row) return;

    const statusBadge = row.querySelector('.status-badge:has(i.bi-truck, i.bi-truck-flatbed)');
    const toggle = row.querySelector(`input[onchange*="toggleAutoConfirm('${accountId}'"]`);

    if (statusBadge && toggle) {
    // 更新状态徽章
    statusBadge.className = `status-badge ${enabled ? 'enabled' : 'disabled'}`;
    statusBadge.title = enabled ? '自动确认发货已开启' : '自动确认发货已关闭';
    statusBadge.innerHTML = `
        <i class="bi bi-${enabled ? 'truck' : 'truck-flatbed'}"></i>
    `;

    // 更新切换按钮的提示
    const label = toggle.closest('.status-toggle');
    label.title = enabled ? '点击关闭自动确认发货' : '点击开启自动确认发货';
    }
}

// 切换自动好评状态
async function toggleAutoComment(accountId, enabled) {
    try {
        toggleLoading(true);

        const response = await fetch(`${apiBase}/cookies/${accountId}/auto-comment`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ auto_comment: enabled })
        });

        if (response.ok) {
            const result = await response.json();
            showToast(result.message, 'success');

            // 更新界面显示
            updateAutoCommentRowStatus(accountId, enabled);
        } else {
            const error = await response.json();
            showToast(error.detail || '更新自动好评设置失败', 'error');

            // 恢复切换按钮状态
            const toggle = document.querySelector(`input[onchange*="toggleAutoComment('${accountId}'"]`);
            if (toggle) {
                toggle.checked = !enabled;
            }
        }

    } catch (error) {
        console.error('切换自动好评状态失败:', error);
        showToast('网络错误，请稍后重试', 'error');

        // 恢复切换按钮状态
        const toggle = document.querySelector(`input[onchange*="toggleAutoComment('${accountId}'"]`);
        if (toggle) {
            toggle.checked = !enabled;
        }
    } finally {
        toggleLoading(false);
    }
}

// 更新自动好评行状态
function updateAutoCommentRowStatus(accountId, enabled) {
    const row = document.querySelector(`tr:has(input[onchange*="toggleAutoComment('${accountId}'"])`);
    if (!row) return;

    const statusBadge = row.querySelector('.status-badge:has(i.bi-star, i.bi-star-fill)');
    const toggle = row.querySelector(`input[onchange*="toggleAutoComment('${accountId}'"]`);

    if (statusBadge && toggle) {
        // 更新状态徽章
        statusBadge.className = `status-badge ${enabled ? 'enabled' : 'disabled'}`;
        statusBadge.title = enabled ? '自动好评已开启' : '自动好评已关闭';
        statusBadge.innerHTML = `
            <i class="bi bi-${enabled ? 'star-fill' : 'star'}"></i>
        `;

        // 更新切换按钮的提示
        const label = toggle.closest('.status-toggle');
        label.title = enabled ? '点击关闭自动好评' : '点击开启自动好评';
    }
}

async function runHistoricalAutoComment(accountIds, options = {}) {
    const normalizedIds = Array.from(new Set((accountIds || []).map(id => String(id || '').trim()).filter(Boolean)));
    if (normalizedIds.length === 0) {
        showToast('当前没有可补评的账号', 'warning');
        return;
    }

    const confirmText = options.confirmText || `确定要为 ${normalizedIds.length} 个账号执行历史订单补评价吗？\n\n将从闲鱼待评价列表拉取订单，并按账号激活的好评模板逐单评价。`;
    if (!options.skipConfirm && !confirm(confirmText)) return;

    toggleLoading(true);
    showToast('正在执行历史订单补评价，请稍候...', 'info');
    try {
        const response = await fetch(`${apiBase}/api/auto-comment/batch-rate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ cookie_ids: normalizedIds, page_size: 100 })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) {
            showToast(data.detail || data.message || '历史补评价失败', 'danger');
            return;
        }

        const stats = data.data || {};
        const failedDetails = (stats.details || []).filter(item => !item.success || (item.failed_count || 0) > 0);
        const summary = `历史补评完成：评价 ${stats.total_rated || 0} 笔，失败 ${stats.total_failed || 0} 笔，待评 ${stats.total_pending || 0} 笔`;
        if (failedDetails.length > 0) {
            const failedText = failedDetails.slice(0, 3).map(item => `${item.account_id}：${item.message}`).join('；');
            showToast(`${summary}。${failedText}`, 'warning');
        } else {
            showToast(data.message || summary, 'success');
        }
        await loadCookies();
    } catch (error) {
        console.error('历史补评价失败:', error);
        showToast(`历史补评价请求异常: ${error.message}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 批量执行当前账号列表的历史订单补评价
async function runHistoricalAutoCommentForAllAccounts() {
    const accountIds = Array.from(document.querySelectorAll('#cookieTable tbody .cookie-id strong'))
        .map(el => (el.textContent || '').trim())
        .filter(Boolean);
    await runHistoricalAutoComment(accountIds, {
        confirmText: `确定要为当前列表中的 ${accountIds.length} 个账号执行历史订单补评价吗？\n\n将从闲鱼待评价列表拉取订单，并按账号激活的好评模板逐单评价。`
    });
}

// 执行单个账号的历史订单补评价
async function runHistoricalAutoCommentForAccount(accountId) {
    if (!accountId) {
        showToast('缺少账号ID', 'warning');
        return;
    }
    await runHistoricalAutoComment([accountId], {
        confirmText: `确定要为账号「${accountId}」执行历史订单补评价吗？`
    });
}

// 切换自动求小红花状态
async function toggleAutoRedFlower(accountId, enabled) {
    try {
        toggleLoading(true);

        const response = await fetch(`${apiBase}/cookies/${accountId}/auto-red-flower`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ auto_red_flower: enabled })
        });

        if (response.ok) {
            const result = await response.json();
            showToast(result.message || (enabled ? '已开启自动求小红花' : '已关闭自动求小红花'), 'success');
            await loadCookies();
        } else {
            const error = await response.json().catch(() => ({}));
            showToast(error.detail || '更新自动求小红花设置失败', 'error');
        }
    } catch (error) {
        console.error('切换自动求小红花状态失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    } finally {
        toggleLoading(false);
    }
}

// 立即执行当前账号的求小红花补偿
async function runAutoRedFlowerForAccount(accountId) {
    if (!accountId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    const confirmed = confirm(`确定要立即为账号「${accountId}」执行一轮求小红花吗？`);
    if (!confirmed) return;

    toggleLoading(true);
    showToast('正在执行求小红花，请稍候...', 'info');
    try {
        const response = await fetch(`${apiBase}/api/auto-red-flower/run-once`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ cookie_id: accountId })
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) {
            showToast(data.detail || data.message || '求小红花执行失败', 'danger');
            return;
        }
        const stats = data?.data?.stats || {};
        showToast(
            `求小红花完成：处理 ${stats.orders || 0} 单，成功 ${stats.success || 0}，失败 ${stats.failed || 0}，跳过 ${stats.skipped || 0}`,
            (stats.failed || 0) > 0 ? 'warning' : 'success'
        );
        await loadCookies();
    } catch (error) {
        console.error('执行求小红花失败:', error);
        showToast(`求小红花请求异常: ${error.message}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 当前编辑的好评模板账号ID
let currentCommentTemplateAccountId = null;

// 显示好评模板管理弹窗
async function showCommentTemplates(accountId) {
    currentCommentTemplateAccountId = accountId;
    
    try {
        toggleLoading(true);
        
        // 获取好评模板列表
        const response = await fetch(`${apiBase}/cookies/${accountId}/comment-templates`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (!response.ok) {
            throw new Error('获取好评模板列表失败');
        }
        
        const data = await response.json();
        const templates = data.templates || [];
        
        // 生成模板列表HTML
        let templatesHtml = '';
        if (templates.length === 0) {
            templatesHtml = '<div class="text-center text-muted py-4"><i class="bi bi-inbox fs-1 d-block mb-2"></i>暂无好评模板，请添加</div>';
        } else {
            templatesHtml = templates.map(template => `
                <div class="card mb-2 ${template.is_active ? 'border-success' : ''}">
                    <div class="card-body py-2 px-3">
                        <div class="d-flex justify-content-between align-items-start">
                            <div class="flex-grow-1">
                                <div class="d-flex align-items-center mb-1">
                                    <strong class="me-2">${escapeHtml(template.name)}</strong>
                                    ${template.is_active ? '<span class="badge bg-success">使用中</span>' : ''}
                                </div>
                                <p class="mb-0 text-muted small" style="white-space: pre-wrap; max-height: 60px; overflow: hidden;">${escapeHtml(template.content)}</p>
                            </div>
                            <div class="btn-group btn-group-sm ms-2">
                                ${!template.is_active ? `<button class="btn btn-outline-success" onclick="activateCommentTemplate('${accountId}', ${template.id})" title="使用此模板"><i class="bi bi-check-circle"></i></button>` : ''}
                                <button class="btn btn-outline-primary" onclick="editCommentTemplate(${template.id}, '${escapeHtml(template.name)}', '${escapeHtml(template.content)}')" title="编辑"><i class="bi bi-pencil"></i></button>
                                <button class="btn btn-outline-danger" onclick="deleteCommentTemplate('${accountId}', ${template.id})" title="删除"><i class="bi bi-trash"></i></button>
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        // 显示模态框
        const modalHtml = `
            <div class="modal fade" id="commentTemplatesModal" tabindex="-1" aria-labelledby="commentTemplatesModalLabel" aria-hidden="true">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="commentTemplatesModalLabel">
                                <i class="bi bi-star-fill text-warning me-2"></i>好评模板管理 - ${accountId}
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <div class="mb-3">
                                <button class="btn btn-primary" onclick="showAddCommentTemplateForm()">
                                    <i class="bi bi-plus-circle me-1"></i>添加模板
                                </button>
                            </div>
                            <div id="addTemplateForm" class="card mb-3" style="display: none;">
                                <div class="card-body">
                                    <h6 class="card-title">添加新模板</h6>
                                    <div class="mb-2">
                                        <label class="form-label">模板名称</label>
                                        <input type="text" class="form-control" id="newTemplateName" placeholder="例如：默认好评">
                                    </div>
                                    <div class="mb-2">
                                        <label class="form-label">好评内容</label>
                                        <textarea class="form-control" id="newTemplateContent" rows="3" placeholder="请输入好评内容..."></textarea>
                                    </div>
                                    <div class="form-check mb-2">
                                        <input class="form-check-input" type="checkbox" id="newTemplateActive">
                                        <label class="form-check-label" for="newTemplateActive">立即使用此模板</label>
                                    </div>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-success" onclick="addCommentTemplate()">保存</button>
                                        <button class="btn btn-secondary" onclick="hideAddCommentTemplateForm()">取消</button>
                                    </div>
                                </div>
                            </div>
                            <div id="editTemplateForm" class="card mb-3" style="display: none;">
                                <div class="card-body">
                                    <h6 class="card-title">编辑模板</h6>
                                    <input type="hidden" id="editTemplateId">
                                    <div class="mb-2">
                                        <label class="form-label">模板名称</label>
                                        <input type="text" class="form-control" id="editTemplateName">
                                    </div>
                                    <div class="mb-2">
                                        <label class="form-label">好评内容</label>
                                        <textarea class="form-control" id="editTemplateContent" rows="3"></textarea>
                                    </div>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-success" onclick="saveEditCommentTemplate()">保存</button>
                                        <button class="btn btn-secondary" onclick="hideEditCommentTemplateForm()">取消</button>
                                    </div>
                                </div>
                            </div>
                            <div id="templatesList">
                                ${templatesHtml}
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // 检查模态框是否已存在
        const existingModalEl = document.getElementById('commentTemplatesModal');
        if (existingModalEl) {
            // 模态框已存在，只更新模板列表内容
            const templatesList = existingModalEl.querySelector('#templatesList');
            if (templatesList) {
                templatesList.innerHTML = templatesHtml;
            }
            // 隐藏添加和编辑表单
            const addForm = existingModalEl.querySelector('#addTemplateForm');
            const editForm = existingModalEl.querySelector('#editTemplateForm');
            if (addForm) addForm.style.display = 'none';
            if (editForm) editForm.style.display = 'none';
        } else {
            // 模态框不存在，创建新的
            // 先清理可能残留的遮罩层
            document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            
            // 添加新模态框
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            
            // 显示模态框
            const modal = new bootstrap.Modal(document.getElementById('commentTemplatesModal'));
            modal.show();
        }
        
    } catch (error) {
        console.error('获取好评模板失败:', error);
        showToast('获取好评模板失败: ' + error.message, 'error');
    } finally {
        toggleLoading(false);
    }
}

// 显示添加模板表单
function showAddCommentTemplateForm() {
    document.getElementById('addTemplateForm').style.display = 'block';
    document.getElementById('editTemplateForm').style.display = 'none';
    document.getElementById('newTemplateName').value = '';
    document.getElementById('newTemplateContent').value = '';
    document.getElementById('newTemplateActive').checked = false;
}

// 隐藏添加模板表单
function hideAddCommentTemplateForm() {
    document.getElementById('addTemplateForm').style.display = 'none';
}

// 添加好评模板
async function addCommentTemplate() {
    const name = document.getElementById('newTemplateName').value.trim();
    const content = document.getElementById('newTemplateContent').value.trim();
    const isActive = document.getElementById('newTemplateActive').checked;
    
    if (!name) {
        showToast('请输入模板名称', 'warning');
        return;
    }
    if (!content) {
        showToast('请输入好评内容', 'warning');
        return;
    }
    
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${currentCommentTemplateAccountId}/comment-templates`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                name: name,
                content: content,
                is_active: isActive
            })
        });
        
        if (response.ok) {
            showToast('添加好评模板成功', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(currentCommentTemplateAccountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '添加好评模板失败', 'error');
        }
    } catch (error) {
        console.error('添加好评模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// 编辑好评模板
function editCommentTemplate(templateId, name, content) {
    document.getElementById('addTemplateForm').style.display = 'none';
    document.getElementById('editTemplateForm').style.display = 'block';
    document.getElementById('editTemplateId').value = templateId;
    document.getElementById('editTemplateName').value = name;
    document.getElementById('editTemplateContent').value = content;
}

// 隐藏编辑模板表单
function hideEditCommentTemplateForm() {
    document.getElementById('editTemplateForm').style.display = 'none';
}

// 保存编辑的好评模板
async function saveEditCommentTemplate() {
    const templateId = document.getElementById('editTemplateId').value;
    const name = document.getElementById('editTemplateName').value.trim();
    const content = document.getElementById('editTemplateContent').value.trim();
    
    if (!name) {
        showToast('请输入模板名称', 'warning');
        return;
    }
    if (!content) {
        showToast('请输入好评内容', 'warning');
        return;
    }
    
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${currentCommentTemplateAccountId}/comment-templates/${templateId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                name: name,
                content: content
            })
        });
        
        if (response.ok) {
            showToast('更新好评模板成功', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(currentCommentTemplateAccountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '更新好评模板失败', 'error');
        }
    } catch (error) {
        console.error('更新好评模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// 删除好评模板
async function deleteCommentTemplate(accountId, templateId) {
    if (!confirm('确定要删除此好评模板吗？')) {
        return;
    }
    
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${accountId}/comment-templates/${templateId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            showToast('删除好评模板成功', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(accountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '删除好评模板失败', 'error');
        }
    } catch (error) {
        console.error('删除好评模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// 激活好评模板
async function activateCommentTemplate(accountId, templateId) {
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${accountId}/comment-templates/${templateId}/activate`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            showToast('已切换使用此模板', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(accountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '切换模板失败', 'error');
        }
    } catch (error) {
        console.error('切换模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// HTML转义函数
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 跳转到自动回复页面并选择指定账号
function goToAutoReply(accountId) {
    // 切换到自动回复页面
    showSection('auto-reply');

    // 设置账号选择器的值
    setTimeout(() => {
    const accountSelect = document.getElementById('accountSelect');
    if (accountSelect) {
        accountSelect.value = accountId;
        // 触发change事件来加载关键词
        loadAccountKeywords();
    }
    }, 100);

    showToast(`已切换到自动回复页面，账号 "${accountId}" 已选中`, 'info');
}





// 登出功能
async function logout() {
    // 停止销售额摘要定时刷新
    stopSalesSummaryRefreshTimer();
    
    try {
    if (window.RemoteVerificationConsole?.revoke) {
        await window.RemoteVerificationConsole.revoke();
    }
    if (authToken) {
        await fetch('/logout', {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
        });
    }
    localStorage.removeItem('auth_token');
    window.location.href = '/';
    } catch (err) {
    console.error('登出失败:', err);
    localStorage.removeItem('auth_token');
    window.location.href = '/';
    }
}

// 检查认证状态
async function checkAuth() {
    const token = getAuthToken();
    if (!token) {
    window.location.href = '/';
    return false;
    }

    try {
    const response = await fetch('/verify', {
        cache: 'no-store',
        headers: {
        'Authorization': `Bearer ${token}`
        }
    });
    const result = await response.json();

    if (!result.authenticated) {
        localStorage.removeItem('auth_token');
        window.location.href = '/';
        return false;
    }

    applyFrontendConnectionMode(result.connection_mode);

    // 检查是否为管理员，显示管理员菜单和功能
    if (result.is_admin === true) {
        const adminMenuSection = document.getElementById('adminMenuSection');
        if (adminMenuSection) {
        adminMenuSection.style.display = 'block';
        }

        // 显示备份管理功能
        const backupManagement = document.getElementById('backup-management');
        if (backupManagement) {
        backupManagement.style.display = 'block';
        }

        // 显示系统重启功能
        const systemRestartBtn = document.getElementById('system-restart-btn');
        if (systemRestartBtn) {
        systemRestartBtn.style.display = 'inline-block';
        }

        const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');
        if (dashboardHotUpdateGroup) {
        dashboardHotUpdateGroup.style.display = 'inline-flex';
        }

        // 显示登录与注册设置
        const loginInfoSettings = document.getElementById('login-info-settings');
        if (loginInfoSettings) {
        loginInfoSettings.style.display = 'flex';
        }

        const riskControlSettings = document.getElementById('risk-control-settings');
        if (riskControlSettings) {
        riskControlSettings.style.display = 'block';
        }

        await loadRiskControlNightSettings();
    } else {
        const riskControlSettings = document.getElementById('risk-control-settings');
        if (riskControlSettings) {
        riskControlSettings.style.display = 'none';
        }
    }

    return true;
    } catch (err) {
    localStorage.removeItem('auth_token');
    window.location.href = '/';
    return false;
    }
}

// 初始化事件监听
document.addEventListener('DOMContentLoaded', async () => {
    // 首先检查认证状态
    const isAuthenticated = await checkAuth();
    if (!isAuthenticated) return;

    // 初始化侧边栏折叠状态
    initSidebarCollapse();
    // 初始化暗色模式
    initDarkMode();
    // 初始化账号保活诊断事件
    initAboutDiagnosticsEvents();
    // 加载系统版本号
    loadSystemVersion();
    // 加载防抖延迟设置
    loadDebounceDelay();
    // 添加Cookie表单提交
    document.getElementById('addForm').addEventListener('submit', handleManualCookieImport);

    // 添加账号密码登录表单提交
    const passwordLoginForm = document.getElementById('passwordLoginFormElement');
    if (passwordLoginForm) {
        passwordLoginForm.addEventListener('submit', handlePasswordLogin);
    }

    // 增强的键盘快捷键和用户体验
    // textarea 中 Enter 允许换行，Ctrl+Enter 提交
    document.getElementById('newKeyword')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        addKeyword();
    }
    });

    document.getElementById('newReply')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        addKeyword();
    }
    });

    // ESC键取消编辑
    document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && typeof window.editingIndex !== 'undefined') {
        e.preventDefault();
        cancelEdit();
    }
    });

    // 输入框实时验证和提示
    document.getElementById('newKeyword')?.addEventListener('input', function(e) {
    const value = e.target.value.trim();
    const addBtn = document.querySelector('.add-btn');
    const replyInput = document.getElementById('newReply');

    if (value.length > 0) {
        e.target.style.borderColor = '#10b981';
        // 只要关键词有内容就可以添加，不需要回复内容
        addBtn.style.opacity = '1';
        addBtn.style.transform = 'scale(1)';
    } else {
        e.target.style.borderColor = '#e5e7eb';
        addBtn.style.opacity = '0.7';
        addBtn.style.transform = 'scale(0.95)';
    }
    });

    document.getElementById('newReply')?.addEventListener('input', function(e) {
    const value = e.target.value.trim();
    const keywordInput = document.getElementById('newKeyword');

    // 回复内容可以为空，只需要关键词有内容即可
    if (value.length > 0) {
        e.target.style.borderColor = '#10b981';
    } else {
        e.target.style.borderColor = '#e5e7eb';
    }

    // 按钮状态只依赖关键词是否有内容
    const addBtn = document.querySelector('.add-btn');
    if (keywordInput.value.trim().length > 0) {
        addBtn.style.opacity = '1';
        addBtn.style.transform = 'scale(1)';
    } else {
        addBtn.style.opacity = '0.7';
        addBtn.style.transform = 'scale(0.95)';
    }
    });

    // 初始加载仪表盘
    loadDashboard();

    // 加载菜单设置并应用
    loadMenuSettings();

    // 初始化图片关键词事件监听器
    initImageKeywordEventListeners();

    // 初始化卡券图片文件选择器
    initCardImageFileSelector();

    // 初始化编辑卡券图片文件选择器
    initEditCardImageFileSelector();

    // 初始化工具提示
    initTooltips();

    // 初始化商品搜索功能
    initItemsSearch();

    // 初始化商品搜索界面功能
    initItemSearch();

    // 点击侧边栏外部关闭移动端菜单
    document.addEventListener('click', function(e) {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.querySelector('.mobile-toggle');

    if (window.innerWidth <= 768 &&
        !sidebar.contains(e.target) &&
        !toggle.contains(e.target) &&
        sidebar.classList.contains('show')) {
        sidebar.classList.remove('show');
    }
    });
});

// ==================== 默认回复管理功能 ====================

// 打开默认回复管理器
async function openDefaultReplyManager() {
    try {
    await loadDefaultReplies();
    const modal = new bootstrap.Modal(document.getElementById('defaultReplyModal'));
    modal.show();
    } catch (error) {
    console.error('打开默认回复管理器失败:', error);
    showToast('打开默认回复管理器失败', 'danger');
    }
}

// 加载默认回复列表
async function loadDefaultReplies() {
    try {
    // 获取所有账号
    const accountsResponse = await fetch(`${apiBase}/cookies`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!accountsResponse.ok) {
        throw new Error('获取账号列表失败');
    }

    const accounts = await accountsResponse.json();

    // 获取所有默认回复设置
    const repliesResponse = await fetch(`${apiBase}/default-replies`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let defaultReplies = {};
    if (repliesResponse.ok) {
        defaultReplies = await repliesResponse.json();
    }

    renderDefaultRepliesList(accounts, defaultReplies);
    } catch (error) {
    console.error('加载默认回复列表失败:', error);
    showToast('加载默认回复列表失败', 'danger');
    }
}

// 渲染默认回复列表
function renderDefaultRepliesList(accounts, defaultReplies) {
    const tbody = document.getElementById('defaultReplyTableBody');
    tbody.innerHTML = '';

    if (accounts.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="5" class="text-center py-4 text-muted">
            <i class="bi bi-chat-text fs-1 d-block mb-3"></i>
            <h5>暂无账号数据</h5>
            <p class="mb-0">请先添加账号</p>
        </td>
        </tr>
    `;
    return;
    }

    accounts.forEach(accountId => {
    const replySettings = defaultReplies[accountId] || { enabled: false, reply_content: '', reply_once: false };
    const tr = document.createElement('tr');

    // 状态标签
    const statusBadge = replySettings.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 只回复一次标签
    const replyOnceBadge = replySettings.reply_once ?
        '<span class="badge bg-warning">是</span>' :
        '<span class="badge bg-light text-dark">否</span>';

    // 回复内容预览
    let contentPreview = replySettings.reply_content || '未设置';
    if (contentPreview.length > 50) {
        contentPreview = contentPreview.substring(0, 50) + '...';
    }

    tr.innerHTML = `
        <td>
        <strong class="text-primary">${accountId}</strong>
        </td>
        <td>${statusBadge}</td>
        <td>${replyOnceBadge}</td>
        <td>
        <div class="text-truncate" style="max-width: 300px;" title="${replySettings.reply_content || ''}">
            ${contentPreview}
        </div>
        </td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editDefaultReply('${accountId}')" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="testDefaultReply('${accountId}')" title="测试">
            <i class="bi bi-play"></i>
            </button>
            ${replySettings.reply_once ? `
            <button class="btn btn-sm btn-outline-warning" onclick="clearDefaultReplyRecords('${accountId}')" title="清空记录">
            <i class="bi bi-arrow-clockwise"></i>
            </button>
            ` : ''}
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 编辑默认回复
async function editDefaultReply(accountId) {
    try {
    // 获取当前设置
    const response = await fetch(`${apiBase}/default-replies/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let settings = { enabled: false, reply_content: '', reply_once: false };
    if (response.ok) {
        settings = await response.json();
    }

    // 填充编辑表单
    document.getElementById('editDefaultReplyAccountId').value = accountId;
    document.getElementById('editDefaultReplyAccountIdDisplay').value = accountId;
    document.getElementById('editDefaultReplyEnabled').checked = settings.enabled;
    document.getElementById('editReplyContent').value = settings.reply_content || '';
    document.getElementById('editReplyOnce').checked = settings.reply_once || false;

    // 根据启用状态显示/隐藏内容输入框
    toggleReplyContentVisibility();

    // 显示编辑模态框
    const modal = new bootstrap.Modal(document.getElementById('editDefaultReplyModal'));
    modal.show();
    } catch (error) {
    console.error('获取默认回复设置失败:', error);
    showToast('获取默认回复设置失败', 'danger');
    }
}

// 切换回复内容输入框的显示/隐藏
function toggleReplyContentVisibility() {
    const enabled = document.getElementById('editDefaultReplyEnabled').checked;
    const contentGroup = document.getElementById('editReplyContentGroup');
    contentGroup.style.display = enabled ? 'block' : 'none';
}

// 保存默认回复设置
async function saveDefaultReply() {
    try {
    const accountId = document.getElementById('editDefaultReplyAccountId').value;
    const enabled = document.getElementById('editDefaultReplyEnabled').checked;
    const replyContent = document.getElementById('editReplyContent').value;
    const replyOnce = document.getElementById('editReplyOnce').checked;

    if (enabled && !replyContent.trim()) {
        showToast('启用默认回复时必须设置回复内容', 'warning');
        return;
    }

    const data = {
        enabled: enabled,
        reply_content: enabled ? replyContent : null,
        reply_once: replyOnce
    };

    const response = await fetch(`${apiBase}/default-replies/${accountId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    });

    if (response.ok) {
        showToast('默认回复设置保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editDefaultReplyModal')).hide();
        loadDefaultReplies(); // 刷新列表
        loadCookies(); // 刷新账号列表以更新默认回复状态显示
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('保存默认回复设置失败:', error);
    showToast('保存默认回复设置失败', 'danger');
    }
}

// 测试默认回复（占位函数）
function testDefaultReply(accountId) {
    showToast('测试功能开发中...', 'info');
}

// 清空默认回复记录
async function clearDefaultReplyRecords(accountId) {
    if (!confirm(`确定要清空账号 "${accountId}" 的默认回复记录吗？\n\n清空后，该账号将可以重新对之前回复过的对话进行默认回复。`)) {
        return;
    }

    try {
        const response = await fetch(`${apiBase}/default-replies/${accountId}/clear-records`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            showToast(`账号 "${accountId}" 的默认回复记录已清空`, 'success');
            loadDefaultReplies(); // 刷新列表
        } else {
            const error = await response.text();
            showToast(`清空失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('清空默认回复记录失败:', error);
        showToast('清空默认回复记录失败', 'danger');
    }
}

// ==================== AI回复配置相关函数 ====================

// 配置AI回复
async function configAIReply(accountId) {
    try {
    // 获取当前AI回复设置
    const settings = await fetchJSON(`${apiBase}/ai-reply-settings/${accountId}`);

    // 填充表单
    document.getElementById('aiConfigAccountId').value = accountId;
    document.getElementById('aiConfigAccountIdDisplay').value = accountId;
    document.getElementById('aiReplyEnabled').checked = settings.ai_enabled;
    // 处理模型名称
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const modelName = settings.model_name;
    // 检查是否是预设模型
    const presetModels = ['deepseek-v3.2', 'kimi-k2.5', 'qwen3-max-2026-01-23', 'qwen3.5-plus', 'gpt-4o-mini', 'gpt-4o'];
    if (presetModels.includes(modelName)) {
        modelSelect.value = modelName;
        customModelInput.style.display = 'none';
        customModelInput.value = '';
    } else {
        // 自定义模型
        modelSelect.value = 'custom';
        customModelInput.style.display = 'block';
        customModelInput.value = modelName;
    }
    document.getElementById('aiBaseUrl').value = settings.base_url;
    const normalizedApiType = settings.api_type === 'dashscope' ? '' : (settings.api_type || '');
    document.getElementById('aiApiType').value = normalizedApiType;
    document.getElementById('aiApiKey').value = settings.api_key;
    document.getElementById('maxDiscountPercent').value = settings.max_discount_percent;
    document.getElementById('maxDiscountAmount').value = settings.max_discount_amount;
    document.getElementById('maxBargainRounds').value = settings.max_bargain_rounds;
    // 解析自定义提示词 JSON，填入三个独立文本框
    let prompts = {};
    if (settings.custom_prompts) {
        try { prompts = JSON.parse(settings.custom_prompts); } catch (e) { prompts = {}; }
    }
    document.getElementById('promptPrice').value = prompts.price || '';
    document.getElementById('promptTech').value = prompts.tech || '';
    document.getElementById('promptDefault').value = prompts.default || '';

    // 切换设置显示状态
    toggleAIReplySettings();
    updateApiUrlPreview();
    await loadAIPresets();

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('aiReplyConfigModal'));
    modal.show();

    } catch (error) {
    console.error('获取AI回复设置失败:', error);
    showToast('获取AI回复设置失败', 'danger');
    }
}

// 更新API请求地址预览
function updateApiUrlPreview() {
    const baseUrl = (document.getElementById('aiBaseUrl').value || '').replace(/\/+$/, '');
    const apiType = document.getElementById('aiApiType').value;
    const preview = document.getElementById('apiUrlPreview');
    if (!preview || !baseUrl) {
        if (preview) preview.textContent = '';
        return;
    }

    const pathMap = {
        'openai':           '/v1/chat/completions',
        'openai_responses': '/v1/responses',
        'anthropic':        '/v1/messages',
        'azure_openai':     '/chat/completions',
        'ollama':           '/v1/chat/completions',
        'gemini':           '',
    };

    let path = pathMap[apiType];
    if (path === undefined) {
        // 自动识别 — 默认 chat/completions
        path = '/v1/chat/completions';
    }

    if (!path) {
        // Gemini 地址格式特殊，不追加路径
        preview.textContent = '请求端点预览: ' + baseUrl;
    } else if (apiType === 'azure_openai') {
        // Azure 不自动加 /v1
        const url = baseUrl.includes('/chat/completions') ? baseUrl : baseUrl + path;
        preview.textContent = '请求端点预览: ' + url;
    } else {
        const base = baseUrl.endsWith('/v1') ? baseUrl : baseUrl + '/v1';
        const suffix = path.replace('/v1', '');
        preview.textContent = '请求端点预览: ' + base + suffix;
    }
}

// 切换AI回复设置显示
function toggleAIReplySettings() {
    const enabled = document.getElementById('aiReplyEnabled').checked;
    const settingsDiv = document.getElementById('aiReplySettings');
    const bargainSettings = document.getElementById('bargainSettings');
    const promptSettings = document.getElementById('promptSettings');
    const testArea = document.getElementById('testArea');

    if (enabled) {
    settingsDiv.style.display = 'block';
    bargainSettings.style.display = 'block';
    promptSettings.style.display = 'block';
    testArea.style.display = 'block';
    } else {
    settingsDiv.style.display = 'none';
    bargainSettings.style.display = 'none';
    promptSettings.style.display = 'none';
    testArea.style.display = 'none';
    }
}

// 保存AI回复配置
async function saveAIReplyConfig() {
    try {
    const accountId = document.getElementById('aiConfigAccountId').value;
    const enabled = document.getElementById('aiReplyEnabled').checked;

    // 如果启用AI回复，验证必填字段
    if (enabled) {
        const apiKey = document.getElementById('aiApiKey').value.trim();
        if (!apiKey) {
        showToast('请输入API密钥', 'warning');
        return;
        }
    }
// 获取模型名称
    let modelName = document.getElementById('aiModelName').value;
    if (modelName === 'custom') {
        const customModelName = document.getElementById('customModelName').value.trim();
        if (!customModelName) {
        showToast('请输入自定义模型名称', 'warning');
        return;
        }
        modelName = customModelName;
    }
    // 从三个文本框组装自定义提示词 JSON
    const promptsObj = {};
    const priceVal = document.getElementById('promptPrice').value.trim();
    const techVal = document.getElementById('promptTech').value.trim();
    const defaultVal = document.getElementById('promptDefault').value.trim();
    if (priceVal) promptsObj.price = priceVal;
    if (techVal) promptsObj.tech = techVal;
    if (defaultVal) promptsObj.default = defaultVal;
    const customPromptsJson = Object.keys(promptsObj).length > 0 ? JSON.stringify(promptsObj) : '';

    // 构建设置对象
    const settings = {
        ai_enabled: enabled,
        model_name: modelName,
        api_key: document.getElementById('aiApiKey').value,
        base_url: document.getElementById('aiBaseUrl').value,
        api_type: document.getElementById('aiApiType').value,
        max_discount_percent: parseInt(document.getElementById('maxDiscountPercent').value),
        max_discount_amount: parseInt(document.getElementById('maxDiscountAmount').value),
        max_bargain_rounds: parseInt(document.getElementById('maxBargainRounds').value),
        custom_prompts: customPromptsJson
    };

    // 保存设置
    const response = await fetch(`${apiBase}/ai-reply-settings/${accountId}`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify(settings)
    });

    if (response.ok) {
        showToast('AI回复配置保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('aiReplyConfigModal')).hide();
        loadCookies(); // 刷新账号列表以更新AI回复状态显示
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }

    } catch (error) {
    console.error('保存AI回复配置失败:', error);
    showToast('保存AI回复配置失败', 'danger');
    }
}

// 测试AI回复
async function testAIReply() {
    const testBtn = document.querySelector('[onclick="testAIReply()"]');
    if (testBtn && testBtn.disabled) return;
    if (testBtn) { testBtn.disabled = true; testBtn.textContent = '测试中...'; }

    try {
    const accountId = document.getElementById('aiConfigAccountId').value;
    const testMessage = document.getElementById('testMessage').value.trim();
    const testItemPrice = document.getElementById('testItemPrice').value;

    if (!testMessage) {
        showToast('请输入测试消息', 'warning');
        return;
    }

    // 构建测试数据
    const testData = {
        message: testMessage,
        item_title: '测试商品',
        item_price: parseFloat(testItemPrice) || 100,
        item_desc: '这是一个用于测试AI回复功能的商品'
    };

    // 显示加载状态
    const testResult = document.getElementById('testResult');
    const testReplyContent = document.getElementById('testReplyContent');
    testResult.style.display = 'block';
    testReplyContent.innerHTML = '<i class="bi bi-hourglass-split"></i> 正在生成AI回复...';

    // 调用测试API
    const response = await fetch(`${apiBase}/ai-reply-test/${accountId}`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify(testData)
    });

    if (response.ok) {
        const result = await response.json();
        testReplyContent.innerHTML = result.reply;
        showToast('AI回复测试成功', 'success');
    } else {
        const error = await response.text();
        testReplyContent.innerHTML = `<span class="text-danger">测试失败: ${error}</span>`;
        showToast(`测试失败: ${error}`, 'danger');
    }

    } catch (error) {
    console.error('测试AI回复失败:', error);
    const testReplyContent = document.getElementById('testReplyContent');
    testReplyContent.innerHTML = `<span class="text-danger">测试失败: ${error.message}</span>`;
    showToast('测试AI回复失败', 'danger');
    } finally {
    if (testBtn) { testBtn.disabled = false; testBtn.textContent = '测试回复'; }
    }
}

// 切换自定义模型输入框的显示/隐藏
function toggleCustomModelInput() {
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    if (modelSelect.value === 'custom') {
    customModelInput.style.display = 'block';
    customModelInput.focus();
    } else {
    customModelInput.style.display = 'none';
    customModelInput.value = '';
    }
}

// -------------------- AI配置预设功能 --------------------

let _aiPresets = []; // 缓存预设数据，避免依赖 option dataset

async function loadAIPresets() {
    try {
        const presets = await fetchJSON(`${apiBase}/ai-config-presets`);
        _aiPresets = presets || [];
        const select = document.getElementById('aiPresetSelect');
        const deleteBtn = document.getElementById('deletePresetBtn');
        select.innerHTML = '<option value="">-- 选择预设 --</option>';
        _aiPresets.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.preset_name;
            select.appendChild(opt);
        });
        // 尝试自动匹配当前表单值对应的预设
        _autoSelectMatchingPreset();
        deleteBtn.style.display = select.value ? '' : 'none';
    } catch (e) {
        console.error('加载AI配置预设失败:', e);
    }
}

function _autoSelectMatchingPreset() {
    const select = document.getElementById('aiPresetSelect');
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const curModel = modelSelect.value === 'custom' ? customModelInput.value : modelSelect.value;
    const curKey = document.getElementById('aiApiKey').value;
    const curUrl = document.getElementById('aiBaseUrl').value;
    const curApiType = document.getElementById('aiApiType').value;

    const match = _aiPresets.find(p => {
        const presetApiType = p.api_type === 'dashscope' ? '' : (p.api_type || '');
        return p.model_name === curModel && p.api_key === curKey && p.base_url === curUrl && presetApiType === curApiType;
    });
    select.value = match ? match.id : '';
}

function loadAIPreset() {
    const select = document.getElementById('aiPresetSelect');
    const deleteBtn = document.getElementById('deletePresetBtn');
    const presetId = select.value;

    if (!presetId) {
        deleteBtn.style.display = 'none';
        return;
    }
    deleteBtn.style.display = '';

    const preset = _aiPresets.find(p => String(p.id) === presetId);
    if (!preset) return;

    // 填充模型
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const builtinModels = Array.from(modelSelect.options).map(o => o.value).filter(v => v && v !== 'custom');
    if (builtinModels.includes(preset.model_name)) {
        modelSelect.value = preset.model_name;
        customModelInput.style.display = 'none';
        customModelInput.value = '';
    } else {
        modelSelect.value = 'custom';
        customModelInput.style.display = 'block';
        customModelInput.value = preset.model_name;
    }

    document.getElementById('aiBaseUrl').value = preset.base_url;
    document.getElementById('aiApiKey').value = preset.api_key;
    const normalizedPresetApiType = preset.api_type === 'dashscope' ? '' : (preset.api_type || '');
    document.getElementById('aiApiType').value = normalizedPresetApiType;
    updateApiUrlPreview();

    showToast(`已切换到预设「${preset.preset_name}」`, 'success');
}

async function saveCurrentAsPreset() {
    const name = prompt('请输入预设名称：');
    if (!name || !name.trim()) return;

    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const modelName = modelSelect.value === 'custom' ? customModelInput.value : modelSelect.value;
    const apiKey = document.getElementById('aiApiKey').value;
    const baseUrl = document.getElementById('aiBaseUrl').value;

    if (!modelName) {
        showToast('请先选择或输入模型名称', 'warning');
        return;
    }

    try {
        await fetchJSON(`${apiBase}/ai-config-presets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                preset_name: name.trim(),
                model_name: modelName,
                api_key: apiKey,
                base_url: baseUrl,
                api_type: document.getElementById('aiApiType').value
            })
        });
        showToast('预设保存成功', 'success');
        await loadAIPresets();
        // 自动选中刚保存的预设
        const select = document.getElementById('aiPresetSelect');
        const saved = _aiPresets.find(p => p.preset_name === name.trim());
        if (saved) {
            select.value = saved.id;
            document.getElementById('deletePresetBtn').style.display = '';
        }
    } catch (e) {
        console.error('保存预设失败:', e);
        showToast('保存预设失败', 'danger');
    }
}

async function deleteSelectedPreset() {
    const select = document.getElementById('aiPresetSelect');
    const presetId = select.value;
    if (!presetId) return;

    const preset = _aiPresets.find(p => String(p.id) === presetId);
    if (!preset) return;
    if (!confirm(`确定删除预设「${preset.preset_name}」吗？`)) return;

    try {
        await fetchJSON(`${apiBase}/ai-config-presets/${presetId}`, {
            method: 'DELETE'
        });
        showToast('预设已删除', 'success');
        await loadAIPresets();
    } catch (e) {
        console.error('删除预设失败:', e);
        showToast('删除预设失败', 'danger');
    }
}

// 监听默认回复启用状态变化
document.addEventListener('DOMContentLoaded', function() {
    const enabledCheckbox = document.getElementById('editDefaultReplyEnabled');
    if (enabledCheckbox) {
    enabledCheckbox.addEventListener('change', toggleReplyContentVisibility);
    }
});

// ================================
// 【外发配置菜单】相关功能
// ================================

// 外发配置类型配置
const outgoingConfigs = {
    smtp: {
        title: 'SMTP邮件配置',
        description: '配置SMTP服务器用于发送注册验证码等邮件通知',
        icon: 'bi-envelope-fill',
        color: 'primary',
        fields: [
            {
                id: 'smtp_server',
                label: 'SMTP服务器',
                type: 'text',
                placeholder: 'smtp.qq.com',
                required: true,
                help: '邮箱服务商的SMTP服务器地址，如：smtp.qq.com、smtp.gmail.com'
            },
            {
                id: 'smtp_port',
                label: 'SMTP端口',
                type: 'number',
                placeholder: '587',
                required: true,
                help: '通常为587（TLS）或465（SSL）'
            },
            {
                id: 'smtp_user',
                label: '发件邮箱',
                type: 'email',
                placeholder: 'your-email@qq.com',
                required: true,
                help: '用于发送邮件的邮箱地址'
            },
            {
                id: 'smtp_password',
                label: '邮箱密码/授权码',
                type: 'password',
                placeholder: '输入密码或授权码',
                required: true,
                help: '邮箱密码或应用专用密码（QQ邮箱需要授权码）'
            },
            {
                id: 'smtp_from',
                label: '发件人显示名（可选）',
                type: 'text',
                placeholder: '闲鱼管理系统',
                required: false,
                help: '邮件发件人显示的名称，留空则使用邮箱地址'
            },
            {
                id: 'smtp_use_tls',
                label: '启用TLS',
                type: 'select',
                options: [
                    { value: 'true', text: '是' },
                    { value: 'false', text: '否' }
                ],
                required: true,
                help: '是否启用TLS加密（推荐开启）'
            },
            {
                id: 'smtp_use_ssl',
                label: '启用SSL',
                type: 'select',
                options: [
                    { value: 'true', text: '是' },
                    { value: 'false', text: '否' }
                ],
                required: true,
                help: '是否启用SSL加密（与TLS二选一）'
            }
        ]
    }
};

// ================================
// 【通知渠道菜单】相关功能
// ================================

// 通知渠道类型配置
const channelTypeConfigs = {
    qq: {
    title: 'QQ通知',
    description: '需要添加QQ号 <code>3607695896</code> 为好友才能正常接收消息通知',
    icon: 'bi-chat-dots-fill',
    color: 'primary',
    fields: [
        {
        id: 'qq_number',
        label: '接收QQ号码',
        type: 'text',
        placeholder: '输入QQ号码',
        required: true,
        help: '用于接收通知消息的QQ号码'
        }
    ]
    },
    dingtalk: {
    title: '钉钉通知',
    description: '请设置钉钉机器人Webhook URL，支持自定义机器人和群机器人',
    icon: 'bi-bell-fill',
    color: 'info',
    fields: [
        {
        id: 'webhook_url',
        label: '钉钉机器人Webhook URL',
        type: 'url',
        placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...',
        required: true,
        help: '钉钉机器人的Webhook地址'
        },
        {
        id: 'secret',
        label: '加签密钥（可选）',
        type: 'text',
        placeholder: '输入加签密钥',
        required: false,
        help: '如果机器人开启了加签验证，请填写密钥'
        }
    ]
    },
    feishu: {
    title: '飞书通知',
    description: '请设置飞书机器人Webhook URL，支持自定义机器人和群机器人',
    icon: 'bi-chat-square-text-fill',
    color: 'warning',
    fields: [
        {
        id: 'webhook_url',
        label: '飞书机器人Webhook URL',
        type: 'url',
        placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/...',
        required: true,
        help: '飞书机器人的Webhook地址'
        },
        {
        id: 'secret',
        label: '签名密钥（可选）',
        type: 'text',
        placeholder: '输入签名密钥',
        required: false,
        help: '如果机器人开启了签名验证，请填写密钥'
        }
    ]
    },
    bark: {
    title: 'Bark通知',
    description: 'iOS推送通知服务，支持自建服务器和官方服务器',
    icon: 'bi-phone-fill',
    color: 'dark',
    fields: [
        {
        id: 'device_key',
        label: '设备密钥',
        type: 'text',
        placeholder: '输入Bark设备密钥',
        required: true,
        help: 'Bark应用中显示的设备密钥'
        },
        {
        id: 'server_url',
        label: '服务器地址（可选）',
        type: 'url',
        placeholder: 'https://api.day.app',
        required: false,
        help: '自建Bark服务器地址，留空使用官方服务器'
        },
        {
        id: 'title',
        label: '通知标题（可选）',
        type: 'text',
        placeholder: '闲鱼管理系统通知',
        required: false,
        help: '推送通知的标题'
        },
        {
        id: 'sound',
        label: '提示音（可选）',
        type: 'text',
        placeholder: 'default',
        required: false,
        help: '通知提示音，如：alarm, anticipate, bell等'
        },
        {
        id: 'group',
        label: '分组（可选）',
        type: 'text',
        placeholder: 'xianyu',
        required: false,
        help: '通知分组名称，用于归类消息'
        }
    ]
    },
    email: {
    title: '邮件通知',
    description: '通过SMTP服务器发送邮件通知，支持各种邮箱服务商',
    icon: 'bi-envelope-fill',
    color: 'success',
    fields: [
        {
        id: 'smtp_server',
        label: 'SMTP服务器',
        type: 'text',
        placeholder: 'smtp.gmail.com',
        required: true,
        help: '邮箱服务商的SMTP服务器地址'
        },
        {
        id: 'smtp_port',
        label: 'SMTP端口',
        type: 'number',
        placeholder: '587',
        required: true,
        help: '通常为587（TLS）或465（SSL）'
        },
        {
        id: 'email_user',
        label: '发送邮箱',
        type: 'email',
        placeholder: 'your-email@gmail.com',
        required: true,
        help: '用于发送通知的邮箱地址'
        },
        {
        id: 'email_password',
        label: '邮箱密码/授权码',
        type: 'password',
        placeholder: '输入密码或授权码',
        required: true,
        help: '邮箱密码或应用专用密码'
        },
        {
        id: 'recipient_email',
        label: '接收邮箱',
        type: 'email',
        placeholder: 'recipient@example.com',
        required: true,
        help: '用于接收通知的邮箱地址'
        }
    ]
    },
    webhook: {
    title: 'Webhook通知',
    description: '通过HTTP POST请求发送通知到自定义的Webhook地址',
    icon: 'bi-link-45deg',
    color: 'warning',
    fields: [
        {
        id: 'webhook_url',
        label: 'Webhook URL',
        type: 'url',
        placeholder: 'https://your-server.com/webhook',
        required: true,
        help: '接收通知的Webhook地址'
        },
        {
        id: 'http_method',
        label: 'HTTP方法',
        type: 'select',
        options: [
            { value: 'POST', text: 'POST' },
            { value: 'PUT', text: 'PUT' }
        ],
        required: true,
        help: '发送请求使用的HTTP方法'
        },
        {
        id: 'headers',
        label: '自定义请求头（可选）',
        type: 'textarea',
        placeholder: '{"Authorization": "Bearer token", "Content-Type": "application/json"}',
        required: false,
        help: 'JSON格式的自定义请求头'
        }
    ]
    },
    wechat: {
    title: '微信通知',
    description: '通过企业微信机器人发送通知消息',
    icon: 'bi-wechat',
    color: 'success',
    fields: [
        {
        id: 'webhook_url',
        label: '企业微信机器人Webhook URL',
        type: 'url',
        placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
        required: true,
        help: '企业微信群机器人的Webhook地址'
        }
    ]
    },
    telegram: {
    title: 'Telegram通知',
    description: '通过Telegram机器人发送通知消息（需要海外服务器）',
    icon: 'bi-telegram',
    color: 'primary',
    fields: [
        {
        id: 'bot_token',
        label: 'Bot Token',
        type: 'text',
        placeholder: '123456789:ABCdefGHIjklMNOpqrsTUVwxyz',
        required: true,
        help: '从@BotFather获取的机器人Token'
        },
        {
        id: 'chat_id',
        label: 'Chat ID',
        type: 'text',
        placeholder: '123456789 或 @channel_name',
        required: true,
        help: '接收消息的用户ID或频道名'
        }
    ]
    }
};

// 显示添加渠道模态框
function showAddChannelModal(type) {
    const config = channelTypeConfigs[type];
    if (!config) {
    showToast('不支持的通知渠道类型', 'danger');
    return;
    }

    // 设置模态框标题和描述
    document.getElementById('addChannelModalTitle').textContent = `添加${config.title}`;
    document.getElementById('channelTypeDescription').innerHTML = config.description;
    document.getElementById('channelType').value = type;

    // 生成配置字段
    const fieldsContainer = document.getElementById('channelConfigFields');
    fieldsContainer.innerHTML = '';

    config.fields.forEach(field => {
    const fieldHtml = generateFieldHtml(field, 'add_');
    fieldsContainer.insertAdjacentHTML('beforeend', fieldHtml);
    });

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('addChannelModal'));
    modal.show();
}

// 生成表单字段HTML
function generateFieldHtml(field, prefix) {
    const fieldId = prefix + field.id;
    let inputHtml = '';

    switch (field.type) {
    case 'select':
        inputHtml = `<select class="form-select" id="${fieldId}" ${field.required ? 'required' : ''}>`;
        if (field.options) {
        field.options.forEach(option => {
            inputHtml += `<option value="${option.value}">${option.text}</option>`;
        });
        }
        inputHtml += '</select>';
        break;
    case 'textarea':
        inputHtml = `<textarea class="form-control" id="${fieldId}" placeholder="${field.placeholder}" rows="3" ${field.required ? 'required' : ''}></textarea>`;
        break;
    default:
        inputHtml = `<input type="${field.type}" class="form-control" id="${fieldId}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
    }

    return `
    <div class="mb-3">
        <label for="${fieldId}" class="form-label">
        ${field.label} ${field.required ? '<span class="text-danger">*</span>' : ''}
        </label>
        ${inputHtml}
        ${field.help ? `<small class="form-text text-muted">${field.help}</small>` : ''}
    </div>
    `;
}

// 保存通知渠道
async function saveNotificationChannel() {
    const type = document.getElementById('channelType').value;
    const name = document.getElementById('channelName').value;
    const enabled = document.getElementById('channelEnabled').checked;
    const form = document.getElementById('addChannelForm');

    if (!name.trim()) {
    showToast('请输入渠道名称', 'warning');
    return;
    }

    const config = channelTypeConfigs[type];
    if (!config) {
    showToast('无效的渠道类型', 'danger');
    return;
    }

    // 收集配置数据
    const configData = {};
    let hasError = false;

    config.fields.forEach(field => {
    const element = form ? form.querySelector(`#add_${field.id}`) : null;
    if (!element) {
        showToast(`找不到${field.label}输入框`, 'danger');
        hasError = true;
        return;
    }
    const value = element.value.trim();

    if (field.required && !value) {
        showToast(`请填写${field.label}`, 'warning');
        hasError = true;
        return;
    }

    if (value) {
        configData[field.id] = value;
    }
    });

    if (hasError) return;

    try {
    const response = await fetch(`${apiBase}/notification-channels`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
        name: name,
        type: type,
        config: JSON.stringify(configData),
        enabled: enabled
        })
    });

    if (response.ok) {
        showToast('通知渠道添加成功', 'success');
        const modal = bootstrap.Modal.getInstance(document.getElementById('addChannelModal'));
        modal.hide();
        loadNotificationChannels();
    } else {
        const error = await response.text();
        showToast(`添加失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('添加通知渠道失败:', error);
    showToast('添加通知渠道失败', 'danger');
    }
}

// 加载通知渠道列表
async function loadNotificationChannels() {
    try {
    const response = await fetch(`${apiBase}/notification-channels`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!response.ok) {
        throw new Error('获取通知渠道失败');
    }

    const channels = await response.json();
    renderNotificationChannels(channels);
    } catch (error) {
    console.error('加载通知渠道失败:', error);
    showToast('加载通知渠道失败', 'danger');
    }
}

// 渲染通知渠道列表
function renderNotificationChannels(channels) {
    const tbody = document.getElementById('channelsTableBody');
    tbody.innerHTML = '';

    if (channels.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="6" class="text-center py-4 text-muted">
            <i class="bi bi-bell fs-1 d-block mb-3"></i>
            <h5>暂无通知渠道</h5>
            <p class="mb-0">点击上方按钮添加通知渠道</p>
        </td>
        </tr>
    `;
    return;
    }

    channels.forEach(channel => {
    const tr = document.createElement('tr');

    const statusBadge = channel.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 获取渠道类型配置（处理类型映射）
    let channelType = channel.type;
    if (channelType === 'ding_talk') {
        channelType = 'dingtalk';  // 兼容旧的类型名
    } else if (channelType === 'lark') {
        channelType = 'feishu';  // 兼容lark类型名
    }
    const typeConfig = channelTypeConfigs[channelType];
    const typeDisplay = typeConfig ? typeConfig.title : channel.type;
    const typeColor = typeConfig ? typeConfig.color : 'secondary';

    // 解析并显示配置信息
    let configDisplay = '';
    try {
        const configData = JSON.parse(channel.config || '{}');
        const configEntries = Object.entries(configData);

        if (configEntries.length > 0) {
        configDisplay = configEntries.map(([key, value]) => {
            // 隐藏敏感信息
            if (key.includes('password') || key.includes('token') || key.includes('secret')) {
            return `${key}: ****`;
            }
            // 截断过长的值
            const displayValue = value.length > 30 ? value.substring(0, 30) + '...' : value;
            return `${key}: ${displayValue}`;
        }).join('<br>');
        } else {
        configDisplay = channel.config || '无配置';
        }
    } catch (e) {
        // 兼容旧格式
        configDisplay = channel.config || '无配置';
        if (configDisplay.length > 30) {
        configDisplay = configDisplay.substring(0, 30) + '...';
        }
    }

    tr.innerHTML = `
        <td><strong class="text-primary">${channel.id}</strong></td>
        <td>
        <div class="d-flex align-items-center">
            <i class="bi ${typeConfig ? typeConfig.icon : 'bi-bell'} me-2 text-${typeColor}"></i>
            ${channel.name}
        </div>
        </td>
        <td><span class="badge bg-${typeColor}">${typeDisplay}</span></td>
        <td><small class="text-muted">${configDisplay}</small></td>
        <td>${statusBadge}</td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editNotificationChannel(${channel.id})" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteNotificationChannel(${channel.id})" title="删除">
            <i class="bi bi-trash"></i>
            </button>
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}



// 删除通知渠道
async function deleteNotificationChannel(channelId) {
    if (!confirm('确定要删除这个通知渠道吗？')) {
    return;
    }

    try {
    const response = await fetch(`${apiBase}/notification-channels/${channelId}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('通知渠道删除成功', 'success');
        loadNotificationChannels();
    } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('删除通知渠道失败:', error);
    showToast('删除通知渠道失败', 'danger');
    }
}

// 编辑通知渠道
async function editNotificationChannel(channelId) {
    try {
    // 获取渠道详情
    const response = await fetch(`${apiBase}/notification-channels`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!response.ok) {
        throw new Error('获取通知渠道失败');
    }

    const channels = await response.json();
    const channel = channels.find(c => c.id === channelId);

    if (!channel) {
        showToast('通知渠道不存在', 'danger');
        return;
    }

    // 处理类型映射
    let channelType = channel.type;
    if (channelType === 'ding_talk') {
        channelType = 'dingtalk';  // 兼容旧的类型名
    } else if (channelType === 'lark') {
        channelType = 'feishu';  // 兼容lark类型名
    }

    const config = channelTypeConfigs[channelType];
    if (!config) {
        showToast('不支持的渠道类型', 'danger');
        return;
    }

    // 填充基本信息
    document.getElementById('editChannelId').value = channel.id;
    document.getElementById('editChannelType').value = channelType;  // 使用映射后的类型
    document.getElementById('editChannelName').value = channel.name;
    document.getElementById('editChannelEnabled').checked = channel.enabled;

    // 解析配置数据
    let configData = {};
    try {
        configData = JSON.parse(channel.config || '{}');
    } catch (e) {
        // 兼容旧格式（直接字符串）
        if (channel.type === 'qq') {
        configData = { qq_number: channel.config };
        } else if (channel.type === 'dingtalk' || channel.type === 'ding_talk') {
        configData = { webhook_url: channel.config };
        } else if (channel.type === 'feishu' || channel.type === 'lark') {
        configData = { webhook_url: channel.config };
        } else if (channel.type === 'bark') {
        configData = { device_key: channel.config };
        } else {
        configData = { config: channel.config };
        }
    }

    // 生成编辑字段
    const fieldsContainer = document.getElementById('editChannelConfigFields');
    fieldsContainer.innerHTML = '';

    config.fields.forEach(field => {
        const fieldHtml = generateFieldHtml(field, 'edit_');
        fieldsContainer.insertAdjacentHTML('beforeend', fieldHtml);

        // 填充现有值
        const element = document.getElementById('edit_' + field.id);
        if (element && configData[field.id]) {
        element.value = configData[field.id];
        }
    });

    // 显示编辑模态框
    const modal = new bootstrap.Modal(document.getElementById('editChannelModal'));
    modal.show();
    } catch (error) {
    console.error('编辑通知渠道失败:', error);
    showToast('编辑通知渠道失败', 'danger');
    }
}

// 更新通知渠道
async function updateNotificationChannel() {
    const channelId = document.getElementById('editChannelId').value;
    const type = document.getElementById('editChannelType').value;
    const name = document.getElementById('editChannelName').value;
    const enabled = document.getElementById('editChannelEnabled').checked;

    if (!name.trim()) {
    showToast('请输入渠道名称', 'warning');
    return;
    }

    const config = channelTypeConfigs[type];
    if (!config) {
    showToast('无效的渠道类型', 'danger');
    return;
    }

    // 收集配置数据
    const configData = {};
    let hasError = false;

    config.fields.forEach(field => {
    const element = document.getElementById('edit_' + field.id);
    const value = element.value.trim();

    if (field.required && !value) {
        showToast(`请填写${field.label}`, 'warning');
        hasError = true;
        return;
    }

    if (value) {
        configData[field.id] = value;
    }
    });

    if (hasError) return;

    try {
    const response = await fetch(`${apiBase}/notification-channels/${channelId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
        name: name,
        config: JSON.stringify(configData),
        enabled: enabled
        })
    });

    if (response.ok) {
        showToast('通知渠道更新成功', 'success');
        const modal = bootstrap.Modal.getInstance(document.getElementById('editChannelModal'));
        modal.hide();
        loadNotificationChannels();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新通知渠道失败:', error);
    showToast('更新通知渠道失败', 'danger');
    }
}

// ================================
// 【通知模板配置】相关功能
// ================================

// 通知模板预览数据
const templatePreviewData = {
    message: {
        account_id: 'test_account',
        buyer_name: '张三',
        buyer_id: '123456789',
        item_id: '987654321',
        chat_id: 'chat_001',
        message: '你好，这个商品还有吗？',
        time: new Date().toLocaleString('zh-CN')
    },
    token_refresh: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        error_message: 'Token已过期，需要重新登录',
        verification_url: 'https://example.com/verify'
    },
    delivery: {
        account_id: 'test_account',
        buyer_name: '李四',
        buyer_id: '234567890',
        item_id: '876543210',
        chat_id: 'chat_002',
        result: '发货成功',
        time: new Date().toLocaleString('zh-CN')
    },
    slider_success: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        status_text: 'cookies已自动更新到数据库'
    },
    face_verify: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        verification_action: '请点击验证链接完成验证:',
        verification_url: 'https://passport.goofish.com/mini_login.htm?example=test',
        verification_type: '身份验证'
    },
    password_login_success: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        cookie_count: '30'
    },
    cookie_refresh_success: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        cookie_count: '30'
    }
};

// 加载通知模板
async function loadNotificationTemplates() {
    try {
        // 重置tab状态，确保只显示第一个tab
        const tabContent = document.getElementById('notificationTemplateTabContent');
        if (tabContent) {
            // 重置所有tab-pane
            tabContent.querySelectorAll('.tab-pane').forEach(pane => {
                pane.classList.remove('show', 'active');
            });
            // 激活第一个tab-pane
            const firstPane = tabContent.querySelector('#message-template');
            if (firstPane) {
                firstPane.classList.add('show', 'active');
            }

            // 重置所有tab按钮
            const tabList = document.getElementById('notificationTemplateTabs');
            if (tabList) {
                tabList.querySelectorAll('.nav-link').forEach(link => {
                    link.classList.remove('active');
                    link.setAttribute('aria-selected', 'false');
                });
                const firstTab = tabList.querySelector('#message-template-tab');
                if (firstTab) {
                    firstTab.classList.add('active');
                    firstTab.setAttribute('aria-selected', 'true');
                }
            }
        }

        const response = await fetch(`${apiBase}/notification-templates`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('获取通知模板失败');
        }

        const data = await response.json();
        const templates = data.templates || [];

        // 加载每个模板到编辑器
        templates.forEach(template => {
            const editor = document.getElementById(`${template.type}-template-editor`);
            if (editor) {
                editor.value = template.template;
                updateTemplatePreview(template.type);
            }
        });

        // 如果没有模板数据，加载默认模板
        ['message', 'token_refresh', 'delivery', 'slider_success', 'face_verify'].forEach(async (type) => {
            const editor = document.getElementById(`${type}-template-editor`);
            if (editor && !editor.value) {
                await loadDefaultTemplate(type);
            }
        });

        showToast('通知模板加载成功', 'success');
    } catch (error) {
        console.error('加载通知模板失败:', error);
        showToast('加载通知模板失败', 'danger');
    }
}

// 加载默认模板
async function loadDefaultTemplate(templateType) {
    try {
        const response = await fetch(`${apiBase}/notification-templates/${templateType}/default`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            const editor = document.getElementById(`${templateType}-template-editor`);
            if (editor) {
                editor.value = data.template;
                updateTemplatePreview(templateType);
            }
        }
    } catch (error) {
        console.error(`加载默认模板失败 (${templateType}):`, error);
    }
}

// 保存通知模板
async function saveNotificationTemplate(templateType) {
    try {
        const editor = document.getElementById(`${templateType}-template-editor`);
        if (!editor) {
            showToast('编辑器不存在', 'danger');
            return;
        }

        const template = editor.value;
        if (!template.trim()) {
            showToast('模板内容不能为空', 'warning');
            return;
        }

        const response = await fetch(`${apiBase}/notification-templates/${templateType}`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ template })
        });

        if (!response.ok) {
            throw new Error('保存模板失败');
        }

        showToast('模板保存成功', 'success');
    } catch (error) {
        console.error('保存通知模板失败:', error);
        showToast('保存模板失败', 'danger');
    }
}

// 重置通知模板
async function resetNotificationTemplate(templateType) {
    if (!confirm('确定要恢复默认模板吗？当前修改将会丢失。')) {
        return;
    }

    try {
        const response = await fetch(`${apiBase}/notification-templates/${templateType}/reset`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('重置模板失败');
        }

        const data = await response.json();
        const editor = document.getElementById(`${templateType}-template-editor`);
        if (editor && data.template) {
            editor.value = data.template.template;
            updateTemplatePreview(templateType);
        }

        showToast('模板已恢复默认', 'success');
    } catch (error) {
        console.error('重置通知模板失败:', error);
        showToast('重置模板失败', 'danger');
    }
}

// 插入模板变量
function insertTemplateVariable(templateType, variable) {
    const editor = document.getElementById(`${templateType}-template-editor`);
    if (!editor) return;

    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const text = editor.value;

    editor.value = text.substring(0, start) + variable + text.substring(end);
    editor.selectionStart = editor.selectionEnd = start + variable.length;
    editor.focus();

    updateTemplatePreview(templateType);
}

// 更新模板预览
function updateTemplatePreview(templateType) {
    const editor = document.getElementById(`${templateType}-template-editor`);
    const preview = document.getElementById(`${templateType}-template-preview`);

    if (!editor || !preview) return;

    let template = editor.value;
    const data = templatePreviewData[templateType] || {};

    // 替换变量
    for (const [key, value] of Object.entries(data)) {
        template = template.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
    }

    preview.textContent = template;
}

// 发送测试通知
async function testNotificationTemplate(templateType) {
    const editor = document.getElementById(`${templateType}-template-editor`);
    if (!editor) {
        showToast('编辑器不存在', 'danger');
        return;
    }

    const template = editor.value;
    if (!template.trim()) {
        showToast('模板内容不能为空', 'warning');
        return;
    }

    // 显示发送中提示
    showToast('正在发送测试通知...', 'info');

    try {
        const response = await fetch(`${apiBase}/notification-templates/test`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                template_type: templateType,
                template: template
            })
        });

        const data = await response.json();

        if (response.ok) {
            showToast(data.message || '测试通知发送成功', 'success');
            if (data.failed_channels && data.failed_channels.length > 0) {
                console.warn('部分渠道发送失败:', data.failed_channels);
            }
        } else {
            showToast(data.detail || '测试通知发送失败', 'danger');
        }
    } catch (error) {
        console.error('发送测试通知失败:', error);
        showToast('发送测试通知失败', 'danger');
    }
}

// ================================
// 【消息通知菜单】相关功能
// ================================

// 加载消息通知配置
async function loadMessageNotifications() {
    try {
    // 获取所有账号
    const accountsResponse = await fetch(`${apiBase}/cookies`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!accountsResponse.ok) {
        throw new Error('获取账号列表失败');
    }

    const accounts = await accountsResponse.json();

    // 获取所有通知配置
    const notificationsResponse = await fetch(`${apiBase}/message-notifications`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let notifications = {};
    if (notificationsResponse.ok) {
        notifications = await notificationsResponse.json();
    }

    renderMessageNotifications(accounts, notifications);
    } catch (error) {
    console.error('加载消息通知配置失败:', error);
    showToast('加载消息通知配置失败', 'danger');
    }
}

// 渲染消息通知配置
function renderMessageNotifications(accounts, notifications) {
    const tbody = document.getElementById('notificationsTableBody');
    tbody.innerHTML = '';

    if (accounts.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="4" class="text-center py-4 text-muted">
            <i class="bi bi-chat-dots fs-1 d-block mb-3"></i>
            <h5>暂无账号数据</h5>
            <p class="mb-0">请先添加账号</p>
        </td>
        </tr>
    `;
    return;
    }

    accounts.forEach(accountId => {
    const accountNotifications = notifications[accountId] || [];
    const tr = document.createElement('tr');

    let channelsList = '';
    if (accountNotifications.length > 0) {
        channelsList = accountNotifications.map(n =>
        `<span class="badge bg-${n.enabled ? 'success' : 'secondary'} me-1">${n.channel_name}</span>`
        ).join('');
    } else {
        channelsList = '<span class="text-muted">未配置</span>';
    }

    const status = accountNotifications.some(n => n.enabled) ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    tr.innerHTML = `
        <td><strong class="text-primary">${accountId}</strong></td>
        <td>${channelsList}</td>
        <td>${status}</td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="configAccountNotification('${accountId}')" title="配置">
            <i class="bi bi-gear"></i> 配置
            </button>
            ${accountNotifications.length > 0 ? `
            <button class="btn btn-sm btn-outline-danger" onclick="deleteAccountNotification('${accountId}')" title="删除配置">
            <i class="bi bi-trash"></i>
            </button>
            ` : ''}
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 配置账号通知
async function configAccountNotification(accountId) {
    try {
    // 获取所有通知渠道
    const channelsResponse = await fetch(`${apiBase}/notification-channels`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!channelsResponse.ok) {
        throw new Error('获取通知渠道失败');
    }

    const channels = await channelsResponse.json();

    if (channels.length === 0) {
        showToast('请先添加通知渠道', 'warning');
        return;
    }

    // 获取当前账号的通知配置
    const notificationResponse = await fetch(`${apiBase}/message-notifications/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let currentNotifications = [];
    if (notificationResponse.ok) {
        currentNotifications = await notificationResponse.json();
    }

    // 填充表单
    document.getElementById('configAccountId').value = accountId;
    document.getElementById('displayAccountId').value = accountId;

    // 填充通知渠道选项
    const channelSelect = document.getElementById('notificationChannel');
    channelSelect.innerHTML = '<option value="">请选择通知渠道</option>';

    // 获取当前配置的第一个通知渠道（如果存在）
    const currentNotification = currentNotifications.length > 0 ? currentNotifications[0] : null;

    channels.forEach(channel => {
        if (channel.enabled) {
        const option = document.createElement('option');
        option.value = channel.id;
        option.textContent = `${channel.name} (${channel.config})`;
        if (currentNotification && currentNotification.channel_id === channel.id) {
            option.selected = true;
        }
        channelSelect.appendChild(option);
        }
    });

    // 设置启用状态
    document.getElementById('notificationEnabled').checked =
        currentNotification ? currentNotification.enabled : true;

    // 显示配置模态框
    const modal = new bootstrap.Modal(document.getElementById('configNotificationModal'));
    modal.show();
    } catch (error) {
    console.error('配置账号通知失败:', error);
    showToast('配置账号通知失败', 'danger');
    }
}

// 删除账号通知配置
async function deleteAccountNotification(accountId) {
    if (!confirm(`确定要删除账号 ${accountId} 的通知配置吗？`)) {
    return;
    }

    try {
    const response = await fetch(`${apiBase}/message-notifications/account/${accountId}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('通知配置删除成功', 'success');
        loadMessageNotifications();
    } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('删除通知配置失败:', error);
    showToast('删除通知配置失败', 'danger');
    }
}

// 保存账号通知配置
async function saveAccountNotification() {
    const accountId = document.getElementById('configAccountId').value;
    const channelId = document.getElementById('notificationChannel').value;
    const enabled = document.getElementById('notificationEnabled').checked;

    if (!channelId) {
    showToast('请选择通知渠道', 'warning');
    return;
    }

    try {
    const response = await fetch(`${apiBase}/message-notifications/${accountId}`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
        channel_id: parseInt(channelId),
        enabled: enabled
        })
    });

    if (response.ok) {
        showToast('通知配置保存成功', 'success');
        const modal = bootstrap.Modal.getInstance(document.getElementById('configNotificationModal'));
        modal.hide();
        loadMessageNotifications();
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('保存通知配置失败:', error);
    showToast('保存通知配置失败', 'danger');
    }
}

// ================================
// 【卡券管理菜单】相关功能
// ================================

// 加载卡券列表
async function loadCards() {
    try {
    const response = await fetch(`${apiBase}/cards`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const cards = await response.json();
        renderCardsList(cards);
        updateCardsStats(cards);
    } else {
        showToast('加载卡券列表失败', 'danger');
    }
    } catch (error) {
    console.error('加载卡券列表失败:', error);
    showToast('加载卡券列表失败', 'danger');
    }
}

// 渲染卡券列表
function renderCardsList(cards) {
    const tbody = document.getElementById('cardsTableBody');

    if (cards.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="8" class="text-center py-4 text-muted">
            <i class="bi bi-credit-card fs-1 d-block mb-3"></i>
            <h5>暂无卡券数据</h5>
            <p class="mb-0">点击"添加卡券"开始创建您的第一个卡券</p>
        </td>
        </tr>
    `;
    return;
    }

    tbody.innerHTML = '';

    cards.forEach(card => {
    const tr = document.createElement('tr');

    // 类型标签
    let typeBadge = '';
    switch(card.type) {
        case 'api':
        typeBadge = '<span class="badge bg-info">API接口</span>';
        break;
        case 'yifan_api':
        typeBadge = '<span class="badge bg-purple">亦凡卡劵API</span>';
        break;
        case 'text':
        typeBadge = '<span class="badge bg-success">固定文字</span>';
        break;
        case 'data':
        typeBadge = '<span class="badge bg-warning">批量数据</span>';
        break;
        case 'image':
        typeBadge = '<span class="badge bg-primary">图片</span>';
        break;
    }

    // 状态标签
    const statusBadge = card.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 数据量显示
    let dataCount = '-';
    if (card.type === 'data' && card.data_content) {
        const lines = card.data_content.split('\n').filter(line => line.trim());
        dataCount = lines.length;
    } else if (card.type === 'api') {
        dataCount = '∞';
    } else if (card.type === 'text') {
        dataCount = '1';
    } else if (card.type === 'image') {
        dataCount = '1';
    }

    // 延时时间显示
    const delayDisplay = card.delay_seconds > 0 ?
        `${card.delay_seconds}秒` :
        '<span class="text-muted">立即</span>';

    // 规格信息显示
    let specDisplay = '<span class="text-muted">普通卡券</span>';
    if (card.is_multi_spec && card.spec_name && card.spec_value) {
        let specInfo = `${card.spec_name}: ${card.spec_value}`;
        if (card.spec_name_2 && card.spec_value_2) {
            specInfo += `<br>${card.spec_name_2}: ${card.spec_value_2}`;
        }
        specDisplay = `<span class="badge bg-primary">${specInfo}</span>`;
    }

    tr.innerHTML = `
        <td>
        <div class="fw-bold">${card.name}</div>
        ${card.description ? `<small class="text-muted">${card.description}</small>` : ''}
        </td>
        <td>${typeBadge}</td>
        <td>${specDisplay}</td>
        <td>${dataCount}</td>
        <td>${delayDisplay}</td>
        <td>${statusBadge}</td>
        <td>
        <small class="text-muted">${formatDateTime(card.created_at)}</small>
        </td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editCard(${card.id})" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="testCard(${card.id})" title="测试">
            <i class="bi bi-play"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteCard(${card.id})" title="删除">
            <i class="bi bi-trash"></i>
            </button>
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 更新卡券统计
function updateCardsStats(cards) {
    const totalCards = cards.length;
    const apiCards = cards.filter(card => card.type === 'api').length;
    const textCards = cards.filter(card => card.type === 'text').length;
    const dataCards = cards.filter(card => card.type === 'data').length;

    document.getElementById('totalCards').textContent = totalCards;
    document.getElementById('apiCards').textContent = apiCards;
    document.getElementById('textCards').textContent = textCards;
    document.getElementById('dataCards').textContent = dataCards;
}

// 显示添加卡券模态框
function showAddCardModal() {
    document.getElementById('addCardForm').reset();
    toggleCardTypeFields();
    const modal = new bootstrap.Modal(document.getElementById('addCardModal'));
    modal.show();
}

// 切换卡券类型字段显示
function toggleCardTypeFields() {
    const cardType = document.getElementById('cardType')?.value || 'text';

    // 安全地设置元素显示状态
    const setDisplay = (id, condition) => {
        const element = document.getElementById(id);
        if (element) {
            element.style.display = condition ? 'block' : 'none';
        }
    };

    setDisplay('apiFields', cardType === 'api');
    setDisplay('yifanApiFields', cardType === 'yifan_api');
    setDisplay('textFields', cardType === 'text');
    setDisplay('dataFields', cardType === 'data');
    setDisplay('imageFields', cardType === 'image');

    // 如果是API类型，初始化API方法监听
    if (cardType === 'api') {
        toggleApiParamsHelp();
        // 添加API方法变化监听
        const apiMethodSelect = document.getElementById('apiMethod');
        if (apiMethodSelect) {
            apiMethodSelect.removeEventListener('change', toggleApiParamsHelp);
            apiMethodSelect.addEventListener('change', toggleApiParamsHelp);
        }
    }
}

// 切换API参数提示显示
function toggleApiParamsHelp() {
    const apiMethodElement = document.getElementById('apiMethod');
    if (!apiMethodElement) return;
    
    const apiMethod = apiMethodElement.value;
    const postParamsHelp = document.getElementById('postParamsHelp');

    if (postParamsHelp) {
        postParamsHelp.style.display = apiMethod === 'POST' ? 'block' : 'none';

        // 如果显示参数提示，添加点击事件
        if (apiMethod === 'POST') {
            initParamClickHandlers('apiParams', 'postParamsHelp');
        }
    }
}

// 初始化参数点击处理器
function initParamClickHandlers(textareaId, containerId) {
    const container = document.getElementById(containerId);
    const textarea = document.getElementById(textareaId);

    if (!container || !textarea) return;

    // 移除现有的点击事件监听器
    const paramNames = container.querySelectorAll('.param-name');
    paramNames.forEach(paramName => {
        paramName.removeEventListener('click', handleParamClick);
    });

    // 添加新的点击事件监听器
    paramNames.forEach(paramName => {
        paramName.addEventListener('click', function() {
            handleParamClick(this, textarea);
        });
    });
}

// 处理参数点击事件
function handleParamClick(paramElement, textarea) {
    const paramName = paramElement.textContent.trim();
    const paramValue = `{${paramName}}`;

    try {
        // 获取当前textarea的值
        let currentValue = textarea.value.trim();

        // 如果当前值为空或不是有效的JSON，创建新的JSON对象
        if (!currentValue || currentValue === '{}') {
            const newJson = {};
            newJson[paramName] = paramValue;
            textarea.value = JSON.stringify(newJson, null, 2);
        } else {
            // 尝试解析现有的JSON
            let jsonObj;
            try {
                jsonObj = JSON.parse(currentValue);
            } catch (e) {
                // 如果解析失败，创建新的JSON对象
                jsonObj = {};
            }

            // 添加新参数
            jsonObj[paramName] = paramValue;

            // 更新textarea
            textarea.value = JSON.stringify(jsonObj, null, 2);
        }

        // 触发change事件
        textarea.dispatchEvent(new Event('change'));

        // 显示成功提示
        showToast(`已添加参数: ${paramName}`, 'success');

    } catch (error) {
        console.error('添加参数时出错:', error);
        showToast('添加参数失败', 'danger');
    }
}

// 切换多规格字段显示
function toggleMultiSpecFields() {
    const isMultiSpec = document.getElementById('isMultiSpec').checked;
    document.getElementById('multiSpecFields').style.display = isMultiSpec ? 'block' : 'none';
}

// 初始化卡券图片文件选择器
function initCardImageFileSelector() {
    const fileInput = document.getElementById('cardImageFile');
    if (fileInput) {
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                // 验证文件类型
                if (!file.type.startsWith('image/')) {
                    showToast('❌ 请选择图片文件，当前文件类型：' + file.type, 'warning');
                    e.target.value = '';
                    hideCardImagePreview();
                    return;
                }

                // 验证文件大小（5MB）
                if (file.size > 5 * 1024 * 1024) {
                    showToast('❌ 图片文件大小不能超过 5MB，当前文件大小：' + (file.size / 1024 / 1024).toFixed(1) + 'MB', 'warning');
                    e.target.value = '';
                    hideCardImagePreview();
                    return;
                }

                // 验证图片尺寸
                validateCardImageDimensions(file, e.target);
            } else {
                hideCardImagePreview();
            }
        });
    }
}

// 验证卡券图片尺寸
function validateCardImageDimensions(file, inputElement) {
    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = function() {
        const width = this.naturalWidth;
        const height = this.naturalHeight;

        // 释放对象URL
        URL.revokeObjectURL(url);

        // 检查图片尺寸
        const maxDimension = 4096;
        const maxPixels = 8 * 1024 * 1024; // 8M像素
        const totalPixels = width * height;

        if (width > maxDimension || height > maxDimension) {
            showToast(`❌ 图片尺寸过大：${width}x${height}，最大允许：${maxDimension}x${maxDimension}像素`, 'warning');
            inputElement.value = '';
            hideCardImagePreview();
            return;
        }

        if (totalPixels > maxPixels) {
            showToast(`❌ 图片像素总数过大：${(totalPixels / 1024 / 1024).toFixed(1)}M像素，最大允许：8M像素`, 'warning');
            inputElement.value = '';
            hideCardImagePreview();
            return;
        }

        // 尺寸检查通过，显示预览和提示信息
        showCardImagePreview(file);

        // 如果图片较大，提示会被压缩
        if (width > 2048 || height > 2048) {
            showToast(`ℹ️ 图片尺寸较大（${width}x${height}），上传时将自动压缩以优化性能`, 'info');
        } else {
            showToast(`✅ 图片尺寸合适（${width}x${height}），可以上传`, 'success');
        }
    };

    img.onerror = function() {
        URL.revokeObjectURL(url);
        showToast('❌ 无法读取图片文件，请选择有效的图片', 'warning');
        inputElement.value = '';
        hideCardImagePreview();
    };

    img.src = url;
}

// 显示卡券图片预览
function showCardImagePreview(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const previewContainer = document.getElementById('cardImagePreview');
        const previewImg = document.getElementById('cardPreviewImg');

        previewImg.src = e.target.result;
        previewContainer.style.display = 'block';
    };
    reader.readAsDataURL(file);
}

// 隐藏卡券图片预览
function hideCardImagePreview() {
    const previewContainer = document.getElementById('cardImagePreview');
    if (previewContainer) {
        previewContainer.style.display = 'none';
    }
}

// 初始化编辑卡券图片文件选择器
function initEditCardImageFileSelector() {
    const fileInput = document.getElementById('editCardImageFile');
    if (fileInput) {
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                // 验证文件类型
                if (!file.type.startsWith('image/')) {
                    showToast('❌ 请选择图片文件，当前文件类型：' + file.type, 'warning');
                    e.target.value = '';
                    hideEditCardImagePreview();
                    return;
                }

                // 验证文件大小（5MB）
                if (file.size > 5 * 1024 * 1024) {
                    showToast('❌ 图片文件大小不能超过 5MB，当前文件大小：' + (file.size / 1024 / 1024).toFixed(1) + 'MB', 'warning');
                    e.target.value = '';
                    hideEditCardImagePreview();
                    return;
                }

                // 验证图片尺寸
                validateEditCardImageDimensions(file, e.target);
            } else {
                hideEditCardImagePreview();
            }
        });
    }
}

// 验证编辑卡券图片尺寸
function validateEditCardImageDimensions(file, inputElement) {
    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = function() {
        const width = this.naturalWidth;
        const height = this.naturalHeight;

        URL.revokeObjectURL(url);

        // 检查尺寸限制
        if (width > 4096 || height > 4096) {
            showToast(`❌ 图片尺寸过大（${width}x${height}），最大支持 4096x4096 像素`, 'warning');
            inputElement.value = '';
            hideEditCardImagePreview();
            return;
        }

        // 显示图片预览
        showEditCardImagePreview(file);

        // 如果图片较大，提示会被压缩
        if (width > 2048 || height > 2048) {
            showToast(`ℹ️ 图片尺寸较大（${width}x${height}），上传时将自动压缩以优化性能`, 'info');
        } else {
            showToast(`✅ 图片尺寸合适（${width}x${height}），可以上传`, 'success');
        }
    };

    img.onerror = function() {
        URL.revokeObjectURL(url);
        showToast('❌ 无法读取图片文件，请选择有效的图片', 'warning');
        inputElement.value = '';
        hideEditCardImagePreview();
    };

    img.src = url;
}

// 显示编辑卡券图片预览
function showEditCardImagePreview(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const previewImg = document.getElementById('editCardPreviewImg');
        const previewContainer = document.getElementById('editCardImagePreview');

        if (previewImg && previewContainer) {
            previewImg.src = e.target.result;
            previewContainer.style.display = 'block';
        }
    };
    reader.readAsDataURL(file);
}

// 隐藏编辑卡券图片预览
function hideEditCardImagePreview() {
    const previewContainer = document.getElementById('editCardImagePreview');
    if (previewContainer) {
        previewContainer.style.display = 'none';
    }
}

// 切换编辑多规格字段显示
function toggleEditMultiSpecFields() {
    const checkbox = document.getElementById('editIsMultiSpec');
    const fieldsDiv = document.getElementById('editMultiSpecFields');

    if (!checkbox) {
    console.error('编辑多规格开关元素未找到');
    return;
    }

    if (!fieldsDiv) {
    console.error('编辑多规格字段容器未找到');
    return;
    }

    const isMultiSpec = checkbox.checked;
    const displayStyle = isMultiSpec ? 'block' : 'none';

    console.log('toggleEditMultiSpecFields - 多规格状态:', isMultiSpec);
    console.log('toggleEditMultiSpecFields - 设置显示样式:', displayStyle);

    fieldsDiv.style.display = displayStyle;

    // 验证设置是否生效
    console.log('toggleEditMultiSpecFields - 实际显示样式:', fieldsDiv.style.display);
}

// 清空添加卡券表单
function clearAddCardForm() {
    try {
    // 安全地清空表单字段
    const setElementValue = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
        if (element.type === 'checkbox') {
            element.checked = value;
        } else {
            element.value = value;
        }
        } else {
        console.warn(`Element with id '${id}' not found`);
        }
    };

    const setElementDisplay = (id, display) => {
        const element = document.getElementById(id);
        if (element) {
        element.style.display = display;
        } else {
        console.warn(`Element with id '${id}' not found`);
        }
    };

    // 清空基本字段
    setElementValue('cardName', '');
    setElementValue('cardType', 'text');
    setElementValue('cardDescription', '');
    setElementValue('cardDelaySeconds', '0');
    setElementValue('isMultiSpec', false);
    setElementValue('specName', '');
    setElementValue('specValue', '');
    setElementValue('specName2', '');
    setElementValue('specValue2', '');

    // 隐藏多规格字段
    setElementDisplay('multiSpecFields', 'none');

    // 清空类型相关字段
    setElementValue('textContent', '');
    setElementValue('dataContent', '');
    setElementValue('apiUrl', '');
    setElementValue('apiMethod', 'GET');
    setElementValue('apiHeaders', '');
    setElementValue('apiParams', '');
    setElementValue('apiTimeout', '10');
    setElementValue('yifanUserId', '');
    setElementValue('yifanUserKey', '');
    setElementValue('yifanGoodsId', '');
    setElementValue('yifanCallbackUrl', '');
    setElementValue('yifanRequireAccount', false);

    // 重置字段显示
    toggleCardTypeFields();
    } catch (error) {
    console.error('清空表单时出错:', error);
    }
}

// 保存卡券
async function saveCard() {
    try {
    const cardType = document.getElementById('cardType').value;
    const cardName = document.getElementById('cardName').value;

    if (!cardType || !cardName) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    // 检查多规格设置
    const isMultiSpec = document.getElementById('isMultiSpec').checked;
    const specName = document.getElementById('specName').value;
    const specValue = document.getElementById('specValue').value;
    const specName2 = document.getElementById('specName2').value;
    const specValue2 = document.getElementById('specValue2').value;

    // 调试日志
    console.log('[DEBUG] 创建卡券 - isMultiSpec:', isMultiSpec);
    console.log('[DEBUG] 创建卡券 - specName:', specName);
    console.log('[DEBUG] 创建卡券 - specValue:', specValue);
    console.log('[DEBUG] 创建卡券 - specName2:', specName2);
    console.log('[DEBUG] 创建卡券 - specValue2:', specValue2);

    // 验证多规格字段
    if (isMultiSpec && (!specName || !specValue)) {
        showToast('多规格卡券必须填写规格1名称和规格1值', 'warning');
        return;
    }

    const cardData = {
        name: cardName,
        type: cardType,
        description: document.getElementById('cardDescription').value,
        delay_seconds: parseInt(document.getElementById('cardDelaySeconds').value) || 0,
        enabled: true,
        is_multi_spec: isMultiSpec,
        spec_name: isMultiSpec ? specName : null,
        spec_value: isMultiSpec ? specValue : null,
        spec_name_2: isMultiSpec ? specName2 : null,
        spec_value_2: isMultiSpec ? specValue2 : null
    };

    // 调试日志 - 显示完整的 cardData
    console.log('[DEBUG] 创建卡券 - 发送的 cardData:', JSON.stringify(cardData, null, 2));

    // 根据类型添加特定配置
    switch(cardType) {
        case 'api':
        // 验证和解析JSON字段
        let headers = '{}';
        let params = '{}';

        try {
            const headersInput = document.getElementById('apiHeaders').value.trim();
            if (headersInput) {
            JSON.parse(headersInput); // 验证JSON格式
            headers = headersInput;
            }
        } catch (e) {
            showToast('请求头格式错误，请输入有效的JSON', 'warning');
            return;
        }

        try {
            const paramsInput = document.getElementById('apiParams').value.trim();
            if (paramsInput) {
            JSON.parse(paramsInput); // 验证JSON格式
            params = paramsInput;
            }
        } catch (e) {
            showToast('请求参数格式错误，请输入有效的JSON', 'warning');
            return;
        }

        cardData.api_config = {
            url: document.getElementById('apiUrl').value,
            method: document.getElementById('apiMethod').value,
            timeout: parseInt(document.getElementById('apiTimeout').value),
            headers: headers,
            params: params
        };
        break;
        case 'yifan_api':
        // 验证必填字段
        const yifanUserId = document.getElementById('yifanUserId').value.trim();
        const yifanUserKey = document.getElementById('yifanUserKey').value.trim();
        const yifanGoodsId = document.getElementById('yifanGoodsId').value.trim();

        if (!yifanUserId || !yifanUserKey || !yifanGoodsId) {
            showToast('请填写商户ID、商户KEY和商品ID', 'warning');
            return;
        }

        // 亦凡API配置也存储在api_config字段中
        cardData.api_config = {
            user_id: yifanUserId,
            user_key: yifanUserKey,
            goods_id: yifanGoodsId,
            callback_url: document.getElementById('yifanCallbackUrl').value.trim(),
            require_account: document.getElementById('yifanRequireAccount').checked
        };
        break;
        case 'text':
        cardData.text_content = document.getElementById('textContent').value;
        break;
        case 'data':
        cardData.data_content = document.getElementById('dataContent').value;
        break;
        case 'image':
        // 处理图片上传
        const imageFile = document.getElementById('cardImageFile').files[0];
        if (!imageFile) {
            showToast('请选择图片文件', 'warning');
            return;
        }

        // 上传图片
        const formData = new FormData();
        formData.append('image', imageFile);

        const uploadResponse = await fetch(`${apiBase}/upload-image`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            },
            body: formData
        });

        if (!uploadResponse.ok) {
            const errorData = await uploadResponse.json();
            showToast(`图片上传失败: ${errorData.detail || '未知错误'}`, 'danger');
            return;
        }

        const uploadResult = await uploadResponse.json();
        cardData.image_url = uploadResult.image_url;
        break;
    }

    // 获取"生成对应发货规则"开关状态
    const generateDeliveryRule = document.getElementById('generateDeliveryRule').checked;
    
    const response = await fetch(`${apiBase}/cards`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            ...cardData,
            generate_delivery_rule: generateDeliveryRule
        })
    });

    if (response.ok) {
        showToast('卡券保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('addCardModal')).hide();
        // 清空表单
        clearAddCardForm();
        loadCards();
    } else {
        let errorMessage = '保存失败';
        try {
        const errorData = await response.json();
        errorMessage = errorData.error || errorData.detail || errorMessage;
        } catch (e) {
        // 如果不是JSON格式，尝试获取文本
        try {
            const errorText = await response.text();
            errorMessage = errorText || errorMessage;
        } catch (e2) {
            errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        }
        }
        showToast(`保存失败: ${errorMessage}`, 'danger');
    }
    } catch (error) {
    console.error('保存卡券失败:', error);
    showToast(`网络错误: ${error.message}`, 'danger');
    }
}
// ================================
// 【自动发货菜单】相关功能
// ================================

// 加载发货规则列表
async function loadDeliveryRules() {
    try {
    const response = await fetch(`${apiBase}/delivery-rules`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const rules = await response.json();
        renderDeliveryRulesList(rules);
        updateDeliveryStats(rules);

        // 同时加载卡券列表用于下拉选择
        loadCardsForSelect();
    } else {
        showToast('加载发货规则失败', 'danger');
    }
    } catch (error) {
    console.error('加载发货规则失败:', error);
    showToast('加载发货规则失败', 'danger');
    }
}

// 渲染发货规则列表
function renderDeliveryRulesList(rules) {
    const tbody = document.getElementById('deliveryRulesTableBody');

    if (rules.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="7" class="text-center py-4 text-muted">
            <i class="bi bi-truck fs-1 d-block mb-3"></i>
            <h5>暂无发货规则</h5>
            <p class="mb-0">点击"添加规则"开始配置自动发货规则</p>
        </td>
        </tr>
    `;
    return;
    }

    tbody.innerHTML = '';

    rules.forEach(rule => {
    const tr = document.createElement('tr');

    // 状态标签
    const statusBadge = rule.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 卡券类型标签
    let cardTypeBadge = '<span class="badge bg-secondary">未知</span>';
    if (rule.card_type) {
        switch(rule.card_type) {
        case 'api':
            cardTypeBadge = '<span class="badge bg-info">API接口</span>';
            break;
        case 'yifan_api':
            cardTypeBadge = '<span class="badge bg-purple">亦凡卡劵API</span>';
            break;
        case 'text':
            cardTypeBadge = '<span class="badge bg-success">固定文字</span>';
            break;
        case 'data':
            cardTypeBadge = '<span class="badge bg-warning">批量数据</span>';
            break;
        case 'image':
            cardTypeBadge = '<span class="badge bg-primary">图片</span>';
            break;
        }
    }

    tr.innerHTML = `
        <td>
        <div class="fw-bold">${rule.keyword}</div>
        ${rule.description ? `<small class="text-muted">${rule.description}</small>` : ''}
        </td>
        <td>
        <div>
            <span class="badge bg-primary">${rule.card_name || '未知卡券'}</span>
            ${rule.is_multi_spec && rule.spec_name && rule.spec_value ?
            `<br><small class="text-muted mt-1 d-block"><i class="bi bi-tags"></i> ${rule.spec_name}: ${rule.spec_value}${rule.spec_name_2 && rule.spec_value_2 ? `<br><i class="bi bi-tags"></i> ${rule.spec_name_2}: ${rule.spec_value_2}` : ''}</small>` :
            ''}
        </div>
        </td>
        <td>${cardTypeBadge}</td>
        <!-- 隐藏发货数量列 -->
        <!-- <td><span class="badge bg-info">${rule.delivery_count || 1}</span></td> -->
        <td>${statusBadge}</td>
        <td>
        <span class="badge bg-warning">${rule.delivery_times || 0}</span>
        </td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editDeliveryRule(${rule.id})" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="testDeliveryRule(${rule.id})" title="测试">
            <i class="bi bi-play"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteDeliveryRule(${rule.id})" title="删除">
            <i class="bi bi-trash"></i>
            </button>
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 更新发货统计
async function updateDeliveryStats(rules) {
    const totalRules = rules.length;
    const activeRules = rules.filter(rule => rule.enabled).length;
    const totalDeliveries = rules.reduce((sum, rule) => sum + (rule.delivery_times || 0), 0);

    document.getElementById('totalRules').textContent = totalRules;
    document.getElementById('activeRules').textContent = activeRules;
    document.getElementById('totalDeliveries').textContent = totalDeliveries;

    // 刷新今日发货统计
    await refreshTodayDeliveryCount();
}

// 刷新今日发货统计（独立函数，可在发货后单独调用）
async function refreshTodayDeliveryCount() {
    try {
        const response = await fetch(`${apiBase}/delivery-rules/stats`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        if (response.ok) {
            const stats = await response.json();
            const todayEl = document.getElementById('todayDeliveries');
            if (todayEl) {
                todayEl.textContent = stats.today_delivery_count || 0;
            }
        }
    } catch (error) {
        console.error('获取今日发货统计失败:', error);
    }
}

// 显示添加发货规则模态框
function showAddDeliveryRuleModal() {
    document.getElementById('addDeliveryRuleForm').reset();
    loadCardsForSelect(); // 加载卡券选项
    const modal = new bootstrap.Modal(document.getElementById('addDeliveryRuleModal'));
    modal.show();
}

// 加载卡券列表用于下拉选择
async function loadCardsForSelect() {
    try {
    const response = await fetch(`${apiBase}/cards`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const cards = await response.json();
        const select = document.getElementById('selectedCard');

        // 清空现有选项
        select.innerHTML = '<option value="">请选择卡券</option>';

        cards.forEach(card => {
        if (card.enabled) { // 只显示启用的卡券
            const option = document.createElement('option');
            option.value = card.id;

            // 构建显示文本
            let displayText = card.name;

            // 添加类型信息
            let typeText;
            switch(card.type) {
                case 'api':
                    typeText = 'API';
                    break;
                case 'text':
                    typeText = '固定文字';
                    break;
                case 'data':
                    typeText = '批量数据';
                    break;
                case 'image':
                    typeText = '图片';
                    break;
                default:
                    typeText = '未知类型';
            }
            displayText += ` (${typeText})`;

            // 添加规格信息
            if (card.is_multi_spec && card.spec_name && card.spec_value) {
            let specInfo = `${card.spec_name}:${card.spec_value}`;
            if (card.spec_name_2 && card.spec_value_2) {
                specInfo += `, ${card.spec_name_2}:${card.spec_value_2}`;
            }
            displayText += ` [${specInfo}]`;
            }

            option.textContent = displayText;
            select.appendChild(option);
        }
        });
    }
    } catch (error) {
    console.error('加载卡券选项失败:', error);
    }
}

// 保存发货规则
async function saveDeliveryRule() {
    try {
    const keyword = document.getElementById('productKeyword').value;
    const cardId = document.getElementById('selectedCard').value;
    const deliveryCount = document.getElementById('deliveryCount').value || 1;
    const enabled = document.getElementById('ruleEnabled').checked;
    const description = document.getElementById('ruleDescription').value;

    if (!keyword || !cardId) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    const ruleData = {
        keyword: keyword,
        card_id: parseInt(cardId),
        delivery_count: parseInt(deliveryCount),
        enabled: enabled,
        description: description
    };

    const response = await fetch(`${apiBase}/delivery-rules`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(ruleData)
    });

    if (response.ok) {
        showToast('发货规则保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('addDeliveryRuleModal')).hide();
        loadDeliveryRules();
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('保存发货规则失败:', error);
    showToast('保存发货规则失败', 'danger');
    }
}

// 编辑卡券
async function editCard(cardId) {
    try {
    // 获取卡券详情
    const response = await fetch(`${apiBase}/cards/${cardId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const card = await response.json();

        // 填充编辑表单
        document.getElementById('editCardId').value = card.id;
        document.getElementById('editCardName').value = card.name;
        document.getElementById('editCardType').value = card.type;
        document.getElementById('editCardDescription').value = card.description || '';
        document.getElementById('editCardDelaySeconds').value = card.delay_seconds || 0;
        document.getElementById('editCardEnabled').checked = card.enabled;

        // 填充多规格字段
        const isMultiSpec = card.is_multi_spec || false;
        document.getElementById('editIsMultiSpec').checked = isMultiSpec;
        document.getElementById('editSpecName').value = card.spec_name || '';
        document.getElementById('editSpecValue').value = card.spec_value || '';
        document.getElementById('editSpecName2').value = card.spec_name_2 || '';
        document.getElementById('editSpecValue2').value = card.spec_value_2 || '';

        // 添加调试日志
        console.log('编辑卡券 - 多规格状态:', isMultiSpec);
        console.log('编辑卡券 - 规格1名称:', card.spec_name);
        console.log('编辑卡券 - 规格1值:', card.spec_value);
        console.log('编辑卡券 - 规格2名称:', card.spec_name_2);
        console.log('编辑卡券 - 规格2值:', card.spec_value_2);

        // 根据类型填充特定字段
        if (card.type === 'api' && card.api_config) {
        // 记住原始 api_config，保存时合并回去，避免表单未覆盖的扩展字段被丢弃
        window._editingCardApiConfig = card.api_config;
        // headers/params 可能是对象（后端存的是嵌套 JSON），需要序列化后再填入文本框，
        // 否则会变成 "[object Object]" 导致保存时 JSON 校验报错
        const _fmtJsonField = (v) => (typeof v === 'string' ? v : JSON.stringify(v || {}, null, 2));
        document.getElementById('editApiUrl').value = card.api_config.url || '';
        document.getElementById('editApiMethod').value = card.api_config.method || 'GET';
        document.getElementById('editApiTimeout').value = card.api_config.timeout || 10;
        document.getElementById('editApiHeaders').value = _fmtJsonField(card.api_config.headers);
        document.getElementById('editApiParams').value = _fmtJsonField(card.api_config.params);
        } else if (card.type === 'yifan_api' && card.api_config) {
        document.getElementById('editYifanUserId').value = card.api_config.user_id || '';
        document.getElementById('editYifanUserKey').value = card.api_config.user_key || '';
        document.getElementById('editYifanGoodsId').value = card.api_config.goods_id || '';
        document.getElementById('editYifanCallbackUrl').value = card.api_config.callback_url || '';
        document.getElementById('editYifanRequireAccount').checked = card.api_config.require_account || false;
        } else if (card.type === 'text') {
        document.getElementById('editTextContent').value = card.text_content || '';
        } else if (card.type === 'data') {
        document.getElementById('editDataContent').value = card.data_content || '';
        } else if (card.type === 'image') {
        // 处理图片类型
        const currentImagePreview = document.getElementById('editCurrentImagePreview');
        const currentImg = document.getElementById('editCurrentImg');
        const noImageText = document.getElementById('editNoImageText');

        if (card.image_url) {
            // 显示当前图片
            currentImg.src = card.image_url;
            currentImagePreview.style.display = 'block';
            noImageText.style.display = 'none';
        } else {
            // 没有图片
            currentImagePreview.style.display = 'none';
            noImageText.style.display = 'block';
        }

        // 清空文件选择器和预览
        document.getElementById('editCardImageFile').value = '';
        document.getElementById('editCardImagePreview').style.display = 'none';
        }

        // 显示对应的字段
        toggleEditCardTypeFields();

        // 使用延迟调用确保DOM更新完成后再显示多规格字段
        setTimeout(() => {
        console.log('延迟调用 toggleEditMultiSpecFields');
        toggleEditMultiSpecFields();

        // 验证多规格字段是否正确显示
        const multiSpecElement = document.getElementById('editMultiSpecFields');
        const isChecked = document.getElementById('editIsMultiSpec').checked;
        console.log('多规格元素存在:', !!multiSpecElement);
        console.log('多规格开关状态:', isChecked);
        console.log('多规格字段显示状态:', multiSpecElement ? multiSpecElement.style.display : 'element not found');
        }, 100);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('editCardModal'));
        modal.show();
    } else {
        showToast('获取卡券详情失败', 'danger');
    }
    } catch (error) {
    console.error('获取卡券详情失败:', error);
    showToast('获取卡券详情失败', 'danger');
    }
}

// 切换编辑卡券类型字段显示
function toggleEditCardTypeFields() {
    const cardType = document.getElementById('editCardType').value;

    document.getElementById('editApiFields').style.display = cardType === 'api' ? 'block' : 'none';
    document.getElementById('editYifanApiFields').style.display = cardType === 'yifan_api' ? 'block' : 'none';
    document.getElementById('editTextFields').style.display = cardType === 'text' ? 'block' : 'none';
    document.getElementById('editDataFields').style.display = cardType === 'data' ? 'block' : 'none';
    document.getElementById('editImageFields').style.display = cardType === 'image' ? 'block' : 'none';

    // 如果是API类型，初始化API方法监听
    if (cardType === 'api') {
        toggleEditApiParamsHelp();
        // 添加API方法变化监听
        const editApiMethodSelect = document.getElementById('editApiMethod');
        if (editApiMethodSelect) {
            editApiMethodSelect.removeEventListener('change', toggleEditApiParamsHelp);
            editApiMethodSelect.addEventListener('change', toggleEditApiParamsHelp);
        }
    }
}

// 切换编辑API参数提示显示
function toggleEditApiParamsHelp() {
    const apiMethod = document.getElementById('editApiMethod').value;
    const editPostParamsHelp = document.getElementById('editPostParamsHelp');

    if (editPostParamsHelp) {
        editPostParamsHelp.style.display = apiMethod === 'POST' ? 'block' : 'none';

        // 如果显示参数提示，添加点击事件
        if (apiMethod === 'POST') {
            initParamClickHandlers('editApiParams', 'editPostParamsHelp');
        }
    }
}

// 更新卡券
async function updateCard() {
    try {
    const cardId = document.getElementById('editCardId').value;
    const cardType = document.getElementById('editCardType').value;
    const cardName = document.getElementById('editCardName').value;

    if (!cardType || !cardName) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    // 检查多规格设置
    const isMultiSpec = document.getElementById('editIsMultiSpec').checked;
    const specName = document.getElementById('editSpecName').value;
    const specValue = document.getElementById('editSpecValue').value;
    const specName2 = document.getElementById('editSpecName2').value;
    const specValue2 = document.getElementById('editSpecValue2').value;

    // 调试日志
    console.log('[DEBUG] 更新卡券 - isMultiSpec:', isMultiSpec);
    console.log('[DEBUG] 更新卡券 - specName:', specName);
    console.log('[DEBUG] 更新卡券 - specValue:', specValue);
    console.log('[DEBUG] 更新卡券 - specName2:', specName2);
    console.log('[DEBUG] 更新卡券 - specValue2:', specValue2);

    // 验证多规格字段
    if (isMultiSpec && (!specName || !specValue)) {
        showToast('多规格卡券必须填写规格1名称和规格1值', 'warning');
        return;
    }

    const cardData = {
        name: cardName,
        type: cardType,
        description: document.getElementById('editCardDescription').value,
        delay_seconds: parseInt(document.getElementById('editCardDelaySeconds').value) || 0,
        enabled: document.getElementById('editCardEnabled').checked,
        is_multi_spec: isMultiSpec,
        spec_name: isMultiSpec ? specName : null,
        spec_value: isMultiSpec ? specValue : null,
        spec_name_2: isMultiSpec ? specName2 : null,
        spec_value_2: isMultiSpec ? specValue2 : null
    };

    // 调试日志 - 显示完整的 cardData
    console.log('[DEBUG] 发送的 cardData:', JSON.stringify(cardData, null, 2));

    // 根据类型添加特定配置
    switch(cardType) {
        case 'api':
        // 验证和解析JSON字段
        let headers = '{}';
        let params = '{}';

        try {
            const headersInput = document.getElementById('editApiHeaders').value.trim();
            if (headersInput) {
            JSON.parse(headersInput);
            headers = headersInput;
            }
        } catch (e) {
            showToast('请求头格式错误，请输入有效的JSON', 'warning');
            return;
        }

        try {
            const paramsInput = document.getElementById('editApiParams').value.trim();
            if (paramsInput) {
            JSON.parse(paramsInput);
            params = paramsInput;
            }
        } catch (e) {
            showToast('请求参数格式错误，请输入有效的JSON', 'warning');
            return;
        }

        // 以原始 api_config 为底合并表单字段，保留表单未覆盖的扩展配置
        cardData.api_config = Object.assign({}, window._editingCardApiConfig || {}, {
            url: document.getElementById('editApiUrl').value,
            method: document.getElementById('editApiMethod').value,
            timeout: parseInt(document.getElementById('editApiTimeout').value) || 10,
            headers: headers,
            params: params
        });
        break;
        case 'yifan_api':
        // 验证必填字段
        const editYifanUserId = document.getElementById('editYifanUserId').value.trim();
        const editYifanUserKey = document.getElementById('editYifanUserKey').value.trim();
        const editYifanGoodsId = document.getElementById('editYifanGoodsId').value.trim();

        if (!editYifanUserId || !editYifanUserKey || !editYifanGoodsId) {
            showToast('请填写商户ID、商户KEY和商品ID', 'warning');
            return;
        }

        // 亦凡API配置也存储在api_config字段中
        cardData.api_config = {
            user_id: editYifanUserId,
            user_key: editYifanUserKey,
            goods_id: editYifanGoodsId,
            callback_url: document.getElementById('editYifanCallbackUrl').value.trim(),
            require_account: document.getElementById('editYifanRequireAccount').checked
        };
        break;
        case 'text':
        cardData.text_content = document.getElementById('editTextContent').value;
        break;
        case 'data':
        cardData.data_content = document.getElementById('editDataContent').value;
        break;
        case 'image':
        // 处理图片类型 - 如果有新图片则上传，否则保持原有图片
        const imageFile = document.getElementById('editCardImageFile').files[0];
        if (imageFile) {
            // 有新图片，需要上传
            await updateCardWithImage(cardId, cardData, imageFile);
            return; // 提前返回，因为上传图片是异步的
        }
        // 没有新图片，保持原有配置，继续正常更新流程
        break;
    }

    const response = await fetch(`${apiBase}/cards/${cardId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(cardData)
    });

    if (response.ok) {
        showToast('卡券更新成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editCardModal')).hide();
        loadCards();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新卡券失败:', error);
    showToast('更新卡券失败', 'danger');
    }
}

// 更新带图片的卡券
async function updateCardWithImage(cardId, cardData, imageFile) {
    try {
        // 创建FormData对象
        const formData = new FormData();

        // 添加图片文件
        formData.append('image', imageFile);

        // 添加卡券数据
        Object.keys(cardData).forEach(key => {
            if (cardData[key] !== null && cardData[key] !== undefined) {
                if (typeof cardData[key] === 'object') {
                    formData.append(key, JSON.stringify(cardData[key]));
                } else {
                    formData.append(key, cardData[key]);
                }
            }
        });

        const response = await fetch(`${apiBase}/cards/${cardId}/image`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`
                // 不设置Content-Type，让浏览器自动设置multipart/form-data
            },
            body: formData
        });

        if (response.ok) {
            showToast('卡券更新成功', 'success');
            bootstrap.Modal.getInstance(document.getElementById('editCardModal')).hide();
            loadCards();
        } else {
            const error = await response.text();
            showToast(`更新失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('更新带图片的卡券失败:', error);
        showToast('更新卡券失败', 'danger');
    }
}



// 测试卡券（占位函数）
function testCard(cardId) {
    showToast('测试功能开发中...', 'info');
}

// 删除卡券
async function deleteCard(cardId) {
    if (confirm('确定要删除这个卡券吗？删除后无法恢复！')) {
    try {
        const response = await fetch(`${apiBase}/cards/${cardId}`, {
        method: 'DELETE',
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
        });

        if (response.ok) {
        showToast('卡券删除成功', 'success');
        loadCards();
        } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('删除卡券失败:', error);
        showToast('删除卡券失败', 'danger');
    }
    }
}

// 编辑发货规则
async function editDeliveryRule(ruleId) {
    try {
    // 获取发货规则详情
    const response = await fetch(`${apiBase}/delivery-rules/${ruleId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const rule = await response.json();

        // 填充编辑表单
        document.getElementById('editRuleId').value = rule.id;
        document.getElementById('editProductKeyword').value = rule.keyword;
        document.getElementById('editDeliveryCount').value = rule.delivery_count || 1;
        document.getElementById('editRuleEnabled').checked = rule.enabled;
        document.getElementById('editRuleDescription').value = rule.description || '';

        // 加载卡券选项并设置当前选中的卡券
        await loadCardsForEditSelect();
        document.getElementById('editSelectedCard').value = rule.card_id;

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('editDeliveryRuleModal'));
        modal.show();
    } else {
        showToast('获取发货规则详情失败', 'danger');
    }
    } catch (error) {
    console.error('获取发货规则详情失败:', error);
    showToast('获取发货规则详情失败', 'danger');
    }
}

// 加载卡券列表用于编辑时的下拉选择
async function loadCardsForEditSelect() {
    try {
    const response = await fetch(`${apiBase}/cards`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const cards = await response.json();
        const select = document.getElementById('editSelectedCard');

        // 清空现有选项
        select.innerHTML = '<option value="">请选择卡券</option>';

        cards.forEach(card => {
        if (card.enabled) { // 只显示启用的卡券
            const option = document.createElement('option');
            option.value = card.id;

            // 构建显示文本
            let displayText = card.name;

            // 添加类型信息
            let typeText;
            switch(card.type) {
                case 'api':
                    typeText = 'API';
                    break;
                case 'text':
                    typeText = '固定文字';
                    break;
                case 'data':
                    typeText = '批量数据';
                    break;
                case 'image':
                    typeText = '图片';
                    break;
                default:
                    typeText = '未知类型';
            }
            displayText += ` (${typeText})`;

            // 添加规格信息
            if (card.is_multi_spec && card.spec_name && card.spec_value) {
            let specInfo = `${card.spec_name}:${card.spec_value}`;
            if (card.spec_name_2 && card.spec_value_2) {
                specInfo += `, ${card.spec_name_2}:${card.spec_value_2}`;
            }
            displayText += ` [${specInfo}]`;
            }

            option.textContent = displayText;
            select.appendChild(option);
        }
        });
    }
    } catch (error) {
    console.error('加载卡券选项失败:', error);
    }
}

// 更新发货规则
async function updateDeliveryRule() {
    try {
    const ruleId = document.getElementById('editRuleId').value;
    const keyword = document.getElementById('editProductKeyword').value;
    const cardId = document.getElementById('editSelectedCard').value;
    const deliveryCount = document.getElementById('editDeliveryCount').value || 1;
    const enabled = document.getElementById('editRuleEnabled').checked;
    const description = document.getElementById('editRuleDescription').value;

    if (!keyword || !cardId) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    const ruleData = {
        keyword: keyword,
        card_id: parseInt(cardId),
        delivery_count: parseInt(deliveryCount),
        enabled: enabled,
        description: description
    };

    const response = await fetch(`${apiBase}/delivery-rules/${ruleId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(ruleData)
    });

    if (response.ok) {
        showToast('发货规则更新成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editDeliveryRuleModal')).hide();
        loadDeliveryRules();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新发货规则失败:', error);
    showToast('更新发货规则失败', 'danger');
    }
}

// 测试发货规则（占位函数）
function testDeliveryRule(ruleId) {
    showToast('测试功能开发中...', 'info');
}

// 删除发货规则
async function deleteDeliveryRule(ruleId) {
    if (confirm('确定要删除这个发货规则吗？删除后无法恢复！')) {
    try {
        const response = await fetch(`${apiBase}/delivery-rules/${ruleId}`, {
        method: 'DELETE',
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
        });

        if (response.ok) {
        showToast('发货规则删除成功', 'success');
        loadDeliveryRules();
        } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('删除发货规则失败:', error);
        showToast('删除发货规则失败', 'danger');
    }
    }
}



// ==================== 系统设置功能 ====================

// 加载用户设置
async function loadUserSettings() {
    const token = getAuthToken();
    if (!token) return;
    try {
        const response = await fetch(`${apiBase}/user-settings`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const settings = await response.json();

            // 设置主题颜色
            if (settings.theme_color && settings.theme_color.value) {
                const color = settings.theme_color.value;
                const picker = document.getElementById('themeColorPicker');
                const hex = document.getElementById('themeColorHex');
                if (picker) picker.value = color;
                if (hex) hex.value = color;
                applyThemeColor(color);
                updatePresetSelection(color);
            } else {
                localStorage.removeItem('themeColor');
            }
        }
    } catch (error) {
        console.error('加载用户设置失败:', error);
    }
}

// 应用主题颜色（支持任意十六进制颜色）
function applyThemeColor(color) {
    if (!color || !color.startsWith('#')) return;

    document.documentElement.style.setProperty('--primary-color', color);

    // 计算hover颜色（稍微深一点）
    const hoverColor = adjustBrightness(color, -20);
    document.documentElement.style.setProperty('--primary-hover', hoverColor);

    // 计算浅色版本（用于某些UI元素）
    const lightColor = adjustBrightness(color, 40);
    document.documentElement.style.setProperty('--primary-light', lightColor);

    // 缓存主题色，供页面首次渲染前预应用，避免刷新闪回默认蓝色
    localStorage.setItem('themeColor', color);
}

// 调整颜色亮度
function adjustBrightness(hex, percent) {
    const num = parseInt(hex.replace("#", ""), 16);
    const amt = Math.round(2.55 * percent);
    const R = (num >> 16) + amt;
    const G = (num >> 8 & 0x00FF) + amt;
    const B = (num & 0x0000FF) + amt;
    return "#" + (0x1000000 + (R < 255 ? R < 1 ? 0 : R : 255) * 0x10000 +
        (G < 255 ? G < 1 ? 0 : G : 255) * 0x100 +
        (B < 255 ? B < 1 ? 0 : B : 255)).toString(16).slice(1);
}

// 更新预设颜色按钮选中状态
function updatePresetSelection(selectedColor) {
    document.querySelectorAll('.color-preset').forEach(btn => {
        if (btn.dataset.color === selectedColor) {
            btn.style.border = '2px solid #333';
            btn.style.boxShadow = '0 0 0 2px #fff, 0 0 0 4px #333';
        } else {
            btn.style.border = '2px solid transparent';
            btn.style.boxShadow = 'none';
        }
    });
}

// ==================== 菜单管理功能 ====================

// 菜单项配置（默认顺序）
const DEFAULT_MENU_ITEMS = [
    { id: 'dashboard', name: '仪表盘', icon: 'bi-speedometer2', required: true },
    { id: 'accounts', name: '账号管理', icon: 'bi-person-circle', required: false },
    { id: 'item-publish', name: '商品发布', icon: 'bi-bag-plus', required: false },
    { id: 'items', name: '商品管理', icon: 'bi-box-seam', required: false },
    { id: 'orders', name: '订单管理', icon: 'bi-receipt-cutoff', required: false },
    { id: 'auto-reply', name: '自动回复', icon: 'bi-chat-left-text', required: false },
    { id: 'message-filters', name: '消息过滤', icon: 'bi-funnel', required: false },
    { id: 'items-reply', name: '指定商品回复', icon: 'bi-chat-left-text', required: false },
    { id: 'cards', name: '卡券管理', icon: 'bi-credit-card', required: false },
    { id: 'auto-delivery', name: '自动发货', icon: 'bi-truck', required: false },
    { id: 'notification-channels', name: '通知渠道', icon: 'bi-bell', required: false },
    { id: 'message-notifications', name: '消息通知', icon: 'bi-chat-dots', required: false },
    { id: 'online-im', name: '在线客服', icon: 'bi-headset', required: false },
    { id: 'blacklist', name: '黑名单管理', icon: 'bi-person-x', required: false },
    { id: 'system-settings', name: '系统设置', icon: 'bi-gear', required: true },
    { id: 'about', name: '关于', icon: 'bi-info-circle', required: true }
];

// 当前菜单设置
let menuSettings = {};  // 显示/隐藏设置
let menuOrder = [];     // 菜单顺序
let draggedItem = null; // 当前拖拽的元素

// 获取排序后的菜单项
function getSortedMenuItems() {
    if (menuOrder.length === 0) {
        return [...DEFAULT_MENU_ITEMS];
    }

    // 按保存的顺序排列
    const sorted = [];
    menuOrder.forEach(id => {
        const item = DEFAULT_MENU_ITEMS.find(m => m.id === id);
        if (item) sorted.push(item);
    });

    // 添加可能遗漏的新菜单项
    DEFAULT_MENU_ITEMS.forEach(item => {
        if (!sorted.find(m => m.id === item.id)) {
            sorted.push(item);
        }
    });

    return sorted;
}

// 初始化菜单管理UI
function initMenuManagement() {
    const container = document.getElementById('menuManagementList');
    if (!container) return;

    const sortedItems = getSortedMenuItems();

    container.innerHTML = sortedItems.map(item => `
        <div class="menu-sort-item" draggable="true" data-menu-id="${item.id}">
            <span class="drag-handle">
                <i class="bi bi-grip-vertical"></i>
            </span>
            <span class="menu-icon">
                <i class="bi ${item.icon}"></i>
            </span>
            <span class="menu-name">${item.name}</span>
            ${item.required ? '<span class="badge bg-secondary">必选</span>' : ''}
            <div class="menu-checkbox">
                <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" id="menu-${item.id}"
                        ${item.required ? 'checked disabled' : (menuSettings[item.id] !== false ? 'checked' : '')}
                        data-menu-id="${item.id}">
                </div>
            </div>
        </div>
    `).join('');

    // 绑定拖拽事件
    initDragAndDrop();
}

// 初始化拖拽功能
function initDragAndDrop() {
    const container = document.getElementById('menuManagementList');
    if (!container) return;

    const items = container.querySelectorAll('.menu-sort-item');

    items.forEach(item => {
        item.addEventListener('dragstart', handleDragStart);
        item.addEventListener('dragend', handleDragEnd);
        item.addEventListener('dragover', handleDragOver);
        item.addEventListener('dragenter', handleDragEnter);
        item.addEventListener('dragleave', handleDragLeave);
        item.addEventListener('drop', handleDrop);
    });
}

function handleDragStart(e) {
    draggedItem = this;
    this.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', this.innerHTML);
}

function handleDragEnd(e) {
    this.classList.remove('dragging');
    document.querySelectorAll('.menu-sort-item').forEach(item => {
        item.classList.remove('drag-over');
    });
    draggedItem = null;
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return false;
}

function handleDragEnter(e) {
    if (this !== draggedItem) {
        this.classList.add('drag-over');
    }
}

function handleDragLeave(e) {
    this.classList.remove('drag-over');
}

function handleDrop(e) {
    e.stopPropagation();
    e.preventDefault();

    if (draggedItem !== this) {
        const container = document.getElementById('menuManagementList');
        const items = Array.from(container.querySelectorAll('.menu-sort-item'));
        const draggedIndex = items.indexOf(draggedItem);
        const targetIndex = items.indexOf(this);

        if (draggedIndex < targetIndex) {
            this.parentNode.insertBefore(draggedItem, this.nextSibling);
        } else {
            this.parentNode.insertBefore(draggedItem, this);
        }
    }

    this.classList.remove('drag-over');
    return false;
}

// 获取当前菜单顺序
function getCurrentMenuOrder() {
    const container = document.getElementById('menuManagementList');
    if (!container) return [];

    const items = container.querySelectorAll('.menu-sort-item');
    return Array.from(items).map(item => item.dataset.menuId);
}

// 保存菜单设置（包括顺序和显示/隐藏）
async function saveMenuSettings() {
    // 获取显示/隐藏设置
    const visibility = {};
    DEFAULT_MENU_ITEMS.forEach(item => {
        if (!item.required) {
            const checkbox = document.getElementById(`menu-${item.id}`);
            if (checkbox) {
                visibility[item.id] = checkbox.checked;
            }
        }
    });

    // 获取顺序
    const order = getCurrentMenuOrder();

    try {
        // 保存显示设置
        await fetch(`${apiBase}/user-settings/menu_visibility`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify(visibility),
                description: '菜单显示设置'
            })
        });

        // 保存顺序设置
        await fetch(`${apiBase}/user-settings/menu_order`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify(order),
                description: '菜单顺序设置'
            })
        });

        menuSettings = visibility;
        menuOrder = order;
        applyMenuSettings();
        showToast('菜单设置保存成功', 'success');
    } catch (error) {
        console.error('保存菜单设置失败:', error);
        showToast('保存菜单设置失败', 'danger');
    }
}

// 重置菜单设置
async function resetMenuSettings() {
    try {
        // 重置显示设置
        await fetch(`${apiBase}/user-settings/menu_visibility`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify({}),
                description: '菜单显示设置'
            })
        });

        // 重置顺序设置
        await fetch(`${apiBase}/user-settings/menu_order`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify([]),
                description: '菜单顺序设置'
            })
        });

        menuSettings = {};
        menuOrder = [];

        // 重新初始化UI
        initMenuManagement();
        applyMenuSettings();
        showToast('菜单设置已恢复默认', 'success');
    } catch (error) {
        console.error('重置菜单设置失败:', error);
        showToast('重置菜单设置失败', 'danger');
    }
}

// 应用菜单设置（顺序和显示/隐藏）
function applyMenuSettings() {
    const sidebar = document.querySelector('.sidebar-nav');
    if (!sidebar) return;

    const sortedItems = getSortedMenuItems();

    // 按顺序重新排列侧边栏菜单（普通菜单项使用 0-99）
    sortedItems.forEach((item, index) => {
        const menuItem = sidebar.querySelector(`.nav-item[data-menu-id="${item.id}"]`);
        if (menuItem) {
            // 设置显示/隐藏
            if (!item.required) {
                const isVisible = menuSettings[item.id] !== false;
                menuItem.style.display = isVisible ? '' : 'none';
            }

            // 设置顺序（通过CSS order属性）
            menuItem.style.order = index;
        }
    });

    // 确保管理员菜单区块在普通菜单之后（order: 100）
    const adminSection = document.getElementById('adminMenuSection');
    if (adminSection) {
        adminSection.style.order = 100;
    }

    // 底部分隔符和登出按钮在最后（order: 200+）
    const dividers = sidebar.querySelectorAll('.nav-divider');
    dividers.forEach((divider, idx) => {
        // 跳过管理员区块内的分隔符
        if (!divider.closest('#adminMenuSection')) {
            divider.style.order = 200 + idx;
        }
    });

    // 登出按钮（没有data-menu-id的nav-item）在最后
    const logoutItem = sidebar.querySelector('.nav-item:not([data-menu-id])');
    if (logoutItem) {
        logoutItem.style.order = 999;
    }
}

// 兼容旧函数名
function applyMenuVisibility() {
    applyMenuSettings();
}

// 加载菜单设置
async function loadMenuSettings() {
    const token = getAuthToken();
    if (!token) return;
    try {
        const response = await fetch(`${apiBase}/user-settings`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const settings = await response.json();

            // 加载显示设置
            if (settings.menu_visibility && settings.menu_visibility.value) {
                try {
                    menuSettings = JSON.parse(settings.menu_visibility.value);
                } catch (e) {
                    menuSettings = {};
                }
            }

            // 加载顺序设置
            if (settings.menu_order && settings.menu_order.value) {
                try {
                    menuOrder = JSON.parse(settings.menu_order.value);
                } catch (e) {
                    menuOrder = [];
                }
            }

            applyMenuSettings();
        }
    } catch (error) {
        console.error('加载菜单设置失败:', error);
    }
}

// 主题表单提交处理
document.addEventListener('DOMContentLoaded', function() {
    // 颜色选择器同步
    const themeColorPicker = document.getElementById('themeColorPicker');
    const themeColorHex = document.getElementById('themeColorHex');

    if (themeColorPicker && themeColorHex) {
        themeColorPicker.addEventListener('input', function() {
            themeColorHex.value = this.value;
            applyThemeColor(this.value);
            updatePresetSelection(this.value);
        });

        themeColorHex.addEventListener('input', function() {
            if (/^#[0-9A-Fa-f]{6}$/.test(this.value)) {
                themeColorPicker.value = this.value;
                applyThemeColor(this.value);
                updatePresetSelection(this.value);
            }
        });
    }

    // 预设颜色按钮点击
    document.querySelectorAll('.color-preset').forEach(btn => {
        btn.addEventListener('click', function() {
            const color = this.dataset.color;
            if (themeColorPicker) themeColorPicker.value = color;
            if (themeColorHex) themeColorHex.value = color;
            applyThemeColor(color);
            updatePresetSelection(color);
        });
    });

    const themeForm = document.getElementById('themeForm');
    if (themeForm) {
        themeForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            const themeColor = document.getElementById('themeColorHex')?.value || '#4f46e5';

            try {
                await fetch(`${apiBase}/user-settings/theme_color`, {
                    method: 'PUT',
                    headers: {
                        'Authorization': `Bearer ${authToken}`,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        value: themeColor,
                        description: '主题颜色'
                    })
                });

                applyThemeColor(themeColor);
                showToast('主题设置保存成功', 'success');
            } catch (error) {
                console.error('主题设置失败:', error);
                showToast('主题设置失败', 'danger');
            }
        });
    }

    // 密码表单提交处理
    const passwordForm = document.getElementById('passwordForm');
    if (passwordForm) {
    passwordForm.addEventListener('submit', async function(e) {
        e.preventDefault();

        const currentPassword = document.getElementById('currentPassword').value;
        const newPassword = document.getElementById('newPassword').value;
        const confirmPassword = document.getElementById('confirmPassword').value;

        if (newPassword !== confirmPassword) {
        showToast('新密码和确认密码不匹配', 'warning');
        return;
        }

        if (newPassword.length < 6) {
        showToast('新密码长度至少6位', 'warning');
        return;
        }

        try {
        const response = await fetch(`${apiBase}/change-admin-password`, {
            method: 'POST',
            headers: {
            'Authorization': `Bearer ${authToken}`,
            'Content-Type': 'application/json'
            },
            body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword
            })
        });

        if (response.ok) {
            const result = await response.json();
            if (result.success) {
            showToast('密码更新成功，请重新登录', 'success');
            passwordForm.reset();
            // 3秒后跳转到登录页面
            setTimeout(() => {
                localStorage.removeItem('auth_token');
                window.location.href = '/login.html';
            }, 3000);
            } else {
            showToast(`密码更新失败: ${result.message}`, 'danger');
            }
        } else {
            const error = await response.text();
            showToast(`密码更新失败: ${error}`, 'danger');
        }
        } catch (error) {
        console.error('密码更新失败:', error);
        showToast('密码更新失败', 'danger');
        }
    });
    }

    // 页面加载时加载用户设置（仅在已登录时）
    if (authToken) {
        loadUserSettings();
    }
});

// ==================== 备份管理功能 ====================

// 下载数据库备份
async function downloadDatabaseBackup() {
    try {
    showToast('正在准备数据库备份，请稍候...', 'info');

    const response = await fetch(`${apiBase}/admin/backup/download`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        // 获取文件名
        const contentDisposition = response.headers.get('content-disposition');
        let filename = 'xianyu_backup.db';
        if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="(.+)"/);
        if (filenameMatch) {
            filename = filenameMatch[1];
        }
        }

        // 下载文件
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showToast('数据库备份下载成功', 'success');
    } else {
        const error = await response.text();
        showToast(`下载失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('下载数据库备份失败:', error);
    showToast('下载数据库备份失败', 'danger');
    }
}

// 上传数据库备份
async function uploadDatabaseBackup() {
    const fileInput = document.getElementById('databaseFile');
    const file = fileInput.files[0];

    if (!file) {
    showToast('请选择数据库文件', 'warning');
    return;
    }

    if (!file.name.endsWith('.db')) {
    showToast('只支持.db格式的数据库文件', 'warning');
    return;
    }

    // 文件大小检查（限制100MB）
    if (file.size > 100 * 1024 * 1024) {
    showToast('数据库文件大小不能超过100MB', 'warning');
    return;
    }

    if (!confirm('恢复数据库将完全替换当前所有数据，包括所有用户、Cookie、卡券等信息。\n\n此操作不可撤销！\n\n确定要继续吗？')) {
    return;
    }

    try {
    showToast('正在上传并恢复数据库，请稍候...', 'info');

    const formData = new FormData();
    formData.append('backup_file', file);

    const response = await fetch(`${apiBase}/admin/backup/upload`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        },
        body: formData
    });

    if (response.ok) {
        const result = await response.json();
        showToast(`数据库恢复成功！包含 ${result.user_count} 个用户`, 'success');

        // 清空文件选择
        fileInput.value = '';

        // 提示用户刷新页面
        setTimeout(() => {
        if (confirm('数据库已恢复，建议刷新页面以加载新数据。是否立即刷新？')) {
            window.location.reload();
        }
        }, 2000);

    } else {
        const error = await response.json();
        showToast(`恢复失败: ${error.detail}`, 'danger');
    }
    } catch (error) {
    console.error('上传数据库备份失败:', error);
    showToast('上传数据库备份失败', 'danger');
    }
}

// 导出备份（JSON格式，兼容旧版本）
async function exportBackup() {
    try {
    showToast('正在导出备份，请稍候...', 'info');

    const response = await fetch(`${apiBase}/backup/export`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const backupData = await response.json();

        // 生成文件名
        const now = new Date();
        const timestamp = now.getFullYear() +
                        String(now.getMonth() + 1).padStart(2, '0') +
                        String(now.getDate()).padStart(2, '0') + '_' +
                        String(now.getHours()).padStart(2, '0') +
                        String(now.getMinutes()).padStart(2, '0') +
                        String(now.getSeconds()).padStart(2, '0');
        const filename = `xianyu_backup_${timestamp}.json`;

        // 创建下载链接
        const blob = new Blob([JSON.stringify(backupData, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showToast('备份导出成功', 'success');
    } else {
        const error = await response.text();
        showToast(`导出失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('导出备份失败:', error);
    showToast('导出备份失败', 'danger');
    }
}

// 导入备份
async function importBackup() {
    const fileInput = document.getElementById('backupFile');
    const file = fileInput.files[0];

    if (!file) {
    showToast('请选择备份文件', 'warning');
    return;
    }

    if (!file.name.endsWith('.json')) {
    showToast('只支持JSON格式的备份文件', 'warning');
    return;
    }

    if (!confirm('导入备份将覆盖当前所有数据，确定要继续吗？')) {
    return;
    }

    try {
    showToast('正在导入备份，请稍候...', 'info');

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${apiBase}/backup/import`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        },
        body: formData
    });

    if (response.ok) {
        showToast('备份导入成功！正在刷新数据...', 'success');

        // 清空文件选择
        fileInput.value = '';

        // 清除前端缓存
        clearKeywordCache();

        // 延迟一下再刷新数据，确保后端缓存已更新
        setTimeout(async () => {
        try {
            // 如果当前在关键字管理页面，重新加载数据
            if (currentCookieId) {
            await loadAccountKeywords();
            }

            // 刷新仪表盘数据
            if (document.getElementById('dashboard-section').classList.contains('active')) {
            await loadDashboard();
            }

            // 刷新账号列表
            if (document.getElementById('accounts-section').classList.contains('active')) {
            await loadCookies();
            }

            showToast('数据刷新完成！', 'success');
        } catch (error) {
            console.error('刷新数据失败:', error);
            showToast('备份导入成功，但数据刷新失败，请手动刷新页面', 'warning');
        }
        }, 1000);
    } else {
        const error = await response.text();
        showToast(`导入失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('导入备份失败:', error);
    showToast('导入备份失败', 'danger');
    }
}

// 刷新系统缓存
async function reloadSystemCache() {
    try {
    showToast('正在刷新系统缓存...', 'info');

    const response = await fetch(`${apiBase}/system/reload-cache`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const result = await response.json();
        showToast('系统缓存刷新成功！关键字等数据已更新', 'success');

        // 清除前端缓存
        clearKeywordCache();

        // 如果当前在关键字管理页面，重新加载数据
        if (currentCookieId) {
        setTimeout(() => {
            loadAccountKeywords();
        }, 500);
        }
    } else {
        const error = await response.text();
        showToast(`刷新缓存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('刷新系统缓存失败:', error);
    showToast('刷新系统缓存失败', 'danger');
    }
}

// 重启系统 - 显示确认对话框
function restartSystem() {
    // 使用 Bootstrap 模态框进行二次确认
    const modalHtml = `
        <div class="modal fade" id="restartConfirmModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header bg-danger text-white">
                        <h5 class="modal-title">
                            <i class="bi bi-exclamation-triangle me-2"></i>确认重启系统
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p class="mb-2"><strong>确定要重启系统吗？</strong></p>
                        <p class="text-muted mb-0">重启期间系统将暂时不可用，所有账号任务将重新启动。</p>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-danger" onclick="doRestartSystem()">
                            <i class="bi bi-power me-1"></i>确认重启
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // 移除已存在的模态框
    const existingModal = document.getElementById('restartConfirmModal');
    if (existingModal) {
        existingModal.remove();
    }

    // 添加模态框到页面
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('restartConfirmModal'));
    modal.show();
}

// 执行重启系统
async function doRestartSystem() {
    // 关闭确认模态框
    const confirmModal = bootstrap.Modal.getInstance(document.getElementById('restartConfirmModal'));
    if (confirmModal) {
        confirmModal.hide();
    }

    try {
        showToast('正在重启系统...', 'info');

        const response = await fetch('/api/update/restart', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            showToast('系统正在重启，请稍候刷新页面...', 'success');

            // 5秒后自动刷新页面
            setTimeout(() => {
                window.location.reload();
            }, 5000);
        } else {
            const error = await response.json();
            showToast(`重启失败: ${error.detail || error.message || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('重启系统失败:', error);
        showToast('重启系统失败，请检查网络连接', 'danger');
    }
}

// ================================
// 【商品发布菜单】相关功能
// ================================

async function loadItemPublish() {
    ensureItemPublishPageInitialized();
    handlePublishDeliveryChoiceChange();
    await Promise.all([
        loadItemPublishAccounts(),
        loadItemPublishMaterials(),
        loadItemPublishLogs()
    ]);
}

function ensureItemPublishPageInitialized() {
    if (itemPublishInitialized) {
        return;
    }

    const form = document.getElementById('itemPublishForm');
    if (form) {
        form.addEventListener('reset', () => {
            window.setTimeout(() => clearItemPublishForm(true), 0);
        });
    }

    itemPublishInitialized = true;
}

async function loadItemPublishAccounts() {
    const select = document.getElementById('publishCookieId');
    if (!select) {
        return;
    }

    const currentValue = select.value;

    try {
        const response = await fetch(`${apiBase}/cookies/details`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const accounts = await response.json();
        const availableAccounts = accounts.filter(account => account.has_cookie_value !== false && account.enabled !== false);

        select.innerHTML = '<option value="">请选择账号</option>';

        if (availableAccounts.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.disabled = true;
            option.textContent = '暂无可用账号';
            select.appendChild(option);
            return;
        }

        availableAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;
            option.textContent = buildItemPublishAccountLabel(account);
            select.appendChild(option);
        });

        if (currentValue && availableAccounts.some(account => account.id === currentValue)) {
            select.value = currentValue;
        } else if (availableAccounts.length === 1) {
            select.value = availableAccounts[0].id;
        }
    } catch (error) {
        console.error('加载发布账号失败:', error);
        select.innerHTML = '<option value="">加载账号失败</option>';
        showToast('加载发布账号失败', 'danger');
    }
}

function buildItemPublishAccountLabel(account) {
    const remark = String(account.remark || '').trim();
    const username = String(account.username || '').trim();
    if (remark) {
        return `${account.id} · ${remark}`;
    }
    if (username) {
        return `${account.id} · ${username}`;
    }
    return account.id;
}

function handlePublishDeliveryChoiceChange() {
    const choice = document.getElementById('publishDeliveryChoice')?.value || '包邮';
    const postPriceWrap = document.getElementById('publishPostPriceWrap');
    const postPriceInput = document.getElementById('publishPostPrice');
    const shouldShowPostPrice = choice === '一口价';

    if (postPriceWrap) {
        postPriceWrap.style.display = shouldShowPostPrice ? '' : 'none';
    }
    if (postPriceInput) {
        postPriceInput.required = shouldShowPostPrice;
        if (!shouldShowPostPrice) {
            postPriceInput.value = '';
        }
    }
}

function handlePublishImagesChange() {
    const input = document.getElementById('publishImages');
    if (!input) {
        return;
    }

    const files = Array.from(input.files || []);
    if (files.length > 0) {
        itemPublishLoadedMaterialImages = [];
    }
    updateItemPublishMaterialModeBadge();
    if (files.length > 9) {
        showToast('单次最多上传 9 张图片', 'warning');
        input.value = '';
        clearItemPublishImagePreviews();
        return;
    }

    renderItemPublishImagePreviews(files);
}

function renderItemPublishImagePreviews(files) {
    const previewContainer = document.getElementById('publishImagePreviewList');
    const summary = document.getElementById('publishImageSummary');

    clearItemPublishImagePreviews();

    if (!previewContainer) {
        return;
    }

    if (!files || files.length === 0) {
        previewContainer.innerHTML = '<div class="item-publish-preview-empty">尚未选择图片</div>';
        if (summary) {
            summary.textContent = '请上传 1-9 张图片，建议首图清晰展示商品主体。';
        }
        return;
    }

    const totalSize = files.reduce((sum, file) => sum + (file.size || 0), 0);
    previewContainer.innerHTML = files.map((file, index) => {
        const objectUrl = URL.createObjectURL(file);
        itemPublishPreviewUrls.push(objectUrl);
        return `
            <div class="item-publish-preview-card">
                <img src="${objectUrl}" alt="预览图 ${index + 1}">
                <div class="item-publish-preview-meta">
                    <div class="item-publish-preview-name" title="${escapeHtml(file.name || `图片 ${index + 1}`)}">${escapeHtml(file.name || `图片 ${index + 1}`)}</div>
                    <div class="item-publish-preview-size">${formatFileSize(file.size || 0)}</div>
                </div>
            </div>
        `;
    }).join('');

    if (summary) {
        summary.textContent = `已选择 ${files.length} 张图片，总大小 ${formatFileSize(totalSize)}。`;
    }
}

function clearItemPublishImagePreviews() {
    itemPublishPreviewUrls.forEach(url => URL.revokeObjectURL(url));
    itemPublishPreviewUrls = [];

    const previewContainer = document.getElementById('publishImagePreviewList');
    const summary = document.getElementById('publishImageSummary');
    if (previewContainer) {
        previewContainer.innerHTML = '<div class="item-publish-preview-empty">尚未选择图片</div>';
    }
    if (summary) {
        summary.textContent = '请上传 1-9 张图片，建议首图清晰展示商品主体。';
    }
}

function clearItemPublishForm(clearResult = true) {
    clearItemPublishImagePreviews();
    itemPublishLoadedMaterialId = null;
    itemPublishLoadedMaterialImages = [];
    updateItemPublishMaterialModeBadge();
    handlePublishDeliveryChoiceChange();

    const imagesInput = document.getElementById('publishImages');
    if (imagesInput) {
        imagesInput.value = '';
    }

    if (clearResult) {
        hideItemPublishResult();
    }
}

function hideItemPublishResult() {
    const panel = document.getElementById('publishResultPanel');
    const meta = document.getElementById('publishResultMeta');
    if (panel) {
        panel.style.display = 'none';
    }
    if (meta) {
        meta.innerHTML = '';
    }
}

function renderItemPublishResult(data, isSuccess) {
    const panel = document.getElementById('publishResultPanel');
    const badge = document.getElementById('publishResultBadge');
    const title = document.getElementById('publishResultTitle');
    const message = document.getElementById('publishResultMessage');
    const meta = document.getElementById('publishResultMeta');

    if (!panel || !badge || !title || !message || !meta) {
        return;
    }

    panel.style.display = '';
    badge.className = `badge ${isSuccess ? 'text-bg-success' : 'text-bg-danger'}`;
    badge.textContent = isSuccess ? '成功' : '失败';
    title.textContent = isSuccess ? '商品发布完成' : '商品发布失败';
    message.textContent = data.message || (isSuccess ? '商品发布成功' : '商品发布失败');

    const metaRows = [];
    if (data.published_item_id) {
        metaRows.push({ label: '商品ID', value: data.published_item_id });
    }
    if (data.item_url) {
        metaRows.push({ label: '商品链接', value: data.item_url });
    }
    if (data.log_id) {
        metaRows.push({ label: '发布日志', value: `#${data.log_id}` });
    }

    const syncResult = data.sync_result || {};
    if (syncResult.message) {
        metaRows.push({ label: '同步结果', value: syncResult.message });
    }

    const pageSync = syncResult.page_sync || {};
    if (pageSync.current_count || pageSync.saved_count) {
        metaRows.push({
            label: '最近页同步',
            value: `获取 ${pageSync.current_count || 0} 个商品，写入 ${pageSync.saved_count || 0} 个`
        });
    }

    const fullSync = syncResult.full_sync || {};
    if (fullSync.used) {
        metaRows.push({
            label: '补充同步',
            value: fullSync.success
                ? `全量扫描 ${fullSync.total_count || 0} 个商品，写入 ${fullSync.total_saved || 0} 个`
                : (fullSync.error || '补充同步失败')
        });
    }

    if (!isSuccess && data.detail) {
        metaRows.push({ label: '错误详情', value: data.detail });
    }

    if (metaRows.length === 0) {
        meta.innerHTML = '<div class="text-muted small">当前没有更多结果详情。</div>';
        return;
    }

    meta.innerHTML = metaRows.map(row => `
        <div class="item-publish-result-row">
            <span class="item-publish-result-label">${escapeHtml(row.label)}</span>
            <span class="item-publish-result-value">${escapeHtml(String(row.value || ''))}</span>
        </div>
    `).join('');
}

async function requestItemPublishJson(path, options = {}) {
    const response = await fetch(`${apiBase}${path}`, {
        ...options,
        headers: {
            'Authorization': `Bearer ${authToken}`,
            ...(options.headers || {})
        }
    });
    const responseText = await response.text();
    let responseData = {};
    try {
        responseData = responseText ? JSON.parse(responseText) : {};
    } catch (parseError) {
        responseData = { detail: responseText || `HTTP ${response.status}` };
    }
    if (!response.ok) {
        throw new Error(responseData.detail || responseData.message || `HTTP ${response.status}`);
    }
    return responseData;
}

function parseOptionalPublishNumber(value, label) {
    const text = String(value ?? '').trim();
    if (!text) {
        return null;
    }
    const number = Number(text);
    if (!Number.isFinite(number) || number < 0) {
        throw new Error(`${label}必须是大于等于 0 的数字`);
    }
    return number;
}

function getItemPublishFormValues() {
    return {
        accountId: document.getElementById('publishCookieId')?.value || '',
        title: document.getElementById('publishTitle')?.value.trim() || '',
        category: document.getElementById('publishCategory')?.value.trim() || '',
        description: document.getElementById('publishDescription')?.value.trim() || '',
        currentPrice: document.getElementById('publishCurrentPrice')?.value.trim() || '',
        originalPrice: document.getElementById('publishOriginalPrice')?.value.trim() || '',
        deliveryChoice: document.getElementById('publishDeliveryChoice')?.value || '包邮',
        postPrice: document.getElementById('publishPostPrice')?.value.trim() || '',
        canSelfPickup: document.getElementById('publishCanSelfPickup')?.checked || false,
        files: Array.from(document.getElementById('publishImages')?.files || [])
    };
}

function validateItemPublishValues(values, { requireAccount = true, requireImages = true } = {}) {
    if (requireAccount && !values.accountId) {
        throw new Error('请选择发布账号');
    }
    if (!values.title) {
        throw new Error('请输入商品标题');
    }
    if (!values.description) {
        throw new Error('请输入商品描述');
    }
    if (values.files.length > 9) {
        throw new Error('单次最多上传 9 张图片');
    }
    if (values.originalPrice && !values.currentPrice) {
        throw new Error('填写原价时必须同时填写现价');
    }
    if (values.deliveryChoice === '一口价' && !values.postPrice) {
        throw new Error('运费方式为一口价时必须填写邮费');
    }
    parseOptionalPublishNumber(values.currentPrice, '现价');
    parseOptionalPublishNumber(values.originalPrice, '原价');
    parseOptionalPublishNumber(values.postPrice, '邮费');

    const imageCount = values.files.length || itemPublishLoadedMaterialImages.length;
    if (requireImages && imageCount === 0) {
        throw new Error('请至少上传 1 张商品图片或载入素材图片');
    }
}

function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ''));
        reader.onerror = () => reject(new Error(`读取图片失败: ${file.name || '未知图片'}`));
        reader.readAsDataURL(file);
    });
}

async function convertPublishFilesToImages(files) {
    const images = [];
    for (const [index, file] of files.entries()) {
        if (file.type && !file.type.startsWith('image/')) {
            throw new Error(`第 ${index + 1} 张文件不是图片`);
        }
        images.push({
            filename: file.name || `publish-image-${index + 1}.jpg`,
            data: await fileToDataUrl(file),
            size: file.size || 0,
            type: file.type || 'image/jpeg'
        });
    }
    return images;
}

function buildItemPublishJsonPayload(values, images) {
    return {
        account_id: values.accountId,
        title: values.title,
        description: values.description,
        category: values.category,
        price: parseOptionalPublishNumber(values.currentPrice, '现价'),
        original_price: parseOptionalPublishNumber(values.originalPrice, '原价'),
        images,
        delivery_method: values.deliveryChoice,
        postage: parseOptionalPublishNumber(values.postPrice, '邮费'),
        can_self_pickup: values.canSelfPickup,
        condition: '全新'
    };
}

function buildItemPublishMaterialPayload(values, images) {
    const payload = buildItemPublishJsonPayload({ ...values, accountId: values.accountId || 'material' }, images);
    delete payload.account_id;
    return payload;
}

function updateItemPublishMaterialModeBadge() {
    const badge = document.getElementById('publishMaterialModeBadge');
    if (!badge) {
        return;
    }
    if (itemPublishLoadedMaterialId) {
        badge.className = 'badge text-bg-info';
        badge.textContent = `编辑素材 #${itemPublishLoadedMaterialId}`;
    } else {
        badge.className = 'badge text-bg-light border';
        badge.textContent = '新建素材';
    }
}

function getItemPublishImageSrc(image) {
    const raw = String(image?.url || image?.image_url || image?.src || image?.data || image?.base64 || '').trim();
    if (!raw) {
        return '';
    }
    if (raw.startsWith('data:') || raw.startsWith('http://') || raw.startsWith('https://') || raw.startsWith('/')) {
        return raw;
    }
    return `data:image/jpeg;base64,${raw}`;
}

function renderItemPublishStoredImagePreviews(images) {
    const previewContainer = document.getElementById('publishImagePreviewList');
    const summary = document.getElementById('publishImageSummary');
    clearItemPublishImagePreviews();
    if (!previewContainer) {
        return;
    }
    const safeImages = Array.isArray(images) ? images : [];
    if (safeImages.length === 0) {
        return;
    }
    previewContainer.innerHTML = safeImages.map((image, index) => {
        const src = getItemPublishImageSrc(image);
        const name = image?.filename || image?.name || `素材图片 ${index + 1}`;
        return `
            <div class="item-publish-preview-card">
                <img src="${escapeHtml(src)}" alt="${escapeHtml(name)}">
                <div class="item-publish-preview-meta">
                    <div class="item-publish-preview-name" title="${escapeHtml(name)}">${escapeHtml(name)}</div>
                    <div class="item-publish-preview-size">素材图片</div>
                </div>
            </div>
        `;
    }).join('');
    if (summary) {
        summary.textContent = `已载入素材图片 ${safeImages.length} 张；如重新选择文件，将替换素材图片。`;
    }
}

function startNewItemPublishMaterial() {
    itemPublishLoadedMaterialId = null;
    itemPublishLoadedMaterialImages = [];
    const form = document.getElementById('itemPublishForm');
    if (form) {
        form.reset();
    }
    clearItemPublishForm(false);
    updateItemPublishMaterialModeBadge();
}

async function saveItemPublishMaterial() {
    if (itemPublishSavingMaterial) {
        return;
    }
    const button = document.getElementById('itemPublishSaveMaterialBtn');
    const originalHtml = button?.innerHTML || '';

    try {
        const values = getItemPublishFormValues();
        validateItemPublishValues(values, { requireAccount: false, requireImages: true });
        const images = values.files.length > 0
            ? await convertPublishFilesToImages(values.files)
            : [...itemPublishLoadedMaterialImages];
        if (images.length === 0) {
            throw new Error('请至少上传 1 张商品图片或载入素材图片');
        }

        itemPublishSavingMaterial = true;
        if (button) {
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>保存中...';
        }

        const payload = buildItemPublishMaterialPayload(values, images);
        const isEdit = Boolean(itemPublishLoadedMaterialId);
        const result = await requestItemPublishJson(
            isEdit ? `/product-materials/${encodeURIComponent(itemPublishLoadedMaterialId)}` : '/product-materials',
            {
                method: isEdit ? 'PUT' : 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }
        );
        const material = result.material || {};
        itemPublishLoadedMaterialId = material.id || itemPublishLoadedMaterialId;
        itemPublishLoadedMaterialImages = Array.isArray(material.images) ? material.images : images;
        const imageInput = document.getElementById('publishImages');
        if (imageInput) {
            imageInput.value = '';
        }
        renderItemPublishStoredImagePreviews(itemPublishLoadedMaterialImages);
        updateItemPublishMaterialModeBadge();
        showToast(result.message || (isEdit ? '商品素材更新成功' : '商品素材保存成功'), 'success');
        await loadItemPublishMaterials();
    } catch (error) {
        console.error('保存商品素材失败:', error);
        showToast(error.message || '保存商品素材失败', 'danger');
    } finally {
        itemPublishSavingMaterial = false;
        if (button) {
            button.disabled = false;
            button.innerHTML = originalHtml || '<i class="bi bi-save me-1"></i>保存素材';
        }
    }
}

async function loadItemPublishMaterials() {
    const container = document.getElementById('publishMaterialList');
    if (!container) {
        return;
    }
    container.innerHTML = '<div class="text-muted small">正在加载素材...</div>';
    try {
        const data = await requestItemPublishJson('/product-materials?page=1&page_size=20');
        itemPublishMaterials = data.list || [];
        renderItemPublishMaterials();
    } catch (error) {
        console.error('加载商品素材失败:', error);
        container.innerHTML = '<div class="item-publish-preview-empty">加载素材失败</div>';
    }
}

function renderItemPublishMaterials() {
    const container = document.getElementById('publishMaterialList');
    if (!container) {
        return;
    }
    if (!itemPublishMaterials.length) {
        container.innerHTML = '<div class="item-publish-preview-empty">暂无素材，填写表单后可点击“保存素材”。</div>';
        return;
    }

    container.innerHTML = itemPublishMaterials.map(material => {
        const image = Array.isArray(material.images) && material.images.length ? material.images[0] : null;
        const imageSrc = getItemPublishImageSrc(image);
        const priceText = material.price !== null && material.price !== undefined ? `¥${material.price}` : '默认价';
        const categoryText = material.category ? ` · ${material.category}` : '';
        const imageCount = Array.isArray(material.images) ? material.images.length : 0;
        return `
            <div class="item-publish-side-item ${itemPublishLoadedMaterialId === material.id ? 'is-active' : ''}">
                ${imageSrc ? `<img class="item-publish-side-thumb" src="${escapeHtml(imageSrc)}" alt="素材图">` : '<div class="item-publish-side-thumb is-empty"><i class="bi bi-image"></i></div>'}
                <div class="item-publish-side-main">
                    <div class="item-publish-side-title" title="${escapeHtml(material.title || '')}">${escapeHtml(material.title || '未命名素材')}</div>
                    <div class="item-publish-side-meta">${escapeHtml(priceText)} · ${imageCount} 张图${escapeHtml(categoryText)}</div>
                    <div class="item-publish-side-actions">
                        <button type="button" class="btn btn-sm btn-outline-primary" onclick="loadItemPublishMaterialToForm(${material.id})">载入</button>
                        <button type="button" class="btn btn-sm btn-outline-danger" onclick="deleteItemPublishMaterial(${material.id})">删除</button>
                    </div>
                </div>
            </div>
        `;
    }).join('');
}

function loadItemPublishMaterialToForm(materialId) {
    const material = itemPublishMaterials.find(item => Number(item.id) === Number(materialId));
    if (!material) {
        showToast('未找到商品素材，请刷新后重试', 'warning');
        return;
    }

    document.getElementById('publishTitle').value = material.title || '';
    document.getElementById('publishCategory').value = material.category || '';
    document.getElementById('publishDescription').value = material.description || '';
    document.getElementById('publishCurrentPrice').value = material.price ?? '';
    document.getElementById('publishOriginalPrice').value = material.original_price ?? '';
    document.getElementById('publishDeliveryChoice').value = material.delivery_method || '包邮';
    document.getElementById('publishPostPrice').value = material.postage ?? '';
    document.getElementById('publishCanSelfPickup').checked = Boolean(material.can_self_pickup);
    const imageInput = document.getElementById('publishImages');
    if (imageInput) {
        imageInput.value = '';
    }

    itemPublishLoadedMaterialId = material.id;
    itemPublishLoadedMaterialImages = Array.isArray(material.images) ? material.images : [];
    handlePublishDeliveryChoiceChange();
    renderItemPublishStoredImagePreviews(itemPublishLoadedMaterialImages);
    updateItemPublishMaterialModeBadge();
    renderItemPublishMaterials();
    showToast('已载入商品素材，可直接发布或继续编辑', 'info');
}

async function deleteItemPublishMaterial(materialId) {
    if (!confirm('确定删除该商品素材吗？')) {
        return;
    }
    try {
        const result = await requestItemPublishJson(`/product-materials/${encodeURIComponent(materialId)}`, { method: 'DELETE' });
        if (Number(itemPublishLoadedMaterialId) === Number(materialId)) {
            startNewItemPublishMaterial();
        }
        showToast(result.message || '商品素材已删除', 'success');
        await loadItemPublishMaterials();
    } catch (error) {
        console.error('删除商品素材失败:', error);
        showToast(error.message || '删除商品素材失败', 'danger');
    }
}

function getItemPublishStatusBadge(status) {
    const statusMap = {
        success: { text: '成功', cls: 'text-bg-success' },
        failed: { text: '失败', cls: 'text-bg-danger' },
        publishing: { text: '发布中', cls: 'text-bg-primary' },
        pending: { text: '等待中', cls: 'text-bg-secondary' }
    };
    const item = statusMap[status] || { text: status || '未知', cls: 'text-bg-light text-dark border' };
    return `<span class="badge ${item.cls}">${escapeHtml(item.text)}</span>`;
}

async function loadItemPublishLogs() {
    const container = document.getElementById('publishLogList');
    if (!container) {
        return;
    }
    container.innerHTML = '<div class="text-muted small">正在加载发布记录...</div>';
    try {
        const data = await requestItemPublishJson('/publish-logs?page=1&page_size=10');
        itemPublishLogs = data.list || [];
        renderItemPublishLogs();
    } catch (error) {
        console.error('加载发布记录失败:', error);
        container.innerHTML = '<div class="item-publish-preview-empty">加载发布记录失败</div>';
    }
}

function renderItemPublishLogs() {
    const container = document.getElementById('publishLogList');
    if (!container) {
        return;
    }
    if (!itemPublishLogs.length) {
        container.innerHTML = '<div class="item-publish-preview-empty">暂无发布记录</div>';
        return;
    }

    container.innerHTML = itemPublishLogs.map(log => {
        const timeText = log.updated_at || log.created_at || '';
        const itemLink = log.item_url
            ? `<a href="${escapeHtml(log.item_url)}" target="_blank" rel="noopener">查看商品</a>`
            : (log.item_id ? `商品ID: ${escapeHtml(log.item_id)}` : '暂无商品链接');
        const detail = log.error_message || log.sync_message || '';
        return `
            <div class="item-publish-log-item">
                <div class="d-flex justify-content-between align-items-start gap-2">
                    <div class="item-publish-side-title" title="${escapeHtml(log.title || '')}">${escapeHtml(log.title || '未命名商品')}</div>
                    ${getItemPublishStatusBadge(log.status)}
                </div>
                <div class="item-publish-side-meta">账号 ${escapeHtml(log.account_id || '-')} · ${escapeHtml(timeText || '-')}</div>
                <div class="item-publish-side-meta">${itemLink}</div>
                ${detail ? `<div class="item-publish-log-detail" title="${escapeHtml(detail)}">${escapeHtml(detail)}</div>` : ''}
            </div>
        `;
    }).join('');
}

async function submitItemPublishForm() {
    if (itemPublishSubmitting) {
        return;
    }

    const values = getItemPublishFormValues();
    const submitButton = document.getElementById('itemPublishSubmitBtn');

    try {
        validateItemPublishValues(values, { requireAccount: true, requireImages: true });
    } catch (error) {
        showToast(error.message || '请完善发布信息', 'warning');
        return;
    }

    itemPublishSubmitting = true;
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>发布中...';
    }

    try {
        let responseData;
        if (values.files.length > 0) {
            const formData = new FormData();
            formData.append('cookie_id', values.accountId);
            formData.append('title', values.title);
            formData.append('category', values.category);
            formData.append('description', values.description);
            formData.append('current_price', values.currentPrice);
            formData.append('original_price', values.originalPrice);
            formData.append('delivery_choice', values.deliveryChoice);
            formData.append('post_price', values.postPrice);
            formData.append('can_self_pickup', values.canSelfPickup ? 'true' : 'false');
            values.files.forEach(file => formData.append('images', file));

            const response = await fetch(`${apiBase}/item-publish`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                },
                body: formData
            });

            const responseText = await response.text();
            try {
                responseData = responseText ? JSON.parse(responseText) : {};
            } catch (parseError) {
                responseData = { detail: responseText || `HTTP ${response.status}` };
            }

            if (!response.ok) {
                throw new Error(responseData.detail || responseData.message || `HTTP ${response.status}`);
            }
        } else {
            const payload = buildItemPublishJsonPayload(values, itemPublishLoadedMaterialImages);
            responseData = await requestItemPublishJson('/product-publish', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
        }

        renderItemPublishResult(responseData, true);
        showToast(responseData.message || '商品发布成功', 'success');
        await loadItemPublishLogs();
    } catch (error) {
        console.error('发布商品失败:', error);
        const errorMessage = error.message || '发布商品失败';
        renderItemPublishResult({ message: errorMessage, detail: errorMessage }, false);
        showToast(errorMessage, 'danger');
    } finally {
        itemPublishSubmitting = false;
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="bi bi-cloud-upload me-1"></i>发布商品';
        }
    }
}

// ================================
// 【商品管理菜单】相关功能
// ================================

// 切换商品多规格状态
async function toggleItemMultiSpec(cookieId, itemId, isMultiSpec) {
    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}/multi-spec`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        is_multi_spec: isMultiSpec
        })
    });

    if (response.ok) {
        showToast(`${isMultiSpec ? '开启' : '关闭'}多规格成功`, 'success');
        // 刷新商品列表
        await refreshItemsData();
    } else {
        const errorData = await response.json();
        throw new Error(errorData.error || '操作失败');
    }
    } catch (error) {
    console.error('切换多规格状态失败:', error);
    showToast(`切换多规格状态失败: ${error.message}`, 'danger');
    }
}

// 切换商品多数量发货状态
async function toggleItemMultiQuantityDelivery(cookieId, itemId, multiQuantityDelivery) {
    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}/multi-quantity-delivery`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        multi_quantity_delivery: multiQuantityDelivery
        })
    });

    if (response.ok) {
        showToast(`${multiQuantityDelivery ? '开启' : '关闭'}多数量发货成功`, 'success');
        // 刷新商品列表
        await refreshItemsData();
    } else {
        const errorData = await response.json();
        throw new Error(errorData.error || '操作失败');
    }
    } catch (error) {
    console.error('切换多数量发货状态失败:', error);
    showToast(`切换多数量发货状态失败: ${error.message}`, 'danger');
    }
}

// 加载商品列表
async function loadItems() {
    try {
    // 先加载Cookie列表用于筛选
    await loadCookieFilter('itemCookieFilter');

    // 加载商品列表
    await refreshItemsData();
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 只刷新商品数据，不重新加载筛选器
async function refreshItemsData() {
    try {
    const selectedCookie = document.getElementById('itemCookieFilter').value;
    if (selectedCookie) {
        await loadItemsByCookie();
    } else {
        await loadAllItems();
    }
    } catch (error) {
    console.error('刷新商品数据失败:', error);
    showToast('刷新商品数据失败', 'danger');
    }
}

// 加载Cookie筛选选项
async function loadCookieFilter(id) {
    try {
    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const accounts = await response.json();
        const select = document.getElementById(id);

        // 保存当前选择的值
        const currentValue = select.value;

        // 清空现有选项（保留"所有账号"）
        select.innerHTML = '<option value="">所有账号</option>';

        if (accounts.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '❌ 暂无账号';
        option.disabled = true;
        select.appendChild(option);
        return;
        }

        // 分组显示：先显示启用的账号，再显示禁用的账号
        const enabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return enabled;
        });
        const disabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return !enabled;
        });

        // 添加启用的账号
        enabledAccounts.forEach(account => {
        const option = document.createElement('option');
        option.value = account.id;
        option.textContent = `🟢 ${account.id}`;
        select.appendChild(option);
        });

        // 添加禁用的账号
        if (disabledAccounts.length > 0) {
        // 添加分隔线
        if (enabledAccounts.length > 0) {
            const separator = document.createElement('option');
            separator.value = '';
            separator.textContent = '────────────────';
            separator.disabled = true;
            select.appendChild(separator);
        }

        disabledAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;
            option.textContent = `🔴 ${account.id} (已禁用)`;
            select.appendChild(option);
        });
        }

        // 恢复之前选择的值
        if (currentValue) {
        select.value = currentValue;
        }
    }
    } catch (error) {
    console.error('加载Cookie列表失败:', error);
    showToast('加载账号列表失败', 'danger');
    }
}

// 加载所有商品
async function loadAllItems() {
    try {
    const response = await fetch(`${apiBase}/items`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItems(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 按Cookie加载商品
async function loadItemsByCookie() {
    const cookieId = document.getElementById('itemCookieFilter').value;

    if (!cookieId) {
    await loadAllItems();
    return;
    }

    try {
    const response = await fetch(`${apiBase}/items/cookie/${encodeURIComponent(cookieId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItems(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 显示商品列表
function displayItems(items) {
    // 存储所有商品数据
    allItemsData = items || [];

    // 应用搜索过滤
    applyItemsFilter();

    // 显示当前页数据
    displayCurrentPageItems();

    // 更新分页控件
    updateItemsPagination();
}

// 应用搜索过滤
function applyItemsFilter() {
    const searchKeyword = currentSearchKeyword.toLowerCase().trim();

    if (!searchKeyword) {
        filteredItemsData = [...allItemsData];
    } else {
        filteredItemsData = allItemsData.filter(item => {
            const title = (item.item_title || '').toLowerCase();
            const detail = getItemDetailText(item.item_detail || '').toLowerCase();
            return title.includes(searchKeyword) || detail.includes(searchKeyword);
        });
    }

    // 重置到第一页
    currentItemsPage = 1;

    // 计算总页数
    totalItemsPages = Math.ceil(filteredItemsData.length / itemsPerPage);

    // 更新搜索统计
    updateItemsSearchStats();
}

// 获取商品详情的纯文本内容
function getItemDetailText(itemDetail) {
    if (!itemDetail) return '';

    try {
        // 尝试解析JSON
        const detail = JSON.parse(itemDetail);
        if (detail.content) {
            return detail.content;
        }
        return itemDetail;
    } catch (e) {
        // 如果不是JSON格式，直接返回原文本
        return itemDetail;
    }
}

// 显示当前页的商品数据
function displayCurrentPageItems() {
    const tbody = document.getElementById('itemsTableBody');

    if (!filteredItemsData || filteredItemsData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted">暂无商品数据</td></tr>';
        resetItemsSelection();
        return;
    }

    // 计算当前页的数据范围
    const startIndex = (currentItemsPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const currentPageItems = filteredItemsData.slice(startIndex, endIndex);

    const itemsHtml = currentPageItems.map(item => {
        // 处理商品标题显示
        let itemTitleDisplay = item.item_title || '未设置';
        if (itemTitleDisplay.length > 30) {
            itemTitleDisplay = itemTitleDisplay.substring(0, 30) + '...';
        }

        // 处理商品详情显示
        let itemDetailDisplay = '未设置';
        if (item.item_detail) {
            const detailText = getItemDetailText(item.item_detail);
            itemDetailDisplay = detailText.substring(0, 50) + (detailText.length > 50 ? '...' : '');
        }

        // 多规格状态显示
        const isMultiSpec = item.is_multi_spec;
        const multiSpecDisplay = isMultiSpec ?
            '<span class="badge bg-success">多规格</span>' :
            '<span class="badge bg-secondary">普通</span>';

        // 多数量发货状态显示
        const isMultiQuantityDelivery = item.multi_quantity_delivery;
        const multiQuantityDeliveryDisplay = isMultiQuantityDelivery ?
            '<span class="badge bg-success">已开启</span>' :
            '<span class="badge bg-secondary">已关闭</span>';

        return `
            <tr>
            <td>
                <input type="checkbox" name="itemCheckbox"
                        data-cookie-id="${escapeHtml(item.cookie_id)}"
                        data-item-id="${escapeHtml(item.item_id)}"
                        onchange="updateSelectAllState()">
            </td>
            <td>${escapeHtml(item.cookie_id)}</td>
            <td>${escapeHtml(item.item_id)}</td>
            <td title="${escapeHtml(item.item_title || '未设置')}">${escapeHtml(itemTitleDisplay)}</td>
            <td title="${escapeHtml(getItemDetailText(item.item_detail || ''))}">${escapeHtml(itemDetailDisplay)}</td>
            <td>${escapeHtml(item.item_price || '未设置')}</td>
            <td>${multiSpecDisplay}</td>
            <td>${multiQuantityDeliveryDisplay}</td>
            <td>${formatDateTime(item.updated_at)}</td>
            <td>
                <div class="btn-group" role="group">
                <button class="btn btn-sm btn-outline-primary" onclick="editItem('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}')" title="编辑详情">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteItem('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', '${escapeHtml(item.item_title || item.item_id)}')" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
                <button class="btn btn-sm ${isMultiSpec ? 'btn-warning' : 'btn-success'}" onclick="toggleItemMultiSpec('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', ${!isMultiSpec})" title="${isMultiSpec ? '关闭多规格' : '开启多规格'}">
                    <i class="bi ${isMultiSpec ? 'bi-toggle-on' : 'bi-toggle-off'}"></i>
                </button>
                <button class="btn btn-sm ${isMultiQuantityDelivery ? 'btn-warning' : 'btn-success'}" onclick="toggleItemMultiQuantityDelivery('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', ${!isMultiQuantityDelivery})" title="${isMultiQuantityDelivery ? '关闭多数量发货' : '开启多数量发货'}">
                    <i class="bi ${isMultiQuantityDelivery ? 'bi-box-arrow-down' : 'bi-box-arrow-up'}"></i>
                </button>
                </div>
            </td>
            </tr>
        `;
    }).join('');

    // 更新表格内容
    tbody.innerHTML = itemsHtml;

    // 重置选择状态
    resetItemsSelection();
}

// 重置商品选择状态
function resetItemsSelection() {
    const selectAllCheckbox = document.getElementById('selectAllItems');
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
    updateBatchDeleteButton();
}

// 商品搜索过滤函数
function filterItems() {
    const searchInput = document.getElementById('itemSearchInput');
    currentSearchKeyword = searchInput ? searchInput.value : '';

    // 应用过滤
    applyItemsFilter();

    // 显示当前页数据
    displayCurrentPageItems();

    // 更新分页控件
    updateItemsPagination();
}

// 更新搜索统计信息
function updateItemsSearchStats() {
    const statsElement = document.getElementById('itemSearchStats');
    const statsTextElement = document.getElementById('itemSearchStatsText');

    if (!statsElement || !statsTextElement) return;

    if (currentSearchKeyword) {
        statsTextElement.textContent = `搜索"${currentSearchKeyword}"，找到 ${filteredItemsData.length} 个商品`;
        statsElement.style.display = 'block';
    } else {
        statsElement.style.display = 'none';
    }
}

// 更新分页控件
function updateItemsPagination() {
    const paginationElement = document.getElementById('itemsPagination');
    const pageInfoElement = document.getElementById('itemsPageInfo');
    const totalPagesElement = document.getElementById('itemsTotalPages');
    const pageInputElement = document.getElementById('itemsPageInput');

    if (!paginationElement) return;

    // 分页控件总是显示
    paginationElement.style.display = 'block';

    // 更新页面信息
    const startIndex = (currentItemsPage - 1) * itemsPerPage + 1;
    const endIndex = Math.min(currentItemsPage * itemsPerPage, filteredItemsData.length);

    if (pageInfoElement) {
        pageInfoElement.textContent = `显示第 ${startIndex}-${endIndex} 条，共 ${filteredItemsData.length} 条记录`;
    }

    if (totalPagesElement) {
        totalPagesElement.textContent = totalItemsPages;
    }

    if (pageInputElement) {
        pageInputElement.value = currentItemsPage;
        pageInputElement.max = totalItemsPages;
    }

    // 更新分页按钮状态
    updateItemsPaginationButtons();
}

// 更新分页按钮状态
function updateItemsPaginationButtons() {
    const firstPageBtn = document.getElementById('itemsFirstPage');
    const prevPageBtn = document.getElementById('itemsPrevPage');
    const nextPageBtn = document.getElementById('itemsNextPage');
    const lastPageBtn = document.getElementById('itemsLastPage');

    if (firstPageBtn) firstPageBtn.disabled = currentItemsPage <= 1;
    if (prevPageBtn) prevPageBtn.disabled = currentItemsPage <= 1;
    if (nextPageBtn) nextPageBtn.disabled = currentItemsPage >= totalItemsPages;
    if (lastPageBtn) lastPageBtn.disabled = currentItemsPage >= totalItemsPages;
}

// 跳转到指定页面
function goToItemsPage(page) {
    if (page < 1 || page > totalItemsPages) return;

    currentItemsPage = page;
    displayCurrentPageItems();
    updateItemsPagination();
}

// 处理页面输入框的回车事件
function handleItemsPageInput(event) {
    if (event.key === 'Enter') {
        const pageInput = event.target;
        const page = parseInt(pageInput.value);

        if (page >= 1 && page <= totalItemsPages) {
            goToItemsPage(page);
        } else {
            pageInput.value = currentItemsPage;
        }
    }
}

// 改变每页显示数量
function changeItemsPageSize() {
    const pageSizeSelect = document.getElementById('itemsPageSize');
    if (!pageSizeSelect) return;

    itemsPerPage = parseInt(pageSizeSelect.value);

    // 重新计算总页数
    totalItemsPages = Math.ceil(filteredItemsData.length / itemsPerPage);

    // 调整当前页码，确保不超出范围
    if (currentItemsPage > totalItemsPages) {
        currentItemsPage = Math.max(1, totalItemsPages);
    }

    // 重新显示数据
    displayCurrentPageItems();
    updateItemsPagination();
}

// 初始化商品搜索功能
let itemsSearchInitialized = false; // 标记是否已初始化
function initItemsSearch() {
    // 避免重复初始化
    if (itemsSearchInitialized) return;
    
    // 初始化分页大小
    const pageSizeSelect = document.getElementById('itemsPageSize');
    if (pageSizeSelect) {
        itemsPerPage = parseInt(pageSizeSelect.value) || 20;
        pageSizeSelect.addEventListener('change', changeItemsPageSize);
    }

    // 初始化搜索输入框事件监听器
    const searchInput = document.getElementById('itemSearchInput');
    if (searchInput) {
        // 使用防抖来避免频繁搜索
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                filterItems();
            }, 300); // 300ms 防抖延迟
        });
        
        // 标记已初始化
        itemsSearchInitialized = true;
        console.log('商品搜索功能已初始化');
    }

    // 初始化页面输入框事件监听器
    const pageInput = document.getElementById('itemsPageInput');
    if (pageInput) {
        pageInput.addEventListener('keydown', handleItemsPageInput);
    }
}

// 刷新商品列表
async function refreshItems() {
    await refreshItemsData();
    showToast('本地商品列表已刷新', 'success');
}

// 获取商品信息
async function getAllItemsFromAccount() {
    const cookieSelect = document.getElementById('itemCookieFilter');
    const selectedCookieId = cookieSelect.value;
    const pageNumber = parseInt(document.getElementById('pageNumber').value) || 1;

    if (!selectedCookieId) {
    showToast('请先选择一个账号', 'warning');
    return;
    }

    if (pageNumber < 1) {
    showToast('页码必须大于0', 'warning');
    return;
    }

    // 显示加载状态
    const button = event.target;
    const originalText = button.innerHTML;
    button.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>同步中...';
    button.disabled = true;

    try {
    const response = await fetch(`${apiBase}/items/get-by-page`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        cookie_id: selectedCookieId,
        page_number: pageNumber,
        page_size: 20
        })
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        showToast(`成功同步第${pageNumber}页 ${data.current_count} 个商品，最新详情已更新`, 'success');
        // 刷新商品列表（保持筛选器选择）
        await refreshItemsData();
        } else {
        showToast(data.message || '同步商品信息失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('同步商品信息失败:', error);
    showToast('同步商品信息失败', 'danger');
    } finally {
    // 恢复按钮状态
    button.innerHTML = originalText;
    button.disabled = false;
    }
}

// 获取所有页商品信息
async function getAllItemsFromAccountAll() {
    const cookieSelect = document.getElementById('itemCookieFilter');
    const selectedCookieId = cookieSelect.value;

    if (!selectedCookieId) {
    showToast('请先选择一个账号', 'warning');
    return;
    }

    // 显示加载状态
    const button = event.target;
    const originalText = button.innerHTML;
    button.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>同步中...';
    button.disabled = true;

    try {
    const response = await fetch(`${apiBase}/items/get-all-from-account`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        cookie_id: selectedCookieId
        })
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        const message = data.total_pages ?
            `成功同步 ${data.total_count} 个商品（共${data.total_pages}页），最新详情已更新` :
            `成功同步商品信息，最新详情已更新`;
        showToast(message, 'success');
        // 刷新商品列表（保持筛选器选择）
        await refreshItemsData();
        } else {
        showToast(data.message || '同步商品信息失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('同步商品信息失败:', error);
    showToast('同步商品信息失败', 'danger');
    } finally {
    // 恢复按钮状态
    button.innerHTML = originalText;
    button.disabled = false;
    }
}



// 编辑商品详情
async function editItem(cookieId, itemId) {
    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        const item = data.item;

        // 填充表单
        document.getElementById('editItemCookieId').value = item.cookie_id;
        document.getElementById('editItemId').value = item.item_id;
        document.getElementById('editItemCookieIdDisplay').value = item.cookie_id;
        document.getElementById('editItemIdDisplay').value = item.item_id;
        document.getElementById('editItemDetail').value = item.item_detail || '';

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('editItemModal'));
        modal.show();
    } else {
        throw new Error('获取商品详情失败');
    }
    } catch (error) {
    console.error('获取商品详情失败:', error);
    showToast('获取商品详情失败', 'danger');
    }
}

// 保存商品详情
async function saveItemDetail() {
    const cookieId = document.getElementById('editItemCookieId').value;
    const itemId = document.getElementById('editItemId').value;
    const itemDetail = document.getElementById('editItemDetail').value.trim();

    if (!itemDetail) {
    showToast('请输入商品详情', 'warning');
    return;
    }

    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        item_detail: itemDetail
        })
    });

    if (response.ok) {
        showToast('商品详情更新成功', 'success');

        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('editItemModal'));
        modal.hide();

        // 刷新列表（保持筛选器选择）
        await refreshItemsData();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新商品详情失败:', error);
    showToast('更新商品详情失败', 'danger');
    }
}

// 删除商品信息
async function deleteItem(cookieId, itemId, itemTitle) {
    try {
    // 确认删除
    const confirmed = confirm(`确定要删除商品信息吗？\n\n商品ID: ${itemId}\n商品标题: ${itemTitle || '未设置'}\n\n此操作不可撤销！`);
    if (!confirmed) {
        return;
    }

    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('商品信息删除成功', 'success');
        // 刷新列表（保持筛选器选择）
        await refreshItemsData();
    } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('删除商品信息失败:', error);
    showToast('删除商品信息失败', 'danger');
    }
}

// 批量删除商品信息
async function batchDeleteItems() {
    try {
    // 获取所有选中的复选框
    const checkboxes = document.querySelectorAll('input[name="itemCheckbox"]:checked');
    if (checkboxes.length === 0) {
        showToast('请选择要删除的商品', 'warning');
        return;
    }

    // 确认删除
    const confirmed = confirm(`确定要删除选中的 ${checkboxes.length} 个商品信息吗？\n\n此操作不可撤销！`);
    if (!confirmed) {
        return;
    }

    // 构造删除列表
    const itemsToDelete = Array.from(checkboxes).map(checkbox => {
        const row = checkbox.closest('tr');
        return {
        cookie_id: checkbox.dataset.cookieId,
        item_id: checkbox.dataset.itemId
        };
    });

    const response = await fetch(`${apiBase}/items/batch`, {
        method: 'DELETE',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ items: itemsToDelete })
    });

    if (response.ok) {
        const result = await response.json();
        showToast(`批量删除完成: 成功 ${result.success_count} 个，失败 ${result.failed_count} 个`, 'success');
        // 刷新列表（保持筛选器选择）
        await refreshItemsData();
    } else {
        const error = await response.text();
        showToast(`批量删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('批量删除商品信息失败:', error);
    showToast('批量删除商品信息失败', 'danger');
    }
}

// 全选/取消全选
function toggleSelectAll(selectAllCheckbox) {
    const checkboxes = document.querySelectorAll('input[name="itemCheckbox"]');
    checkboxes.forEach(checkbox => {
    checkbox.checked = selectAllCheckbox.checked;
    });
    updateBatchDeleteButton();
}

// 更新全选状态
function updateSelectAllState() {
    const checkboxes = document.querySelectorAll('input[name="itemCheckbox"]');
    const checkedCheckboxes = document.querySelectorAll('input[name="itemCheckbox"]:checked');
    const selectAllCheckbox = document.getElementById('selectAllItems');

    if (checkboxes.length === 0) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length === checkboxes.length) {
    selectAllCheckbox.checked = true;
    selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length > 0) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = true;
    } else {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    }

    updateBatchDeleteButton();
}

// 更新批量删除按钮状态
function updateBatchDeleteButton() {
    const checkedCheckboxes = document.querySelectorAll('input[name="itemCheckbox"]:checked');
    const batchDeleteBtn = document.getElementById('batchDeleteBtn');

    if (checkedCheckboxes.length > 0) {
    batchDeleteBtn.disabled = false;
    batchDeleteBtn.innerHTML = `<i class="bi bi-trash"></i> 批量删除 (${checkedCheckboxes.length})`;
    } else {
    batchDeleteBtn.disabled = true;
    batchDeleteBtn.innerHTML = '<i class="bi bi-trash"></i> 批量删除';
    }
}

function toggleSelectAllItemReplies(selectAllCheckbox) {
    const checkboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]');
    checkboxes.forEach(checkbox => {
        checkbox.checked = selectAllCheckbox.checked;
    });
    updateItemReplyBatchDeleteButton();
}

function updateItemReplySelectAllState() {
    const checkboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]');
    const checkedCheckboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]:checked');
    const selectAllCheckbox = document.getElementById('selectAllItemReplies');

    if (!selectAllCheckbox) return;

    if (checkboxes.length === 0) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length === checkboxes.length) {
        selectAllCheckbox.checked = true;
        selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length > 0) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = true;
    } else {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }

    updateItemReplyBatchDeleteButton();
}

function updateItemReplyBatchDeleteButton() {
    const checkedCheckboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]:checked');
    const batchDeleteBtn = document.getElementById('batchDeleteItemRepliesBtn');

    if (!batchDeleteBtn) return;

    if (checkedCheckboxes.length > 0) {
        batchDeleteBtn.disabled = false;
        batchDeleteBtn.innerHTML = `<i class="bi bi-trash"></i> 批量删除 (${checkedCheckboxes.length})`;
    } else {
        batchDeleteBtn.disabled = true;
        batchDeleteBtn.innerHTML = '<i class="bi bi-trash"></i> 批量删除';
    }
}

// 格式化日期时间
function formatDateTime(dateString) {
    const date = parseUtcDateTime(dateString);
    return date ? date.toLocaleString('zh-CN') : '未知';
}

// HTML转义函数
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ================================
// 【商品回复管理菜单】相关功能
// ================================

// 加载商品回复列表
async function loadItemsReplay() {
    try {
    // 先加载Cookie列表用于筛选
    await loadCookieFilter('itemReplayCookieFilter');
    await loadCookieFilterPlus('editReplyCookieIdSelect');
    // 加载商品列表
    await refreshItemsReplayData();
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 只刷新商品回复数据，不重新加载筛选器
async function refreshItemsReplayData() {
    try {
    const selectedCookie = document.getElementById('itemReplayCookieFilter').value;
    if (selectedCookie) {
        await loadItemsReplayByCookie();
    } else {
        await loadAllItemReplays();
    }
    } catch (error) {
    console.error('刷新商品数据失败:', error);
    showToast('刷新商品数据失败', 'danger');
    }
}

// 加载Cookie筛选选项添加弹框中使用
async function loadCookieFilterPlus(id) {
    try {
    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const accounts = await response.json();
        const select = document.getElementById(id);

        // 保存当前选择的值
        const currentValue = select.value;

        // 清空现有选项（保留"所有账号"）
        select.innerHTML = '<option value="">选择账号</option>';

        if (accounts.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '❌ 暂无账号';
        option.disabled = true;
        select.appendChild(option);
        return;
        }

        // 分组显示：先显示启用的账号，再显示禁用的账号
        const enabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return enabled;
        });
        const disabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return !enabled;
        });

        // 添加启用的账号
        enabledAccounts.forEach(account => {
        const option = document.createElement('option');
        option.value = account.id;
        option.textContent = `🟢 ${account.id}`;
        select.appendChild(option);
        });

        // 添加禁用的账号
        if (disabledAccounts.length > 0) {
        // 添加分隔线
        if (enabledAccounts.length > 0) {
            const separator = document.createElement('option');
            separator.value = '';
            separator.textContent = '────────────────';
            separator.disabled = true;
            select.appendChild(separator);
        }

        disabledAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;
            option.textContent = `🔴 ${account.id} (已禁用)`;
            select.appendChild(option);
        });
        }

        // 恢复之前选择的值
        if (currentValue) {
        select.value = currentValue;
        }
    }
    } catch (error) {
    console.error('加载Cookie列表失败:', error);
    showToast('加载账号列表失败', 'danger');
    }
}

// 刷新商品回复列表
async function refreshItemReplayS() {
    await refreshItemsReplayData();
    showToast('商品列表已刷新', 'success');
}

// 加载所有商品回复
async function loadAllItemReplays() {
    try {
    const response = await fetch(`${apiBase}/itemReplays`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItemReplays(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 按Cookie加载商品回复
async function loadItemsReplayByCookie() {
    const cookieId = document.getElementById('itemReplayCookieFilter').value;
    if (!cookieId) {
    await loadAllItemReplays();
    return;
    }

    try {
    const response = await fetch(`${apiBase}/itemReplays/cookie/${encodeURIComponent(cookieId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItemReplays(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 显示商品回复列表
function displayItemReplays(items) {
    const tbody = document.getElementById('itemReplaysTableBody');

    if (!items || items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">暂无商品数据</td></tr>';
    // 重置选择状态
    const selectAllCheckbox = document.getElementById('selectAllItemReplies');
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
    updateItemReplyBatchDeleteButton();
    return;
    }

    const itemsHtml = items.map(item => {
    // 处理商品标题显示
    let itemTitleDisplay = item.item_title || '未设置';
    if (itemTitleDisplay.length > 30) {
        itemTitleDisplay = itemTitleDisplay.substring(0, 30) + '...';
    }

    // 处理商品详情显示
    let itemDetailDisplay = '未设置';
    if (item.item_detail) {
        try {
        // 尝试解析JSON并提取有用信息
        const detail = JSON.parse(item.item_detail);
        if (detail.content) {
            itemDetailDisplay = detail.content.substring(0, 50) + (detail.content.length > 50 ? '...' : '');
        } else {
            // 如果是纯文本或其他格式，直接显示前50个字符
            itemDetailDisplay = item.item_detail.substring(0, 50) + (item.item_detail.length > 50 ? '...' : '');
        }
        } catch (e) {
        // 如果不是JSON格式，直接显示前50个字符
        itemDetailDisplay = item.item_detail.substring(0, 50) + (item.item_detail.length > 50 ? '...' : '');
        }
    }

    return `
        <tr>
         <td>
            <input type="checkbox" name="itemReplyCheckbox"
                    data-cookie-id="${escapeHtml(item.cookie_id)}"
                    data-item-id="${escapeHtml(item.item_id)}"
                    onchange="updateItemReplySelectAllState()">
        </td>
        <td>${escapeHtml(item.cookie_id)}</td>
        <td>${escapeHtml(item.item_id)}</td>
        <td title="${escapeHtml(item.item_title || '未设置')}">${escapeHtml(itemTitleDisplay)}</td>
        <td title="${escapeHtml(item.item_detail || '未设置')}">${escapeHtml(itemDetailDisplay)}</td>
        <td title="${escapeHtml(item.reply_content || '未设置')}">${escapeHtml(item.reply_content)}</td>
        <td>${formatDateTime(item.updated_at)}</td>
        <td>
            <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editItemReply('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}')" title="编辑详情">
                <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteItemReply('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', '${escapeHtml(item.item_title || item.item_id)}')" title="删除">
                <i class="bi bi-trash"></i>
            </button>
            </div>
        </td>
        </tr>
    `;
    }).join('');

    // 更新表格内容
    tbody.innerHTML = itemsHtml;

    // 重置选择状态
    const selectAllCheckbox = document.getElementById('selectAllItemReplies');
    if (selectAllCheckbox) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    }
    updateItemReplyBatchDeleteButton();
}

// 显示添加弹框
async function showItemReplayEdit(){
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('editItemReplyModal'));
    document.getElementById('editReplyCookieIdSelect').value = '';
    document.getElementById('editReplyItemIdSelect').value = '';
    document.getElementById('editReplyItemIdSelect').disabled = true
    document.getElementById('editItemReplyContent').value = '';
    document.getElementById('itemReplayTitle').textContent = '添加商品回复';
    modal.show();
}

// 当账号变化时加载对应商品
async function onCookieChangeForReply() {
  const cookieId = document.getElementById('editReplyCookieIdSelect').value;
  const itemSelect = document.getElementById('editReplyItemIdSelect');

  itemSelect.innerHTML = '<option value="">选择商品</option>';
  if (!cookieId) {
    itemSelect.disabled = true;  // 禁用选择框
    return;
  } else {
    itemSelect.disabled = false; // 启用选择框
  }

  const response = await fetch(`${apiBase}/items/cookie/${encodeURIComponent(cookieId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });
    try {
       if (response.ok) {
            const data = await response.json();
            data.items.forEach(item => {
                  const opt = document.createElement('option');
                  opt.value = item.item_id;
                  opt.textContent = `${item.item_id} - ${item.item_title || '无标题'}`;
                  itemSelect.appendChild(opt);
                });
        } else {
            throw new Error('获取商品列表失败');
        }
    }catch (error) {
        console.error('加载商品列表失败:', error);
        showToast('加载商品列表失败', 'danger');
    }
}

// 编辑商品回复
async function editItemReply(cookieId, itemId) {
  try {
    const response = await fetch(`${apiBase}/item-reply/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });
    if (response.ok) {
      const data = await response.json();
      document.getElementById('itemReplayTitle').textContent = '编辑商品回复';
      // 填充表单
      document.getElementById('editReplyCookieIdSelect').value = data.cookie_id;
      let res = await onCookieChangeForReply()
      document.getElementById('editReplyItemIdSelect').value = data.item_id;
      document.getElementById('editItemReplyContent').value = data.reply_content || '';

    } else if (response.status === 404) {
      // 如果没有记录，则填充空白内容（用于添加）
//      document.getElementById('editReplyCookieIdSelect').value = data.cookie_id;
//      document.getElementById('editReplyItemIdSelect').value = data.item_id;
//      document.getElementById('editItemReplyContent').value = data.reply_content || '';
    } else {
      throw new Error('获取商品回复失败');
    }

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('editItemReplyModal'));
    modal.show();

  } catch (error) {
    console.error('获取商品回复失败:', error);
    showToast('获取商品回复失败', 'danger');
  }
}

// 保存商品回复
async function saveItemReply() {
  const cookieId = document.getElementById('editReplyCookieIdSelect').value;
  const itemId = document.getElementById('editReplyItemIdSelect').value;
  const replyContent = document.getElementById('editItemReplyContent').value.trim();

  console.log(cookieId)
  console.log(itemId)
  console.log(replyContent)
  if (!cookieId) {
    showToast('请选择账号', 'warning');
    return;
  }

  if (!itemId) {
    showToast('请选择商品', 'warning');
    return;
  }

  if (!replyContent) {
    showToast('请输入商品回复内容', 'warning');
    return;
  }

  try {
    const response = await fetch(`${apiBase}/item-reply/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
      },
      body: JSON.stringify({
        reply_content: replyContent
      })
    });

    if (response.ok) {
      showToast('商品回复保存成功', 'success');

      // 关闭模态框
      const modal = bootstrap.Modal.getInstance(document.getElementById('editItemReplyModal'));
      modal.hide();

      // 可选：刷新数据
      await refreshItemsReplayData?.();
    } else {
      const error = await response.text();
      showToast(`保存失败: ${error}`, 'danger');
    }
  } catch (error) {
    console.error('保存商品回复失败:', error);
    showToast('保存商品回复失败', 'danger');
  }
}

// 删除商品回复
async function deleteItemReply(cookieId, itemId, itemTitle) {
  try {
    const confirmed = confirm(`确定要删除该商品的自动回复吗？\n\n商品ID: ${itemId}\n商品标题: ${itemTitle || '未设置'}\n\n此操作不可撤销！`);
    if (!confirmed) return;

    const response = await fetch(`${apiBase}/item-reply/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });

    if (response.ok) {
      showToast('商品回复删除成功', 'success');
      await loadItemsReplayByCookie?.(); // 如果你有刷新商品列表的函数
    } else {
      const error = await response.text();
      showToast(`删除失败: ${error}`, 'danger');
    }
  } catch (error) {
    console.error('删除商品回复失败:', error);
    showToast('删除商品回复失败', 'danger');
  }
}

// 批量删除商品回复
async function batchDeleteItemReplies() {
  try {
    const checkboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]:checked');
    if (checkboxes.length === 0) {
      showToast('请选择要删除回复的商品', 'warning');
      return;
    }

    const confirmed = confirm(`确定要删除选中商品的自动回复吗？\n共 ${checkboxes.length} 个商品\n\n此操作不可撤销！`);
    if (!confirmed) return;

    const itemsToDelete = Array.from(checkboxes).map(checkbox => ({
      cookie_id: checkbox.dataset.cookieId,
      item_id: checkbox.dataset.itemId
    }));

    const response = await fetch(`${apiBase}/item-reply/batch`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
      },
      body: JSON.stringify({ items: itemsToDelete })
    });

    if (response.ok) {
      const result = await response.json();
      showToast(`批量删除回复完成: 成功 ${result.success_count} 个，失败 ${result.failed_count} 个`, 'success');
      await loadItemsReplayByCookie?.();
    } else {
      const error = await response.text();
      showToast(`批量删除失败: ${error}`, 'danger');
    }
  } catch (error) {
    console.error('批量删除商品回复失败:', error);
    showToast('批量删除商品回复失败', 'danger');
  }
}

// ================================
// 【日志管理菜单】相关功能
// ================================

window.autoRefreshInterval = null;
window.allLogs = [];
window.filteredLogs = [];

// 刷新日志
async function refreshLogs() {
    try {
        const logLinesElement = document.getElementById('logLines');
        if (!logLinesElement) {
            console.warn('logLines 元素不存在');
            showToast('页面元素缺失，请刷新页面', 'warning');
            return;
        }

        const lines = logLinesElement.value;

        const response = await fetch(`${apiBase}/logs?lines=${lines}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            window.allLogs = data.logs || [];
            window.filteredLogs = window.allLogs; // 不再过滤，直接显示所有日志
            displayLogs();
            updateLogStats();
            showToast('日志已刷新', 'success');
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('刷新日志失败:', error);
        showToast(`刷新日志失败: ${error.message}`, 'danger');
    }
}



// 显示日志
function displayLogs() {
    const container = document.getElementById('logContainer');

    // 检查容器是否存在
    if (!container) {
        // 只在特定页面显示警告，避免在其他页面产生无用的警告
        const currentPath = window.location.pathname;
        if (currentPath.includes('log') || currentPath.includes('admin')) {
            console.warn('logContainer 元素不存在，无法显示日志');
        }
        return;
    }

    if (!window.filteredLogs || window.filteredLogs.length === 0) {
    container.innerHTML = `
        <div class="text-center p-4 text-muted">
        <i class="bi bi-file-text fs-1"></i>
        <p class="mt-2">暂无日志数据</p>
        </div>
    `;
    return;
    }

    const logsHtml = window.filteredLogs.map(log => {
    const timestamp = formatLogTimestamp(log.timestamp);
    const levelClass = log.level || 'INFO';

    return `
        <div class="log-entry ${levelClass}">
        <span class="log-timestamp">${timestamp}</span>
        <span class="log-level">[${log.level}]</span>
        <span class="log-source">${log.source}:</span>
        <span class="log-message">${escapeHtml(log.message)}</span>
        </div>
    `;
    }).join('');

    container.innerHTML = logsHtml;

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
}

// 格式化日志时间戳
function formatLogTimestamp(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3
    });
}

// 更新日志统计信息
function updateLogStats() {
    const logCountElement = document.getElementById('logCount');
    const lastUpdateElement = document.getElementById('lastUpdate');

    if (logCountElement) {
        const count = window.filteredLogs ? window.filteredLogs.length : 0;
        logCountElement.textContent = `${count} 条日志`;
    }

    if (lastUpdateElement) {
        lastUpdateElement.textContent = new Date().toLocaleTimeString('zh-CN');
    }
}

// 清空日志显示
function clearLogsDisplay() {
    window.allLogs = [];
    window.filteredLogs = [];
    document.getElementById('logContainer').innerHTML = `
    <div class="text-center p-4 text-muted">
        <i class="bi bi-file-text fs-1"></i>
        <p class="mt-2">日志显示已清空</p>
    </div>
    `;
    updateLogStats();
    showToast('日志显示已清空', 'info');
}

// 切换自动刷新
function toggleAutoRefresh() {
    const button = document.querySelector('#autoRefreshText');
    const icon = button.previousElementSibling;

    if (window.autoRefreshInterval) {
    // 停止自动刷新
    clearInterval(window.autoRefreshInterval);
    window.autoRefreshInterval = null;
    button.textContent = '开启自动刷新';
    icon.className = 'bi bi-play-circle me-1';
    showToast('自动刷新已停止', 'info');
    } else {
    // 开启自动刷新
    window.autoRefreshInterval = setInterval(refreshLogs, 5000); // 每5秒刷新一次
    button.textContent = '停止自动刷新';
    icon.className = 'bi bi-pause-circle me-1';
    showToast('自动刷新已开启（每5秒）', 'success');

    // 立即刷新一次
    refreshLogs();
    }
}

// 清空服务器日志
async function clearLogsServer() {
    if (!confirm('确定要清空服务器端的所有日志吗？此操作不可恢复！')) {
    return;
    }

    try {
    const response = await fetch(`${apiBase}/logs/clear`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        window.allLogs = [];
        window.filteredLogs = [];
        displayLogs();
        updateLogStats();
        showToast('服务器日志已清空', 'success');
        } else {
        showToast(data.message || '清空失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('清空服务器日志失败:', error);
    showToast('清空服务器日志失败', 'danger');
    }
}

// 显示日志统计信息
async function showLogStats() {
    try {
    const response = await fetch(`${apiBase}/logs/stats`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        const stats = data.stats;

        let statsHtml = `
            <div class="row">
            <div class="col-md-6">
                <h6>总体统计</h6>
                <ul class="list-unstyled">
                <li>总日志数: <strong>${stats.total_logs}</strong></li>
                <li>最大容量: <strong>${stats.max_capacity}</strong></li>
                <li>使用率: <strong>${((stats.total_logs / stats.max_capacity) * 100).toFixed(1)}%</strong></li>
                </ul>
            </div>
            <div class="col-md-6">
                <h6>级别分布</h6>
                <ul class="list-unstyled">
        `;

        for (const [level, count] of Object.entries(stats.level_counts || {})) {
            const percentage = ((count / stats.total_logs) * 100).toFixed(1);
            statsHtml += `<li>${level}: <strong>${count}</strong> (${percentage}%)</li>`;
        }

        statsHtml += `
                </ul>
            </div>
            </div>
            <div class="row mt-3">
            <div class="col-12">
                <h6>来源分布</h6>
                <div class="row">
        `;

        const sources = Object.entries(stats.source_counts || {});
        sources.forEach(([source, count], index) => {
            if (index % 2 === 0) statsHtml += '<div class="col-md-6"><ul class="list-unstyled">';
            const percentage = ((count / stats.total_logs) * 100).toFixed(1);
            statsHtml += `<li>${source}: <strong>${count}</strong> (${percentage}%)</li>`;
            if (index % 2 === 1 || index === sources.length - 1) statsHtml += '</ul></div>';
        });

        statsHtml += `
                </div>
            </div>
            </div>
        `;

        // 显示模态框
        const modalHtml = `
            <div class="modal fade" id="logStatsModal" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">日志统计信息</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    ${statsHtml}
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                </div>
                </div>
            </div>
            </div>
        `;

        // 移除旧的模态框
        const oldModal = document.getElementById('logStatsModal');
        if (oldModal) oldModal.remove();

        // 添加新的模态框
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('logStatsModal'));
        modal.show();

        } else {
        showToast(data.message || '获取统计信息失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('获取日志统计失败:', error);
    showToast('获取日志统计失败', 'danger');
    }
}

// ==================== 导入导出功能 ====================

// 导出关键词
async function exportKeywords() {
    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    try {
    const response = await fetch(`${apiBase}/keywords-export/${currentCookieId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        // 创建下载链接
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        // 根据当前账号是否有数据来设置文件名和提示
        const currentKeywords = keywordsData[currentCookieId] || [];
        const hasData = currentKeywords.length > 0;

        if (hasData) {
        a.download = `keywords_${currentCookieId}_${new Date().getTime()}.xlsx`;
        showToast('关键词导出成功！', 'success');
        } else {
        a.download = `keywords_template_${currentCookieId}_${new Date().getTime()}.xlsx`;
        showToast('导入模板导出成功！模板中包含示例数据供参考', 'success');
        }

        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } else {
        const error = await response.json();
        showToast(`导出失败: ${error.detail}`, 'error');
    }
    } catch (error) {
    console.error('导出关键词失败:', error);
    showToast('导出关键词失败', 'error');
    }
}

// 显示导入模态框
function showImportModal() {
    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    const modal = new bootstrap.Modal(document.getElementById('importKeywordsModal'));
    modal.show();
}

// 导入关键词
async function importKeywords() {
    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    const fileInput = document.getElementById('importFileInput');
    const file = fileInput.files[0];

    if (!file) {
    showToast('请选择要导入的Excel文件', 'warning');
    return;
    }

    try {
    // 显示进度条
    const progressDiv = document.getElementById('importProgress');
    const progressBar = progressDiv.querySelector('.progress-bar');
    progressDiv.style.display = 'block';
    progressBar.style.width = '30%';

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${apiBase}/keywords-import/${currentCookieId}`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        },
        body: formData
    });

    progressBar.style.width = '70%';

    if (response.ok) {
        const result = await response.json();
        progressBar.style.width = '100%';

        setTimeout(() => {
        progressDiv.style.display = 'none';
        progressBar.style.width = '0%';

        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('importKeywordsModal'));
        modal.hide();

        // 清空文件输入
        fileInput.value = '';

        // 重新加载关键词列表
        loadAccountKeywords(currentCookieId);

        showToast(`导入成功！新增: ${result.added}, 更新: ${result.updated}`, 'success');
        }, 500);
    } else {
        const error = await response.json();
        progressDiv.style.display = 'none';
        progressBar.style.width = '0%';
        showToast(`导入失败: ${error.detail}`, 'error');
    }
    } catch (error) {
    console.error('导入关键词失败:', error);
    document.getElementById('importProgress').style.display = 'none';
    document.querySelector('#importProgress .progress-bar').style.width = '0%';
    showToast('导入关键词失败', 'error');
    }
}

// ========================= 账号添加相关函数 =========================

// 切换手动输入表单显示/隐藏
function toggleManualInput() {
    const manualForm = document.getElementById('manualInputForm');
    const passwordForm = document.getElementById('passwordLoginForm');
    const refreshForm = document.getElementById('refreshCookieForm');
    if (manualForm.style.display === 'none') {
        // 隐藏账号密码登录表单
        if (passwordForm) {
            passwordForm.style.display = 'none';
        }
        // 隐藏刷新Cookie表单
        if (refreshForm) {
            refreshForm.style.display = 'none';
        }
        manualForm.style.display = 'block';
        // 清空表单
        document.getElementById('addForm').reset();
    } else {
        manualForm.style.display = 'none';
        resetManualCookieImportForm();
    }
}

let manualCookieImportCheckInterval = null;
let manualCookieImportSessionId = null;
let manualCookieImportPollingState = {
    sessionId: null,
    inFlight: false,
    completed: false
};

async function handleManualCookieImport(event) {
    event.preventDefault();

    const accountId = document.getElementById('cookieId').value.trim();
    const cookieValue = document.getElementById('cookieValue').value.trim();
    const showBrowserCheckbox = document.getElementById('manualCookieShowBrowser');
    const showBrowser = showBrowserCheckbox ? showBrowserCheckbox.checked : false;

    if (!accountId || !cookieValue) {
        showToast('请填写完整的账号ID和Cookie', 'warning');
        return;
    }

    const submitBtn = event.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>验证中...';

    try {
        const response = await fetch(`${apiBase}/manual-cookie-import`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                account_id: accountId,
                cookie: cookieValue,
                show_browser: showBrowser
            })
        });

        const data = await response.json();
        if (response.ok && data.success && data.session_id) {
            manualCookieImportSessionId = data.session_id;
            startManualCookieImportCheck(originalText);
        } else {
            showToast(data.message || 'Cookie 导入验证失败', 'danger');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    } catch (error) {
        console.error('手动导入 Cookie 失败:', error);
        showToast('网络错误，请重试', 'danger');
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
}

function clearManualCookieImportCheck() {
    if (manualCookieImportCheckInterval) {
        clearInterval(manualCookieImportCheckInterval);
        manualCookieImportCheckInterval = null;
    }
}

function resetManualCookieImportForm() {
    manualCookieImportSessionId = null;
    clearManualCookieImportCheck();
    manualCookieImportPollingState = {
        sessionId: null,
        inFlight: false,
        completed: false
    };

    const submitBtn = document.querySelector('#addForm button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>导入并验证账号';
    }
}

function handleManualCookieImportSuccess(data) {
    closePasswordLoginQRModal();
    showToast(`账号 ${data.account_id} 导入并验证成功`, 'success');

    const form = document.getElementById('addForm');
    if (form) {
        form.reset();
    }
    const manualForm = document.getElementById('manualInputForm');
    if (manualForm) {
        manualForm.style.display = 'none';
    }
    loadCookies();
    resetManualCookieImportForm();
}

function handleManualCookieImportFailure(data) {
    closePasswordLoginQRModal();
    showToast(data.message || data.error || 'Cookie 导入验证失败', 'danger');
    resetManualCookieImportForm();
}

function startManualCookieImportCheck(originalText) {
    clearManualCookieImportCheck();

    const submitBtn = document.querySelector('#addForm button[type="submit"]');
    if (submitBtn) {
        submitBtn.dataset.originalText = originalText;
    }

    manualCookieImportPollingState = {
        sessionId: manualCookieImportSessionId,
        inFlight: false,
        completed: false
    };

    manualCookieImportCheckInterval = setInterval(checkManualCookieImportStatus, 2000);
    checkManualCookieImportStatus();
}

async function checkManualCookieImportStatus() {
    if (!manualCookieImportSessionId || manualCookieImportPollingState.completed || manualCookieImportPollingState.inFlight) {
        return;
    }

    const sessionId = manualCookieImportSessionId;
    manualCookieImportPollingState.inFlight = true;

    try {
        const response = await fetch(`${apiBase}/manual-cookie-import/check/${sessionId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            if (manualCookieImportPollingState.sessionId !== sessionId || manualCookieImportPollingState.completed) {
                return;
            }

            switch (data.status) {
                case 'processing':
                    break;
                case 'verification_required':
                    showPasswordLoginQRCode(
                        data.screenshot_path || data.verification_url,
                        data.screenshot_path,
                        data.verification_type
                    );
                    break;
                case 'success':
                    manualCookieImportPollingState.completed = true;
                    clearManualCookieImportCheck();
                    handleManualCookieImportSuccess(data);
                    break;
                case 'failed':
                    manualCookieImportPollingState.completed = true;
                    clearManualCookieImportCheck();
                    handleManualCookieImportFailure(data);
                    break;
                case 'not_found':
                case 'forbidden':
                case 'error':
                    manualCookieImportPollingState.completed = true;
                    clearManualCookieImportCheck();
                    closePasswordLoginQRModal();
                    showToast(data.message || 'Cookie 导入验证检查失败', 'danger');
                    resetManualCookieImportForm();
                    break;
            }
        } else {
            let errorMessage = 'Cookie 导入验证检查失败';
            try {
                const errorData = await response.json();
                errorMessage = errorData.message || errorData.detail || errorMessage;
            } catch (e) {
                // ignore parse error
            }
            manualCookieImportPollingState.completed = true;
            clearManualCookieImportCheck();
            closePasswordLoginQRModal();
            showToast(errorMessage, 'danger');
            resetManualCookieImportForm();
        }
    } catch (error) {
        console.error('检查手动导入 Cookie 状态失败:', error);
        manualCookieImportPollingState.completed = true;
        clearManualCookieImportCheck();
        closePasswordLoginQRModal();
        showToast('网络错误，请重试', 'danger');
        resetManualCookieImportForm();
    } finally {
        if (manualCookieImportPollingState.sessionId === sessionId) {
            manualCookieImportPollingState.inFlight = false;
        }
    }
}

// 切换账号密码登录表单显示/隐藏
function togglePasswordLogin() {
    const passwordForm = document.getElementById('passwordLoginForm');
    const manualForm = document.getElementById('manualInputForm');
    const refreshForm = document.getElementById('refreshCookieForm');
    if (passwordForm.style.display === 'none') {
        // 隐藏手动输入表单
        if (manualForm) {
            manualForm.style.display = 'none';
            resetManualCookieImportForm();
        }
        // 隐藏刷新Cookie表单
        if (refreshForm) {
            refreshForm.style.display = 'none';
        }
        passwordForm.style.display = 'block';
        // 清空表单
        document.getElementById('passwordLoginFormElement').reset();
    } else {
        passwordForm.style.display = 'none';
    }
}

// 切换刷新Cookie表单显示/隐藏
function toggleRefreshCookieForm() {
    const refreshForm = document.getElementById('refreshCookieForm');
    const manualForm = document.getElementById('manualInputForm');
    const passwordForm = document.getElementById('passwordLoginForm');

    if (refreshForm.style.display === 'none') {
        // 隐藏其他表单
        if (manualForm) {
            manualForm.style.display = 'none';
            resetManualCookieImportForm();
        }
        if (passwordForm) {
            passwordForm.style.display = 'none';
        }
        refreshForm.style.display = 'block';
        // 清空表单
        document.getElementById('refreshCookieFormElement').reset();
        document.getElementById('refreshCookieAccountStatus').innerHTML = '请先选择账号';
        // 加载账号列表到下拉框
        loadRefreshCookieAccountList();
    } else {
        refreshForm.style.display = 'none';
    }
}

// 加载账号列表到刷新Cookie下拉框
async function loadRefreshCookieAccountList() {
    const select = document.getElementById('refreshCookieAccountSelect');
    select.innerHTML = '<option value="">请选择账号...</option>';

    try {
        const response = await fetch(`${apiBase}/cookies/details`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        const data = await response.json();

        if (data && data.length > 0) {
            data.forEach(cookie => {
                const option = document.createElement('option');
                option.value = cookie.id;
                // 显示账号ID和是否配置了用户名密码
                const hasCredentials = cookie.username && cookie.has_password ? '(已配置账密)' : '(未配置账密)';
                option.textContent = `${cookie.id} ${hasCredentials}`;
                option.dataset.hasCredentials = cookie.username && cookie.has_password ? 'true' : 'false';
                option.dataset.username = cookie.username || '';
                select.appendChild(option);
            });
        }
    } catch (error) {
        console.error('加载账号列表失败:', error);
        showToast('加载账号列表失败', 'danger');
    }
}

// 刷新Cookie账号选择变化时显示状态
document.addEventListener('DOMContentLoaded', function() {
    const select = document.getElementById('refreshCookieAccountSelect');
    if (select) {
        select.addEventListener('change', function() {
            const statusDiv = document.getElementById('refreshCookieAccountStatus');
            const selectedOption = this.options[this.selectedIndex];

            if (this.value) {
                const hasCredentials = selectedOption.dataset.hasCredentials === 'true';
                const username = selectedOption.dataset.username;

                if (hasCredentials) {
                    statusDiv.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>已配置用户名: ${username}</span>`;
                } else {
                    statusDiv.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle me-1"></i>未配置用户名和密码，无法刷新</span>`;
                }
            } else {
                statusDiv.innerHTML = '请先选择账号';
            }
        });
    }

    // 绑定刷新Cookie表单提交事件
    const refreshForm = document.getElementById('refreshCookieFormElement');
    if (refreshForm) {
        refreshForm.addEventListener('submit', handleRefreshCookie);
    }
});

// 处理刷新Cookie表单提交
async function handleRefreshCookie(event) {
    event.preventDefault();

    const select = document.getElementById('refreshCookieAccountSelect');
    const cookieId = select.value;
    const selectedOption = select.options[select.selectedIndex];
    const showBrowser = document.getElementById('refreshCookieShowBrowser').checked;

    if (!cookieId) {
        showToast('请选择要刷新的账号', 'warning');
        return;
    }

    const hasCredentials = selectedOption.dataset.hasCredentials === 'true';
    if (!hasCredentials) {
        showToast('该账号未配置用户名和密码，无法刷新Cookie', 'danger');
        return;
    }

    // 显示loading
    toggleLoading(true);

    try {
        // 调用密码登录API刷新Cookie
        const response = await fetch(`${apiBase}/password-login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                account_id: cookieId,
                refresh_mode: true,  // 标记为刷新模式
                show_browser: showBrowser
            })
        });

        const data = await response.json();

        if (data.session_id) {
            // 开始轮询检查登录状态
            showToast('正在验证账号并刷新Cookie，请稍候...', 'info');
            startRefreshCookiePolling(data.session_id, cookieId);
        } else {
            toggleLoading(false);
            showToast(data.message || '启动刷新失败', 'danger');
        }
    } catch (error) {
        toggleLoading(false);
        console.error('刷新Cookie失败:', error);
        showToast('刷新Cookie失败: ' + error.message, 'danger');
    }
}

// 更新刷新Cookie状态显示
function updateRefreshCookieStatus(message) {
    const statusDiv = document.getElementById('refreshCookieAccountStatus');
    if (statusDiv) {
        statusDiv.innerHTML = `<span class="text-info"><i class="bi bi-hourglass-split me-1"></i>${message}</span>`;
    }
}

// 轮询检查刷新Cookie状态
let refreshCookieCheckInterval = null;
let refreshCookiePollingState = {
    sessionId: null,
    cookieId: null,
    inFlight: false,
    completed: false
};

function stopRefreshCookiePolling(sessionId = refreshCookiePollingState.sessionId) {
    if (sessionId && refreshCookiePollingState.sessionId && refreshCookiePollingState.sessionId !== sessionId) {
        return;
    }

    if (refreshCookieCheckInterval) {
        clearInterval(refreshCookieCheckInterval);
        refreshCookieCheckInterval = null;
    }

    refreshCookiePollingState.completed = true;
}

function startRefreshCookiePolling(sessionId, cookieId) {
    // 清除之前的轮询
    stopRefreshCookiePolling();

    refreshCookiePollingState = {
        sessionId,
        cookieId,
        inFlight: false,
        completed: false
    };

    let checkCount = 0;
    const maxChecks = 120; // 最多检查120次，每次2秒，共4分钟

    const pollRefreshCookieStatus = async () => {
        if (refreshCookiePollingState.completed || refreshCookiePollingState.inFlight || refreshCookiePollingState.sessionId !== sessionId) {
            return;
        }

        refreshCookiePollingState.inFlight = true;
        checkCount++;

        if (checkCount > maxChecks) {
            stopRefreshCookiePolling(sessionId);
            closePasswordLoginQRModal();
            toggleLoading(false);
            showToast('刷新Cookie超时，请重试', 'warning');
            refreshCookiePollingState.inFlight = false;
            return;
        }

        try {
            const response = await fetch(`${apiBase}/password-login/check/${sessionId}`, {
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            const data = await response.json();

            if (refreshCookiePollingState.sessionId !== sessionId || refreshCookiePollingState.completed) {
                return;
            }

            console.log('刷新Cookie状态检查:', data); // 调试日志

            switch (data.status) {
                case 'processing':
                    // 处理中，更新状态显示
                    updateRefreshCookieStatus('正在登录中，请稍候...');
                    break;
                case 'verification_required':
                    // 需要身份验证，显示验证截图或链接
                    updateRefreshCookieStatus(`需要${getPasswordLoginVerificationTypeLabel(data.verification_type)}，请查看弹出的验证窗口`);
                    // 使用账号密码登录的验证显示函数
                    showPasswordLoginQRCode(
                        data.screenshot_path || data.verification_url || data.qr_code_url,
                        data.screenshot_path,
                        data.verification_type
                    );
                    break;
                case 'success':
                    stopRefreshCookiePolling(sessionId);
                    const passwordLoginQRModal = document.getElementById('passwordLoginQRModal');
                    if (passwordLoginQRModal && passwordLoginQRModal.classList.contains('show')) {
                        setPasswordLoginQRModalStatus('验证已完成，正在刷新账号状态...');
                        await new Promise(resolve => setTimeout(resolve, 400));
                    }
                    closePasswordLoginQRModal();
                    toggleLoading(false);
                    showToast(`账号 ${cookieId} Cookie刷新成功！`, 'success');
                    // 隐藏表单
                    document.getElementById('refreshCookieForm').style.display = 'none';
                    // 刷新账号列表
                    loadCookies();
                    break;
                case 'failed':
                case 'cancelled':
                case 'error':
                case 'not_found':
                case 'forbidden':
                    stopRefreshCookiePolling(sessionId);
                    closePasswordLoginQRModal();
                    toggleLoading(false);
                    if (data.status === 'cancelled') {
                        showToast(data.message || '刷新Cookie已取消', 'info');
                    } else {
                        showToast(`刷新失败: ${data.message || data.error || '未知错误'}`, 'danger');
                    }
                    break;
            }
        } catch (error) {
            console.error('检查刷新状态失败:', error);
        } finally {
            if (refreshCookiePollingState.sessionId === sessionId) {
                refreshCookiePollingState.inFlight = false;
            }
        }
    };

    refreshCookieCheckInterval = setInterval(pollRefreshCookieStatus, 2000);
    pollRefreshCookieStatus();
}

// ========================= 账号密码登录相关函数 =========================

let passwordLoginCheckInterval = null;
let passwordLoginSessionId = null;
let passwordLoginPollingState = {
    sessionId: null,
    inFlight: false,
    completed: false
};
let passwordLoginQRModalEventsBound = false;
let passwordLoginQRModalState = {
    systemClosing: false,
    cancelInFlight: false
};

// 处理账号密码登录表单提交
async function handlePasswordLogin(event) {
    event.preventDefault();
    
    const accountId = document.getElementById('passwordLoginAccountId').value.trim();
    const account = document.getElementById('passwordLoginAccount').value.trim();
    const password = document.getElementById('passwordLoginPassword').value;
    const showBrowser = document.getElementById('passwordLoginShowBrowser').checked;
    
    if (!accountId || !account || !password) {
        showToast('请填写完整的登录信息', 'warning');
        return;
    }
    
    // 禁用提交按钮，显示加载状态
    const submitBtn = event.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>登录中...';
    
    try {
        const response = await fetch(`${apiBase}/password-login`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                account_id: accountId,
                account: account,
                password: password,
                show_browser: showBrowser
            })
        });
        
        const data = await response.json();
        
        if (response.ok && data.success && data.session_id) {
            passwordLoginSessionId = data.session_id;
            // 开始轮询检查登录状态
            startPasswordLoginCheck();
        } else {
            showToast(data.message || '登录失败，请检查账号密码是否正确', 'danger');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    } catch (error) {
        console.error('账号密码登录失败:', error);
        showToast('网络错误，请重试', 'danger');
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
}

// 开始检查账号密码登录状态
function startPasswordLoginCheck() {
    clearPasswordLoginCheck();

    passwordLoginPollingState = {
        sessionId: passwordLoginSessionId,
        inFlight: false,
        completed: false
    };

    passwordLoginCheckInterval = setInterval(checkPasswordLoginStatus, 2000); // 每2秒检查一次
    checkPasswordLoginStatus();
}

// 检查账号密码登录状态
async function checkPasswordLoginStatus() {
    if (!passwordLoginSessionId || passwordLoginPollingState.completed || passwordLoginPollingState.inFlight) return;

    const sessionId = passwordLoginSessionId;
    passwordLoginPollingState.inFlight = true;
    
    try {
        const response = await fetch(`${apiBase}/password-login/check/${sessionId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();

            if (passwordLoginPollingState.sessionId !== sessionId || passwordLoginPollingState.completed) {
                return;
            }

            console.log('账号密码登录状态检查:', data); // 调试日志
            
            switch (data.status) {
                case 'processing':
                    // 处理中，继续等待
                    break;
                case 'verification_required':
                    // 需要身份验证，显示验证截图或链接
                    showPasswordLoginQRCode(
                        data.screenshot_path || data.verification_url || data.qr_code_url,
                        data.screenshot_path,
                        data.verification_type
                    );
                    // 继续监控（人脸认证后需要继续等待登录完成）
                    break;
                case 'success':
                    // 登录成功
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    handlePasswordLoginSuccess(data);
                    break;
                case 'failed':
                    // 登录失败
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    handlePasswordLoginFailure(data);
                    break;
                case 'cancelled':
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    closePasswordLoginQRModal();
                    showToast(data.message || '登录已取消', 'info');
                    resetPasswordLoginForm();
                    break;
                case 'not_found':
                case 'forbidden':
                case 'error':
                    // 错误情况
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    closePasswordLoginQRModal();
                    showToast(data.message || '登录检查失败', 'danger');
                    resetPasswordLoginForm();
                    break;
            }
        } else {
            // 响应不OK时也尝试解析错误消息
            try {
                const errorData = await response.json();
                passwordLoginPollingState.completed = true;
                clearPasswordLoginCheck();
                closePasswordLoginQRModal();
                showToast(errorData.message || '登录检查失败', 'danger');
                resetPasswordLoginForm();
            } catch (e) {
                passwordLoginPollingState.completed = true;
                clearPasswordLoginCheck();
                closePasswordLoginQRModal();
                showToast('登录检查失败，请重试', 'danger');
                resetPasswordLoginForm();
            }
        }
    } catch (error) {
        console.error('检查账号密码登录状态失败:', error);
        passwordLoginPollingState.completed = true;
        clearPasswordLoginCheck();
        closePasswordLoginQRModal();
        showToast('网络错误，请重试', 'danger');
        resetPasswordLoginForm();
    } finally {
        if (passwordLoginPollingState.sessionId === sessionId) {
            passwordLoginPollingState.inFlight = false;
        }
    }
}

function getPasswordLoginVerificationTypeLabel(verificationType) {
    const normalized = String(verificationType || '').trim();
    const labelMap = {
        face_verify: '人脸验证',
        sms_verify: '短信验证',
        qr_verify: '二维码验证',
        unknown: '身份验证'
    };
    return labelMap[normalized] || normalized || '身份验证';
}

async function cancelPasswordLoginSession(sessionId, flowLabel = '登录') {
    if (!sessionId || passwordLoginQRModalState.cancelInFlight) {
        return;
    }

    passwordLoginQRModalState.cancelInFlight = true;
    try {
        const response = await fetch(`${apiBase}/password-login/cancel/${sessionId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) {
            console.warn(`${flowLabel}取消请求返回异常:`, data);
            showToast(data.message || `已停止当前${flowLabel}轮询`, 'warning');
            return;
        }
        showToast(data.message || `${flowLabel}已取消`, 'info');
    } catch (error) {
        console.error(`取消${flowLabel}会话失败:`, error);
        showToast(`已停止当前${flowLabel}轮询，请稍后重试`, 'warning');
    } finally {
        passwordLoginQRModalState.cancelInFlight = false;
    }
}

function bindPasswordLoginQRModalEvents(modalElement) {
    if (!modalElement || passwordLoginQRModalEventsBound) {
        return;
    }

    modalElement.addEventListener('hidden.bs.modal', function () {
        if (passwordLoginQRModalState.systemClosing) {
            passwordLoginQRModalState.systemClosing = false;
            return;
        }

        if (passwordLoginPollingState.sessionId && !passwordLoginPollingState.completed) {
            const activeSessionId = passwordLoginPollingState.sessionId;
            passwordLoginPollingState.completed = true;
            passwordLoginPollingState.inFlight = false;
            resetPasswordLoginForm();
            void cancelPasswordLoginSession(activeSessionId, '登录');
            return;
        }

        if (refreshCookiePollingState.sessionId && !refreshCookiePollingState.completed) {
            const activeSessionId = refreshCookiePollingState.sessionId;
            stopRefreshCookiePolling(activeSessionId);
            refreshCookiePollingState.inFlight = false;
            toggleLoading(false);
            void cancelPasswordLoginSession(activeSessionId, '刷新Cookie');
            return;
        }

        if (manualCookieImportPollingState.sessionId && !manualCookieImportPollingState.completed) {
            manualCookieImportPollingState.completed = true;
            manualCookieImportPollingState.inFlight = false;
            resetManualCookieImportForm();
            showToast('已停止当前导入验证流程', 'info');
        }
    });

    passwordLoginQRModalEventsBound = true;
}

// 显示账号密码登录验证
function showPasswordLoginQRCode(verificationUrl, screenshotPath, verificationType) {
    // 使用现有的二维码登录模态框
    let modal = document.getElementById('passwordLoginQRModal');
    if (!modal) {
        // 如果模态框不存在，创建一个
        createPasswordLoginQRModal();
        modal = document.getElementById('passwordLoginQRModal');
    }
    bindPasswordLoginQRModalEvents(modal);
    
    // 更新模态框标题
    const modalTitle = document.getElementById('passwordLoginQRModalLabel');
    if (modalTitle) {
        modalTitle.innerHTML = '<i class="bi bi-shield-exclamation text-warning me-2"></i>闲鱼验证';
    }
    
    // 获取或创建模态框实例
    let modalInstance = bootstrap.Modal.getInstance(modal);
    if (!modalInstance) {
        modalInstance = new bootstrap.Modal(modal);
    }
    modalInstance.show();
    
    // 隐藏加载容器
    const qrContainer = document.getElementById('passwordLoginQRContainer');
    if (qrContainer) {
        qrContainer.style.display = 'none';
    }
    
    // 优先显示截图，如果没有截图则显示链接
    const screenshotImg = document.getElementById('passwordLoginScreenshotImg');
    const linkButton = document.getElementById('passwordLoginVerificationLink');
    const statusText = document.getElementById('passwordLoginQRStatusText');
    const verificationTypeLabel = getPasswordLoginVerificationTypeLabel(verificationType);
    
    if (screenshotPath) {
        // 显示截图
        if (screenshotImg) {
            screenshotImg.src = `${normalizeStaticAssetPath(screenshotPath)}?t=${new Date().getTime()}`;
            screenshotImg.style.display = 'block';
            screenshotImg.alt = `${verificationTypeLabel}截图`;
        }
        
        // 隐藏链接按钮
        if (linkButton) {
            linkButton.style.display = 'none';
        }
        
        // 更新状态文本
        if (statusText) {
            statusText.textContent = verificationTypeLabel === '二维码验证'
                ? '需要闲鱼二维码验证，请使用手机闲鱼APP扫描下方二维码完成验证'
                : `需要闲鱼${verificationTypeLabel}，请根据下方验证信息在手机闲鱼APP中完成操作`;
        }
    } else if (verificationUrl) {
        // 隐藏截图
        if (screenshotImg) {
            screenshotImg.style.display = 'none';
        }
        
        // 显示链接按钮
        if (linkButton) {
            linkButton.href = verificationUrl;
            linkButton.style.display = 'inline-block';
        }
        
        // 更新状态文本
        if (statusText) {
            statusText.textContent = `服务端已保持原始会话；如${verificationTypeLabel}入口暂未显示，可使用下方兜底入口`;
        }
    } else {
        // 都没有，显示等待
        if (screenshotImg) {
            screenshotImg.style.display = 'none';
        }
        if (linkButton) {
            linkButton.style.display = 'none';
        }
        if (statusText) {
            statusText.textContent = `需要闲鱼${verificationTypeLabel}，请等待验证信息...`;
        }
    }
}

function closePasswordLoginQRModal() {
    const modalElement = document.getElementById('passwordLoginQRModal');
    if (!modalElement) {
        passwordLoginQRModalState.systemClosing = false;
        return;
    }

    const modalTitle = document.getElementById('passwordLoginQRModalLabel');
    if (modalTitle) {
        modalTitle.innerHTML = '<i class="bi bi-shield-exclamation text-warning me-2"></i>闲鱼验证';
    }

    const screenshotImg = document.getElementById('passwordLoginScreenshotImg');
    if (screenshotImg) {
        screenshotImg.src = '';
        screenshotImg.style.display = 'none';
    }

    const linkButton = document.getElementById('passwordLoginVerificationLink');
    if (linkButton) {
        linkButton.href = '#';
        linkButton.style.display = 'none';
    }

    const statusText = document.getElementById('passwordLoginQRStatusText');
    if (statusText) {
        statusText.textContent = '需要闲鱼身份验证，请等待验证信息...';
    }

    const modalInstance = bootstrap.Modal.getInstance(modalElement);
    if (modalInstance && modalElement.classList.contains('show')) {
        passwordLoginQRModalState.systemClosing = true;
        modalInstance.hide();
    } else {
        passwordLoginQRModalState.systemClosing = false;
    }
}

function setPasswordLoginQRModalStatus(message) {
    const statusText = document.getElementById('passwordLoginQRStatusText');
    if (statusText) {
        statusText.textContent = message;
    }
}

// 创建账号密码登录二维码模态框
function createPasswordLoginQRModal() {
    const modalHtml = `
        <div class="modal fade" id="passwordLoginQRModal" tabindex="-1" aria-labelledby="passwordLoginQRModalLabel" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="passwordLoginQRModalLabel">
                            <i class="bi bi-shield-exclamation text-warning me-2"></i>闲鱼验证
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body text-center">
                        <p id="passwordLoginQRStatusText" class="text-muted mb-3">
                            需要闲鱼身份验证，请等待验证信息...
                        </p>
                        
                        <!-- 截图显示区域 -->
                        <div id="passwordLoginScreenshotContainer" class="mb-3 d-flex justify-content-center">
                            <img id="passwordLoginScreenshotImg" src="" alt="验证截图" 
                                 class="img-fluid" style="display: none; max-width: 400px; height: auto; border: 2px solid #ddd; border-radius: 8px;">
                        </div>
                        
                        <!-- 验证链接按钮（回退方案） -->
                        <div id="passwordLoginLinkContainer" class="mt-4">
                            <a id="passwordLoginVerificationLink" href="#" target="_blank" 
                               class="btn btn-warning btn-lg" style="display: none;">
                                <i class="bi bi-shield-check me-2"></i>
                                打开兜底验证页面
                            </a>
                        </div>
                        
                        <div class="alert alert-info mt-3">
                            <i class="bi bi-info-circle me-2"></i>
                            <small>验证完成后，系统将自动检测并继续登录流程</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    bindPasswordLoginQRModalEvents(document.getElementById('passwordLoginQRModal'));
}

// 处理账号密码登录成功
function handlePasswordLoginSuccess(data) {
    // 关闭二维码模态框
    closePasswordLoginQRModal();
    
    showToast(`账号 ${data.account_id} 登录成功！`, 'success');
    
    // 隐藏表单
    togglePasswordLogin();
    
    // 刷新账号列表
    loadCookies();
    
    // 重置表单
    resetPasswordLoginForm();
}

// 处理账号密码登录失败
function handlePasswordLoginFailure(data) {
    console.log('账号密码登录失败，错误数据:', data); // 调试日志
    
    // 关闭二维码模态框
    closePasswordLoginQRModal();
    
    // 优先使用 message，如果没有则使用 error 字段
    const errorMessage = data.message || data.error || '登录失败，请检查账号密码是否正确';
    console.log('显示错误消息:', errorMessage); // 调试日志
    
    showToast(errorMessage, 'danger');  // 使用 'danger' 而不是 'error'，因为 Bootstrap 使用 'danger' 作为错误类型
    
    // 重置表单
    resetPasswordLoginForm();
}

// 清理账号密码登录检查
function clearPasswordLoginCheck() {
    if (passwordLoginCheckInterval) {
        clearInterval(passwordLoginCheckInterval);
        passwordLoginCheckInterval = null;
    }
}

// 重置账号密码登录表单
function resetPasswordLoginForm() {
    passwordLoginSessionId = null;
    clearPasswordLoginCheck();
    passwordLoginPollingState = {
        sessionId: null,
        inFlight: false,
        completed: false
    };
    
    const submitBtn = document.querySelector('#passwordLoginFormElement button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-box-arrow-in-right me-1"></i>开始登录';
    }
}

// ========================= 扫码登录相关函数 =========================

let qrCodeModalEventsBound = false;
let qrLoginMode = 'standard'; // 'standard' = 原 Playwright；'lite' = 纯 HTTP (cv-cat 风格)
let qrLoginTargetAccountId = '';
let frontendConnectionMode = 'legacy';
const QR_HANDOFF_RETRY_LIMIT = 3;
const QR_HANDOFF_RETRY_DELAY_MS = 1000;
let qrCodeVerificationState = {
    inFlight: false,
    completed: false,
    activeSessionId: null,
    sessionId: null,
    authProgressObserved: false,
    startedAt: 0,
    pollFailureCount: 0,
    checkTimer: null,
    generation: 0,
    generationInFlight: false,
    handoffFailure: null,
};

const QR_CODE_LOADING_HTML = `
    <div class="spinner-border text-success mb-3" role="status" style="width: 3rem; height: 3rem;">
        <span class="visually-hidden">生成二维码中...</span>
    </div>
    <p class="text-muted fs-5 mb-2">正在生成二维码...</p>
    <div class="alert alert-warning border-0 bg-light-warning d-inline-block qr-loading-tip">
        <i class="bi bi-clock me-2 text-warning"></i>
        <small class="text-warning fw-bold">二维码生成较慢，请耐心等待</small>
    </div>
`;

function applyFrontendConnectionMode(value) {
    const normalized = String(value || '').trim().toLowerCase();
    frontendConnectionMode = ['external', 'connector', 'external_connector'].includes(normalized)
        ? 'external_connector'
        : 'legacy';
    const external = frontendConnectionMode === 'external_connector';
    const legacyOnlyActions = document.querySelectorAll?.('[data-requires-legacy-connection="true"]') || [];
    legacyOnlyActions.forEach(element => {
        element.hidden = external;
        element.disabled = external;
        element.setAttribute?.('aria-hidden', external ? 'true' : 'false');
    });
    const hint = document.getElementById('connectorQrModeHint');
    if (hint) {
        hint.hidden = !external;
    }
}

function canStartQrLogin(accountId = '') {
    return frontendConnectionMode !== 'external_connector' || Boolean(String(accountId || '').trim());
}

function getQRLoginEndpoints() {
    if (qrLoginTargetAccountId) {
        const accountPath = encodeURIComponent(qrLoginTargetAccountId);
        return {
            generate: `${apiBase}/api/accounts/${accountPath}/qr-sessions`,
            checkPrefix: `${apiBase}/api/accounts/${accountPath}/qr-sessions/`,
        };
    }
    if (!canStartQrLogin()) {
        return null;
    }
    if (qrLoginMode === 'lite') {
        return {
            generate: `${apiBase}/qr-login-lite/generate`,
            checkPrefix: `${apiBase}/qr-login-lite/check/`,
        };
    }
    return {
        generate: `${apiBase}/qr-login/generate`,
        checkPrefix: `${apiBase}/qr-login/check/`,
    };
}

function renderConnectorReadiness(runtimeStatus) {
    const connectorState = getConnectorRuntimeState(runtimeStatus);
    const connectorNeedsManual = isConnectorManualVerificationRequired(runtimeStatus);
    if (!runtimeStatus || (!connectorState && !connectorNeedsManual)) {
        return '';
    }
    const signals = [
        ['会话', runtimeStatus.session_ready],
        ['令牌', runtimeStatus.has_current_token],
        ['连接', runtimeStatus.ws_ready],
        ['心跳', runtimeStatus.message_stream_ready],
    ];
    const online = !connectorNeedsManual && connectorState === 'online' && signals.every(([, ready]) => Boolean(ready));
    const stateText = getConnectorRuntimeLabel(runtimeStatus) || '状态未知';
    const badgeClass = online ? 'bg-success' : (connectorNeedsManual ? 'bg-warning text-dark' : 'bg-secondary');
    return `
        <div class="mt-1" title="${escapeHtml(runtimeStatus.connector_reason_message || stateText)}">
            <span class="badge ${badgeClass}">${escapeHtml(stateText)}</span>
            <span class="ms-1" aria-label="账号在线检查">
                ${signals.map(([label, ready]) => `
                    <span class="${ready ? 'text-success' : 'text-muted'} small" title="${label}${ready ? '就绪' : '未就绪'}">
                        <i class="bi bi-circle-fill" style="font-size: 0.45rem;"></i>
                    </span>
                `).join('')}
            </span>
        </div>
    `;
}

function applyQRLoginModeChrome() {
    const titleEl = document.getElementById('qrLoginModalTitleText');
    if (titleEl) {
        const modeTitle = qrLoginMode === 'lite' ? '轻量扫码登录闲鱼账号' : '扫码登录闲鱼账号';
        titleEl.textContent = qrLoginTargetAccountId
            ? `${modeTitle} · 恢复 ${qrLoginTargetAccountId}`
            : modeTitle;
    }

    const targetHint = document.getElementById('qrLoginTargetHint');
    if (targetHint) {
        targetHint.style.display = qrLoginTargetAccountId ? 'block' : 'none';
        targetHint.textContent = qrLoginTargetAccountId
            ? `请使用账号 ${qrLoginTargetAccountId} 对应的闲鱼 APP 扫码，成功后会更新原账号 Cookie。`
            : '';
    }
}

function normalizeStaticAssetPath(path) {
    if (!path) {
        return '';
    }
    if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:')) {
        return path;
    }
    return path.startsWith('/') ? path : `/${path}`;
}

function resetQRCodeVerificationState() {
    qrCodeVerificationState.inFlight = false;
    qrCodeVerificationState.completed = false;
    qrCodeVerificationState.activeSessionId = null;
    qrCodeVerificationState.sessionId = null;
    qrCodeVerificationState.authProgressObserved = false;
    qrCodeVerificationState.startedAt = 0;
    qrCodeVerificationState.pollFailureCount = 0;
    qrCodeVerificationState.handoffFailure = null;
}

function getQrHandoffFailure() {
    return qrCodeVerificationState.handoffFailure;
}

function setQRCodeRefreshBusy(busy) {
    const button = document.getElementById('refreshQRBtn');
    if (!button) return;
    button.disabled = Boolean(busy);
    button.innerHTML = busy
        ? '<span class="spinner-border spinner-border-sm me-1" aria-hidden="true"></span>正在生成'
        : '<i class="bi bi-arrow-clockwise me-1"></i>重新生成二维码';
}

function closeQRCodeLoginModal(delay = 3000) {
    setTimeout(() => {
        const modalElement = document.getElementById('qrCodeLoginModal');
        if (!modalElement) {
            loadCookies();
            return;
        }

        const modal = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
        modal.hide();
        loadCookies();
    }, delay);
}

function initializeQRCodeLoginModal() {
    const modalElement = document.getElementById('qrCodeLoginModal');
    if (!modalElement || qrCodeModalEventsBound) {
        return modalElement;
    }

    modalElement.addEventListener('shown.bs.modal', function () {
        generateQRCode();
    });

    modalElement.addEventListener('hidden.bs.modal', function () {
        clearQRCodeCheck();
        qrLoginTargetAccountId = '';
        applyQRLoginModeChrome();
    });

    qrCodeModalEventsBound = true;
    return modalElement;
}

function showAccountQRRelogin(accountId, mode = 'lite') {
    showQRCodeLogin(mode, accountId);
}

async function resumeConnectorAccount(accountId) {
    const normalizedAccountId = String(accountId || '').trim();
    if (!normalizedAccountId) {
        return;
    }
    const idempotencyKey = `resume:${normalizedAccountId}:${crypto.randomUUID?.() || Date.now()}`;
    const response = await authenticatedFetch(`${apiBase}/api/accounts/${encodeURIComponent(normalizedAccountId)}/commands`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'Idempotency-Key': idempotencyKey,
        },
        body: JSON.stringify({ command: 'resume_after_verification', payload: {} }),
    });
    if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        showToast(error.detail || '恢复账号连接失败', 'danger');
        return;
    }
    showToast('已提交恢复命令，正在重新校验账号连接', 'info');
    await loadCookies();
}

async function loadQrManualVerificationRuntime(accountId, options = {}) {
    let response;
    try {
        response = await authenticatedFetch(
            `${apiBase}/api/accounts/${encodeURIComponent(accountId)}/runtime`,
        );
    } catch (error) {
        console.error('加载扫码人工验证状态失败:', error);
        return {
            runtime: null,
            failure: {
                kind: 'network',
                message: '扫码后连接校验暂时不可用，请稍后重试',
            },
        };
    }
    if (!response.ok) {
        return {
            runtime: null,
            failure: {
                kind: 'network',
                message: '扫码后连接校验暂时不可用，请稍后重试',
            },
        };
    }
    const runtimeStatus = await response.json().catch(() => null);
    const reason = String(
        runtimeStatus?.connector_reason_code || runtimeStatus?.reason_code || '',
    ).trim().toLowerCase();
    if (reason.includes('timeout') || reason.includes('network')) {
        return {
            runtime: null,
            failure: {
                kind: 'timeout',
                message: '扫码后连接校验超时，请重新生成二维码',
            },
        };
    }
    const allowStaleEnteredAt = Boolean(
        options.allowStaleEnteredAt && qrCodeVerificationState.authProgressObserved,
    );
    const allowed = isQrManualVerificationHandoffAllowed(
        runtimeStatus,
        qrCodeVerificationState.startedAt,
        { allowStaleEnteredAt },
    );
    return allowed
        ? { runtime: runtimeStatus, failure: null }
        : {
            runtime: null,
            failure: { kind: 'pending', message: '人工验证窗口正在准备，请稍候' },
        };
}

function hideQrModalForManualVerification() {
    const modalElement = document.getElementById('qrCodeLoginModal');
    if (!modalElement || !modalElement.classList.contains('show')) {
        return Promise.resolve();
    }
    return new Promise(resolve => {
        let settled = false;
        const finish = () => {
            if (settled) return;
            settled = true;
            resolve();
        };
        modalElement.addEventListener('hidden.bs.modal', finish, { once: true });
        (bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement)).hide();
        setTimeout(finish, 500);
    });
}

async function openRemoteVerificationConsole(accountId, options = {}) {
    const normalizedAccountId = String(accountId || '').trim();
    if (!normalizedAccountId) return false;
    if (!window.RemoteVerificationConsole?.start) {
        showToast('远程验证组件未加载，请刷新页面重试', 'danger');
        return false;
    }
    let errorMessage = '';
    const started = await window.RemoteVerificationConsole.start({
        accountId: normalizedAccountId,
        authToken: getAuthToken(),
        idempotencyKey: options.idempotencyKey,
        request: authenticatedFetch,
        onToast: (message, tone) => {
            errorMessage = String(message || '');
            showToast(message, tone);
        },
        onRefresh: loadCookies,
        beforeOpen: options.beforeOpen,
    });
    if (!started && typeof options.onError === 'function') {
        options.onError(errorMessage || '远程验证入口暂时不可用，请稍后重试');
    }
    return started;
}

async function startManualVerification(accountId, options = {}) {
    const normalizedAccountId = String(accountId || '').trim();
    const qrSessionId = String(options.qrSessionId || '').trim();
    if (
        !normalizedAccountId
        || !qrSessionId
        || qrSessionId !== qrCodeVerificationState.activeSessionId
        || (!qrCodeVerificationState.authProgressObserved && !options.verificationRequired)
    ) {
        showToast('请先通过“重登”完成本次二维码扫码', 'warning');
        return false;
    }
    return openRemoteVerificationConsole(normalizedAccountId, {
        beforeOpen: options.beforeOpen,
        onError: options.onError,
        idempotencyKey: `remote-create:${normalizedAccountId}:${qrSessionId}`,
    });
}

// 显示扫码登录模态框
function showQRCodeLogin(mode = 'standard', accountId = '') {
    const normalizedAccountId = String(accountId || '').trim();
    if (!canStartQrLogin(normalizedAccountId)) {
        showToast('独立连接器模式不支持无目标账号扫码，请在账号列表使用“重登”', 'warning');
        return false;
    }
    qrLoginMode = mode === 'lite' ? 'lite' : 'standard';
    qrLoginTargetAccountId = normalizedAccountId;
    applyQRLoginModeChrome();
    const modalElement = initializeQRCodeLoginModal();
    if (!modalElement) {
        showToast('扫码登录弹窗未找到，请刷新页面重试', 'danger');
        return false;
    }

    const modal = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
    modal.show();
    return true;
}

// 生成二维码
async function generateQRCode() {
    if (qrCodeVerificationState.generationInFlight) return false;
    const generationSequence = ++qrCodeVerificationState.generation;
    qrCodeVerificationState.generationInFlight = true;
    setQRCodeRefreshBusy(true);
    try {
    showQRCodeLoading();

    const endpoints = getQRLoginEndpoints();
    if (!endpoints) {
        showQRCodeError('独立连接器模式不支持无目标账号扫码，请在账号列表使用“重登”');
        return false;
    }
    const response = await authenticatedFetch(endpoints.generate, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json'
        }
    });

    const data = await response.json().catch(() => ({}));
    if (generationSequence !== qrCodeVerificationState.generation) return false;
    if (response.ok) {
        if (data.success && data.session_id && data.qr_code_url) {
        qrCodeVerificationState.sessionId = data.session_id;
        qrCodeVerificationState.activeSessionId = data.session_id;
        qrCodeVerificationState.startedAt = Math.max(0, Number(data.expires_at || 0) - 90);
        showQRCodeImage(data.qr_code_url);
        startQRCodeCheck();
        return true;
        } else {
        showQRCodeError(data.message || '生成二维码失败');
        }
    } else {
        const detail = typeof data.detail === 'string' ? data.detail : '';
        showQRCodeError(detail || data.message || '生成二维码失败，请点击“重新生成二维码”重试');
    }
    } catch (error) {
    if (generationSequence !== qrCodeVerificationState.generation) return false;
    console.error('生成二维码失败:', error);
    showQRCodeError('网络错误，请点击“重新生成二维码”重试');
    } finally {
    if (generationSequence === qrCodeVerificationState.generation) {
        qrCodeVerificationState.generationInFlight = false;
        setQRCodeRefreshBusy(false);
    }
    }
    return false;
}

// 显示二维码加载状态
function showQRCodeLoading() {
    resetQRCodeVerificationState();
    const container = document.getElementById('qrCodeContainer');
    container.innerHTML = QR_CODE_LOADING_HTML;
    container.style.display = 'block';
    document.getElementById('qrCodeImage').style.display = 'none';
    document.getElementById('statusText').textContent = '正在生成二维码，请耐心等待...';
    document.getElementById('statusSpinner').style.display = 'none';

}

// 显示二维码图片
function showQRCodeImage(qrCodeUrl) {
    document.getElementById('qrCodeContainer').style.display = 'none';
    document.getElementById('qrCodeImage').style.display = 'block';
    document.getElementById('qrCodeImg').src = qrCodeUrl;
    document.getElementById('statusText').textContent = '等待扫码...';
    document.getElementById('statusSpinner').style.display = 'none';
}

// 显示二维码错误
function showQRCodeError(message) {
    const container = document.getElementById('qrCodeContainer');
    container.innerHTML = `
    <div class="text-danger">
        <i class="bi bi-exclamation-triangle fs-1 mb-3"></i>
        <p>${escapeHtml(String(message || '二维码不可用，请重新生成'))}</p>
    </div>
    `;
    container.style.display = 'block';
    document.getElementById('qrCodeImage').style.display = 'none';
    document.getElementById('statusText').textContent = '二维码不可用，请重新生成';
    document.getElementById('statusSpinner').style.display = 'none';
    setQRCodeRefreshBusy(false);
}

// 开始检查二维码状态
function startQRCodeCheck() {
    if (qrCodeVerificationState.checkTimer) {
    clearInterval(qrCodeVerificationState.checkTimer);
    }

    document.getElementById('statusSpinner').style.display = 'inline-block';
    document.getElementById('statusText').textContent = '等待扫码...';

    qrCodeVerificationState.checkTimer = setInterval(checkQRCodeStatus, 2000); // 每2秒检查一次
}

async function handoffQrToManualVerification(data, requestSessionId) {
    qrCodeVerificationState.handoffFailure = null;
    const directVerificationRequired = Boolean(
        data?.verification_required === true
        || data?.status === 'verification_required'
        || data?.state === 'verification_required'
    );
    if ((!qrCodeVerificationState.authProgressObserved && !directVerificationRequired) || !qrLoginTargetAccountId) {
        qrCodeVerificationState.handoffFailure = { kind: 'pending', message: '请先完成本次二维码扫码' };
        return false;
    }
    const failurePriority = { pending: 0, network: 1, timeout: 2, remote: 3, stale: 4 };
    const mergeFailure = (current, next) => {
        if (!next) return current;
        if (!current) return next;
        return (failurePriority[next.kind] ?? 0) > (failurePriority[current.kind] ?? 0)
            ? next
            : current;
    };
    const accountId = qrLoginTargetAccountId;
    for (let attempt = 0; attempt < QR_HANDOFF_RETRY_LIMIT; attempt += 1) {
        if (
            requestSessionId !== qrCodeVerificationState.activeSessionId
            || qrCodeVerificationState.completed
        ) {
            qrCodeVerificationState.handoffFailure = { kind: 'stale', message: '二维码会话已切换，请重新生成二维码' };
            return false;
        }
        const runtimeResult = await loadQrManualVerificationRuntime(accountId, {
            allowStaleEnteredAt: true,
        });
        const wrappedRuntimeResult = Boolean(
            runtimeResult
            && Object.prototype.hasOwnProperty.call(runtimeResult, 'runtime'),
        );
        const runtimeStatus = wrappedRuntimeResult ? runtimeResult.runtime : runtimeResult;
        const runtimeFailure = wrappedRuntimeResult ? runtimeResult.failure : null;
        if (runtimeStatus) {
            let remoteError = '';
            const started = await startManualVerification(accountId, {
                deferModal: true,
                qrSessionId: requestSessionId,
                verificationRequired: directVerificationRequired,
                beforeOpen: hideQrModalForManualVerification,
                onError: message => { remoteError = String(message || ''); },
            });
            if (started) {
                qrCodeVerificationState.completed = true;
                clearQRCodeCheck({ preserveState: true });
                showToast(data.message || '扫码后需要人工验证，请在验证窗口中完成操作', 'warning');
                loadCookies();
                return true;
            }
            qrCodeVerificationState.handoffFailure = mergeFailure(qrCodeVerificationState.handoffFailure, {
                kind: 'remote',
                message: remoteError || '远程验证入口暂时不可用，请稍后重试',
            });
        } else {
            qrCodeVerificationState.handoffFailure = mergeFailure(qrCodeVerificationState.handoffFailure, runtimeFailure || {
                kind: 'pending',
                message: '人工验证窗口正在准备，请稍候',
            });
            if (runtimeFailure?.kind === 'timeout') {
                return false;
            }
        }
        if (attempt < QR_HANDOFF_RETRY_LIMIT - 1) {
            document.getElementById('statusText').textContent =
                `正在准备人工验证窗口（${attempt + 1}/${QR_HANDOFF_RETRY_LIMIT}）...`;
            await new Promise(resolve => {
                if (typeof setTimeout === 'function') setTimeout(resolve, QR_HANDOFF_RETRY_DELAY_MS);
                else resolve();
            });
        }
    }
    return false;
}

// 检查二维码状态
async function checkQRCodeStatus() {
    if (!qrCodeVerificationState.sessionId || qrCodeVerificationState.inFlight || qrCodeVerificationState.completed) return;

    const requestSessionId = qrCodeVerificationState.sessionId;
    qrCodeVerificationState.inFlight = true;

    try {
    const endpoints = getQRLoginEndpoints();
    const response = await authenticatedFetch(`${endpoints.checkPrefix}${requestSessionId}`);

    if (requestSessionId !== qrCodeVerificationState.activeSessionId || qrCodeVerificationState.completed) {
        return;
    }

    if (response.ok) {
        const data = await response.json();

        if (requestSessionId !== qrCodeVerificationState.activeSessionId || qrCodeVerificationState.completed) {
        return;
        }

        const qrStatus = data.verification_required === true
            ? 'verification_required'
            : data.status;
        switch (qrStatus) {
        case 'waiting':
            document.getElementById('statusText').textContent = '等待扫码...';
            break;
        case 'scanned':
            qrCodeVerificationState.authProgressObserved = true;
            document.getElementById('statusText').textContent = '已扫码，请在手机上确认...';
            break;
        case 'confirmed':
            qrCodeVerificationState.authProgressObserved = true;
            document.getElementById('statusText').textContent = '已确认，正在获取Cookie...';
            break;
        case 'success':
            qrCodeVerificationState.completed = true;
            document.getElementById('statusText').textContent = '登录成功！';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck({ preserveState: true });
            handleQRCodeSuccess(data);
            break;
        case 'error':
            qrCodeVerificationState.completed = true;
            clearQRCodeCheck({ preserveState: true });
            showQRCodeError(data.message || '扫码登录失败，请重新生成二维码');
            showToast(data.message || '扫码登录失败，请重新生成二维码', 'danger');
            break;
        case 'expired':
            qrCodeVerificationState.completed = true;
            document.getElementById('statusText').textContent = '二维码已过期';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck({ preserveState: true });
            showQRCodeError('二维码已过期，请刷新重试');
            break;
        case 'cancelled':
            qrCodeVerificationState.completed = true;
            clearQRCodeCheck({ preserveState: true });
            showQRCodeError('用户已取消登录，请重新生成二维码');
            break;
        case 'verification_required':
            if (qrLoginTargetAccountId) {
                const handedOff = await handoffQrToManualVerification(data, requestSessionId);
                if (!handedOff) {
                    const failure = getQrHandoffFailure() || {
                        kind: 'pending',
                        message: '人工验证窗口仍在准备，请重新生成二维码',
                    };
                    qrCodeVerificationState.completed = true;
                    clearQRCodeCheck({ preserveState: true });
                    showQRCodeError(failure.message);
                    if (failure.kind !== 'remote') {
                        showToast(
                            failure.message,
                            failure.kind === 'timeout' ? 'warning' : 'danger',
                        );
                    }
                }
            } else {
                qrCodeVerificationState.completed = true;
                clearQRCodeCheck({ preserveState: true });
                showQRCodeError('当前扫码需要人工验证，请在账号管理中打开“实时验证”');
                showToast('当前扫码需要人工验证，请从账号管理打开实时验证', 'warning');
            }
            break;
        case 'processing':
            qrCodeVerificationState.authProgressObserved = true;
            document.getElementById('statusText').textContent = '正在处理中...';
            // 继续轮询，不清理检查
            break;
        case 'already_processed':
            qrCodeVerificationState.completed = true;
            document.getElementById('statusText').textContent = '登录已完成';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck({ preserveState: true });
            handleQRCodeSuccess(data);
            break;
        default:
            document.getElementById('statusText').textContent = '正在等待扫码状态更新...';
            break;
        }
        qrCodeVerificationState.pollFailureCount = 0;
    } else {
        const errorData = await response.json().catch(() => ({}));
        const expired = response.status === 404 || response.status === 410;
        const message = expired
            ? '二维码会话已失效，请重新生成二维码'
            : (typeof errorData.detail === 'string'
                ? errorData.detail
                : '二维码状态检查失败，请重新生成二维码');
        qrCodeVerificationState.completed = true;
        clearQRCodeCheck({ preserveState: true });
        showQRCodeError(message);
        showToast(message, expired ? 'warning' : 'danger');
    }
    } catch (error) {
    console.error('检查二维码状态失败:', error);
    if (requestSessionId === qrCodeVerificationState.activeSessionId && !qrCodeVerificationState.completed) {
        qrCodeVerificationState.pollFailureCount += 1;
        if (qrCodeVerificationState.pollFailureCount >= 3) {
            qrCodeVerificationState.completed = true;
            clearQRCodeCheck({ preserveState: true });
            showQRCodeError('网络不稳定，二维码状态检查失败，请重新生成二维码');
            showToast('二维码状态检查失败，请重新生成二维码', 'danger');
        } else {
            document.getElementById('statusText').textContent = `状态检查暂时失败，正在重试（${qrCodeVerificationState.pollFailureCount}/3）...`;
        }
    }
    } finally {
    qrCodeVerificationState.inFlight = false;
    }
}

// 处理扫码成功
function handleQRCodeSuccess(data) {
    if (data.account_info) {
    const {
        account_id,
        is_new_account,
        real_cookie_refreshed,
        fallback_reason,
        cookie_length,
        token_prewarmed,
        task_restarted,
        warning_message
    } = data.account_info;
    const targetMatched = !qrLoginTargetAccountId || String(account_id) === qrLoginTargetAccountId;

    // 构建成功消息
    let successMessage = '';
    if (!targetMatched) {
        successMessage = `扫码账号 ${account_id} 与目标账号 ${qrLoginTargetAccountId} 不一致，未执行账号切换。`;
    } else if (qrLoginTargetAccountId) {
        successMessage = `账号重新登录成功，四项在线检查已通过！账号ID: ${account_id}`;
    } else if (is_new_account) {
        successMessage = `新账号添加成功！账号ID: ${account_id}`;
    } else {
        successMessage = `账号Cookie已更新！账号ID: ${account_id}`;
    }

    // 添加cookie长度信息
    if (cookie_length) {
        successMessage += `\nCookie长度: ${cookie_length}`;
    }

    // 添加真实cookie获取状态信息
    if (real_cookie_refreshed === true) {
        if (task_restarted === false) {
            successMessage += '\n✅ 真实Cookie已获取';
            if (warning_message) {
                successMessage += `\n⚠️ ${warning_message}`;
            }
            document.getElementById('statusText').textContent = '登录完成，但账号任务尚未切换';
            showToast(successMessage, targetMatched ? 'warning' : 'danger');
        } else if (token_prewarmed === false) {
            successMessage += '\n✅ 真实Cookie获取并保存成功';
            if (warning_message) {
                successMessage += `\n⚠️ ${warning_message}`;
            }
            document.getElementById('statusText').textContent = '登录完成，账号任务已切换，Token将在后台继续初始化';
            showToast(successMessage, targetMatched ? 'warning' : 'danger');
        } else {
            successMessage += '\n✅ 真实Cookie获取并保存成功';
            document.getElementById('statusText').textContent = '登录成功！真实Cookie已获取并保存';
            showToast(successMessage, targetMatched ? 'success' : 'danger');
        }
    } else if (real_cookie_refreshed === false) {
        successMessage += '\n⚠️ 真实Cookie获取失败，已保存原始扫码Cookie';
        if (fallback_reason) {
            successMessage += `\n原因: ${fallback_reason}`;
        }
        document.getElementById('statusText').textContent = '登录成功，但使用原始Cookie';
        showToast(successMessage, targetMatched ? 'warning' : 'danger');
    } else {
        // 兼容旧版本，没有真实cookie刷新信息
        document.getElementById('statusText').textContent = '登录成功！';
        showToast(successMessage, targetMatched ? 'success' : 'danger');
    }

    closeQRCodeLoginModal(3000);
    return;
    }

    document.getElementById('statusText').textContent = '登录成功！';
    showToast(data.message || '扫码登录已完成，账号信息已同步', 'success');
    closeQRCodeLoginModal(1500);
}

// 清理二维码检查
function stopQRCodeCheck() {
    if (qrCodeVerificationState.checkTimer) {
    clearInterval(qrCodeVerificationState.checkTimer);
    qrCodeVerificationState.checkTimer = null;
    }
    qrCodeVerificationState.inFlight = false;
}

function clearQRCodeCheck(options = {}) {
    stopQRCodeCheck();
    qrCodeVerificationState.generation += 1;
    qrCodeVerificationState.generationInFlight = false;
    if (!options.preserveState) {
        resetQRCodeVerificationState();
    }
    setQRCodeRefreshBusy(false);
}

// 刷新二维码
async function refreshQRCode() {
    clearQRCodeCheck();
    return generateQRCode();
}

// ==================== 图片关键词管理功能 ====================

// 显示添加图片关键词模态框
function showAddImageKeywordModal() {
    if (!currentCookieId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    // 加载商品列表到图片关键词模态框
    loadItemsListForImageKeyword();

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('addImageKeywordModal'));
    modal.show();

    // 清空表单
    document.getElementById('imageKeyword').value = '';
    const imageSelectElement = document.getElementById('imageItemIdSelect');
    if (imageSelectElement) {
        // 清除所有选中项
        Array.from(imageSelectElement.options).forEach(opt => opt.selected = false);
    }
    document.getElementById('imageFile').value = '';
    hideImagePreview();
}

// 为图片关键词模态框加载商品列表
async function loadItemsListForImageKeyword() {
    try {
        const response = await fetch(`${apiBase}/items/${currentCookieId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            const items = data.items || [];

            // 更新商品选择下拉框
            const selectElement = document.getElementById('imageItemIdSelect');
            if (selectElement) {
                // 清空现有选项（保留第一个默认选项）
                selectElement.innerHTML = '<option value="">选择商品或留空表示通用关键词</option>';

                // 添加商品选项
                items.forEach(item => {
                    const option = document.createElement('option');
                    option.value = item.item_id;
                    option.textContent = `${item.item_id} - ${item.item_title}`;
                    selectElement.appendChild(option);
                });
            }

            console.log(`为图片关键词加载了 ${items.length} 个商品到选择列表`);
        } else {
            console.warn('加载商品列表失败:', response.status);
        }
    } catch (error) {
        console.error('加载商品列表时发生错误:', error);
    }
}

// 处理图片文件选择事件监听器
function initImageKeywordEventListeners() {
    const imageFileInput = document.getElementById('imageFile');
    if (imageFileInput && !imageFileInput.hasEventListener) {
        imageFileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                // 验证文件类型
                if (!file.type.startsWith('image/')) {
                    showToast('请选择图片文件', 'warning');
                    e.target.value = '';
                    hideImagePreview();
                    return;
                }

                // 验证文件大小（5MB）
                if (file.size > 5 * 1024 * 1024) {
                    showToast('❌ 图片文件大小不能超过 5MB，当前文件大小：' + (file.size / 1024 / 1024).toFixed(1) + 'MB', 'warning');
                    e.target.value = '';
                    hideImagePreview();
                    return;
                }

                // 验证图片尺寸
                validateImageDimensions(file, e.target);
            } else {
                hideImagePreview();
            }
        });
        imageFileInput.hasEventListener = true;
    }
}

// 验证图片尺寸
function validateImageDimensions(file, inputElement) {
    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = function() {
        const width = this.naturalWidth;
        const height = this.naturalHeight;

        // 释放对象URL
        URL.revokeObjectURL(url);

        // 检查图片尺寸
        const maxDimension = 4096;
        const maxPixels = 8 * 1024 * 1024; // 8M像素
        const totalPixels = width * height;

        if (width > maxDimension || height > maxDimension) {
            showToast(`❌ 图片尺寸过大：${width}x${height}，最大允许：${maxDimension}x${maxDimension}像素`, 'warning');
            inputElement.value = '';
            hideImagePreview();
            return;
        }

        if (totalPixels > maxPixels) {
            showToast(`❌ 图片像素总数过大：${(totalPixels / 1024 / 1024).toFixed(1)}M像素，最大允许：8M像素`, 'warning');
            inputElement.value = '';
            hideImagePreview();
            return;
        }

        // 尺寸检查通过，显示预览和提示信息
        showImagePreview(file);

        // 如果图片较大，提示会被压缩
        if (width > 2048 || height > 2048) {
            showToast(`ℹ️ 图片尺寸较大（${width}x${height}），上传时将自动压缩以优化性能`, 'info');
        } else {
            showToast(`✅ 图片尺寸合适（${width}x${height}），可以上传`, 'success');
        }
    };

    img.onerror = function() {
        URL.revokeObjectURL(url);
        showToast('❌ 无法读取图片文件，请选择有效的图片', 'warning');
        inputElement.value = '';
        hideImagePreview();
    };

    img.src = url;
}

// 显示图片预览
function showImagePreview(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const previewContainer = document.getElementById('imagePreview');
        const previewImg = document.getElementById('previewImg');

        previewImg.src = e.target.result;
        previewContainer.style.display = 'block';
    };
    reader.readAsDataURL(file);
}

// 隐藏图片预览
function hideImagePreview() {
    const previewContainer = document.getElementById('imagePreview');
    if (previewContainer) {
        previewContainer.style.display = 'none';
    }
}

// 添加图片关键词
async function addImageKeyword() {
    const keywordInput = document.getElementById('imageKeyword').value.trim();
    const selectElement = document.getElementById('imageItemIdSelect');
    const selectedOptions = Array.from(selectElement.selectedOptions);
    const fileInput = document.getElementById('imageFile');
    const file = fileInput.files[0];

    if (!keywordInput) {
        showToast('请填写关键词', 'warning');
        return;
    }

    if (!file) {
        showToast('请选择图片文件', 'warning');
        return;
    }

    // 解析多个关键词（支持竖线、换行符分隔）
    const keywords = keywordInput
        .split(/[\|\n]/)
        .map(k => k.trim())
        .filter(k => k.length > 0);
    
    if (keywords.length === 0) {
        showToast('请填写有效的关键词', 'warning');
        return;
    }

    // 获取选中的商品ID列表
    let itemIds = selectedOptions
        .map(opt => opt.value)
        .filter(id => id !== ''); // 过滤掉空值（通用关键词选项）
    
    // 如果没有选中任何商品，或者选中了空值，则作为通用关键词
    if (itemIds.length === 0) {
        itemIds = [''];
    }

    if (!currentCookieId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        toggleLoading(true);

        // 检查重复关键词
        const allKeywords = keywordsData[currentCookieId] || [];
        const duplicates = [];
        for (const keyword of keywords) {
            for (const itemId of itemIds) {
                const existingKeyword = allKeywords.find(item =>
                    item.keyword === keyword &&
                    (item.item_id || '') === (itemId || '')
                );
                if (existingKeyword) {
                    const itemIdText = itemId ? `（商品ID: ${itemId}）` : '（通用关键词）';
                    duplicates.push(`"${keyword}" ${itemIdText}`);
                }
            }
        }

        if (duplicates.length > 0) {
            showToast(`以下关键词已存在：\n${duplicates.join('\n')}\n请修改后重试`, 'warning');
            toggleLoading(false);
            return;
        }

        const totalCount = keywords.length * itemIds.length;

        // 第一步：先上传一次图片获取URL
        const formData = new FormData();
        formData.append('image', file);

        const uploadResponse = await fetch(`${apiBase}/upload-image`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            },
            body: formData
        });

        if (!uploadResponse.ok) {
            const errorData = await uploadResponse.json().catch(() => ({}));
            showToast(`❌ 图片上传失败: ${errorData.detail || '请检查后重试'}`, 'danger');
            toggleLoading(false);
            return;
        }

        const uploadResult = await uploadResponse.json();
        const imageUrl = uploadResult.image_url;

        if (!imageUrl) {
            showToast('❌ 图片上传失败：未获取到图片URL', 'danger');
            toggleLoading(false);
            return;
        }

        // 第二步：使用批量API添加所有关键词
        const batchResponse = await fetch(`${apiBase}/keywords/${currentCookieId}/image-batch`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                image_url: imageUrl,
                keywords: keywords,
                item_ids: itemIds
            })
        });

        if (batchResponse.ok) {
            const result = await batchResponse.json();
            const successCount = result.success_count || 0;
            const failCount = result.fail_count || 0;

            if (successCount > 0) {
                const keywordText = keywords.length > 1 ? `${keywords.length}个关键词` : `"${keywords[0]}"`;
                const itemText = itemIds.length > 1 ? `${itemIds.length}个商品` : (itemIds[0] ? '指定商品' : '通用');
                
                if (failCount === 0) {
                    showToast(`✨ ${keywordText} 添加成功！（共${totalCount}条配置，应用于${itemText}）`, 'success');
                } else {
                    showToast(`⚠️ 部分添加成功：成功${successCount}条，失败${failCount}条`, 'warning');
                }

                // 关闭模态框
                const modal = bootstrap.Modal.getInstance(document.getElementById('addImageKeywordModal'));
                modal.hide();

                // 只刷新关键词列表，不重新加载整个界面
                await refreshKeywordsList();
            } else {
                showToast('❌ 所有图片关键词添加失败，请检查后重试', 'danger');
            }
        } else {
            const errorData = await batchResponse.json().catch(() => ({}));
            showToast(`❌ 添加图片关键词失败: ${errorData.detail || '请检查后重试'}`, 'danger');
        }
    } catch (error) {
        console.error('添加图片关键词失败:', error);
        showToast('添加图片关键词失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 显示图片模态框
function showImageModal(imageUrl) {
    // 创建模态框HTML
    const modalHtml = `
        <div class="modal fade" id="imageViewModal" tabindex="-1">
            <div class="modal-dialog modal-lg modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">图片预览</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body text-center">
                        <img src="${imageUrl}" alt="关键词图片" style="max-width: 100%; max-height: 70vh; border-radius: 8px;">
                    </div>
                </div>
            </div>
        </div>
    `;

    // 移除已存在的模态框
    const existingModal = document.getElementById('imageViewModal');
    if (existingModal) {
        existingModal.remove();
    }

    // 添加新模态框
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('imageViewModal'));
    modal.show();

    // 模态框关闭后移除DOM元素
    document.getElementById('imageViewModal').addEventListener('hidden.bs.modal', function() {
        this.remove();
    });
}

// 编辑图片关键词（不允许修改）
function editImageKeyword(index) {
    showToast('图片关键词不允许修改，请删除后重新添加', 'warning');
}

// 修改导出关键词函数，使用后端导出API
async function exportKeywords() {
    if (!currentCookieId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        toggleLoading(true);

        // 使用后端导出API
        const response = await fetch(`${apiBase}/keywords-export/${currentCookieId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            // 获取文件blob
            const blob = await response.blob();

            // 从响应头获取文件名
            const contentDisposition = response.headers.get('Content-Disposition');
            let fileName = `关键词数据_${currentCookieId}_${new Date().toISOString().slice(0, 10)}.xlsx`;

            if (contentDisposition) {
                const fileNameMatch = contentDisposition.match(/filename\*=UTF-8''(.+)/);
                if (fileNameMatch) {
                    fileName = decodeURIComponent(fileNameMatch[1]);
                }
            }

            // 创建下载链接
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = fileName;
            document.body.appendChild(a);
            a.click();

            // 清理
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showToast('✅ 关键词导出成功', 'success');
        } else {
            const errorText = await response.text();
            console.error('导出关键词失败:', errorText);
            showToast('导出关键词失败', 'danger');
        }
    } catch (error) {
        console.error('导出关键词失败:', error);
        showToast('导出关键词失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// ==================== 备注管理功能 ====================

// 编辑备注
function editRemark(cookieId, currentRemark) {
    console.log('editRemark called:', cookieId, currentRemark); // 调试信息
    const remarkCell = document.querySelector(`[data-cookie-id="${cookieId}"] .remark-display`);
    if (!remarkCell) {
        console.log('remarkCell not found'); // 调试信息
        return;
    }

    // 创建输入框
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control form-control-sm';
    input.value = currentRemark || '';
    input.placeholder = '请输入备注...';
    input.style.fontSize = '0.875rem';
    input.maxLength = 100; // 限制备注长度

    // 保存原始内容和原始值
    const originalContent = remarkCell.innerHTML;
    const originalValue = currentRemark || '';

    // 标记是否已经进行了编辑
    let hasChanged = false;
    let isProcessing = false; // 防止重复处理

    // 替换为输入框
    remarkCell.innerHTML = '';
    remarkCell.appendChild(input);

    // 监听输入变化
    input.addEventListener('input', () => {
        hasChanged = input.value.trim() !== originalValue;
    });

    // 保存函数
    const saveRemark = async () => {
        console.log('saveRemark called, isProcessing:', isProcessing, 'hasChanged:', hasChanged); // 调试信息
        if (isProcessing) return; // 防止重复调用

        const newRemark = input.value.trim();
        console.log('newRemark:', newRemark, 'originalValue:', originalValue); // 调试信息

        // 如果没有变化，直接恢复显示
        if (!hasChanged || newRemark === originalValue) {
            console.log('No changes detected, restoring original content'); // 调试信息
            remarkCell.innerHTML = originalContent;
            return;
        }

        isProcessing = true;

        try {
            const response = await fetch(`${apiBase}/cookies/${cookieId}/remark`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ remark: newRemark })
            });

            if (response.ok) {
                // 更新显示
                remarkCell.innerHTML = `
                    <span class="remark-display" onclick="editRemark('${cookieId}', '${newRemark.replace(/'/g, '&#39;')}')" title="点击编辑备注" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                        ${newRemark || '<i class="bi bi-plus-circle text-muted"></i> 添加备注'}
                    </span>
                `;
                showToast('备注更新成功', 'success');
            } else {
                const errorData = await response.json();
                showToast(`备注更新失败: ${errorData.detail || '未知错误'}`, 'danger');
                // 恢复原始内容
                remarkCell.innerHTML = originalContent;
            }
        } catch (error) {
            console.error('更新备注失败:', error);
            showToast('备注更新失败', 'danger');
            // 恢复原始内容
            remarkCell.innerHTML = originalContent;
        } finally {
            isProcessing = false;
        }
    };

    // 取消函数
    const cancelEdit = () => {
        if (isProcessing) return;
        remarkCell.innerHTML = originalContent;
    };

    // 延迟绑定blur事件，避免立即触发
    setTimeout(() => {
        input.addEventListener('blur', saveRemark);
    }, 100);

    // 绑定键盘事件
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveRemark();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelEdit();
        }
    });

    // 聚焦并选中文本
    input.focus();
    input.select();
}

// 编辑暂停时间
function editPauseDuration(cookieId, currentDuration) {
    console.log('editPauseDuration called:', cookieId, currentDuration); // 调试信息
    const pauseCell = document.querySelector(`[data-cookie-id="${cookieId}"] .pause-duration-display`);
    if (!pauseCell) {
        console.log('pauseCell not found'); // 调试信息
        return;
    }

    // 创建输入框
    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'form-control form-control-sm';
    input.value = currentDuration !== undefined ? currentDuration : 10;
    input.placeholder = '请输入暂停时间...';
    input.style.fontSize = '0.875rem';
    input.min = 0;
    input.max = 60;
    input.step = 1;

    // 保存原始内容和原始值
    const originalContent = pauseCell.innerHTML;
    const originalValue = currentDuration !== undefined ? currentDuration : 10;

    // 标记是否已经进行了编辑
    let hasChanged = false;
    let isProcessing = false; // 防止重复处理

    // 替换为输入框
    pauseCell.innerHTML = '';
    pauseCell.appendChild(input);

    // 监听输入变化
    input.addEventListener('input', () => {
        const newValue = input.value === '' ? 10 : parseInt(input.value);
        hasChanged = newValue !== originalValue;
    });

    // 保存函数
    const savePauseDuration = async () => {
        console.log('savePauseDuration called, isProcessing:', isProcessing, 'hasChanged:', hasChanged); // 调试信息
        if (isProcessing) return; // 防止重复调用

        const newDuration = input.value === '' ? 10 : parseInt(input.value);
        console.log('newDuration:', newDuration, 'originalValue:', originalValue); // 调试信息

        // 验证范围
        if (isNaN(newDuration) || newDuration < 0 || newDuration > 60) {
            showToast('暂停时间必须在0-60分钟之间（0表示不暂停）', 'warning');
            input.focus();
            return;
        }

        // 如果没有变化，直接恢复显示
        if (!hasChanged || newDuration === originalValue) {
            console.log('No changes detected, restoring original content'); // 调试信息
            pauseCell.innerHTML = originalContent;
            return;
        }

        isProcessing = true;

        try {
            const response = await fetch(`${apiBase}/cookies/${cookieId}/pause-duration`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ pause_duration: newDuration })
            });

            if (response.ok) {
                // 更新显示
                pauseCell.innerHTML = `
                    <span class="pause-duration-display" onclick="editPauseDuration('${cookieId}', ${newDuration})" title="点击编辑暂停时间" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                        <i class="bi bi-clock me-1"></i>${newDuration === 0 ? '不暂停' : newDuration + '分钟'}
                    </span>
                `;
                showToast('暂停时间更新成功', 'success');
            } else {
                const errorData = await response.json();
                showToast(`暂停时间更新失败: ${errorData.detail || '未知错误'}`, 'danger');
                // 恢复原始内容
                pauseCell.innerHTML = originalContent;
            }
        } catch (error) {
            console.error('更新暂停时间失败:', error);
            showToast('暂停时间更新失败', 'danger');
            // 恢复原始内容
            pauseCell.innerHTML = originalContent;
        } finally {
            isProcessing = false;
        }
    };

    // 取消函数
    const cancelEdit = () => {
        if (isProcessing) return;
        pauseCell.innerHTML = originalContent;
    };

    // 延迟绑定blur事件，避免立即触发
    setTimeout(() => {
        input.addEventListener('blur', savePauseDuration);
    }, 100);

    // 绑定键盘事件
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            savePauseDuration();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelEdit();
        }
    });

    // 聚焦并选中文本
    input.focus();
    input.select();
}

// ==================== 工具提示初始化 ====================

// 初始化工具提示
function initTooltips() {
    // 初始化所有工具提示
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

// ==================== 系统设置功能 ====================

// 加载系统设置
async function loadSystemSettings() {
    console.log('加载系统设置');

    // 通过验证接口获取用户信息（更可靠）
    try {
        const response = await fetch(`${apiBase}/verify`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            const isAdmin = result.is_admin === true;

            console.log('用户信息:', result, '是否管理员:', isAdmin);

            // 显示/隐藏管理员专用设置（仅管理员可见）
            const apiSecuritySettings = document.getElementById('api-security-settings');
            const loginInfoSettings = document.getElementById('login-info-settings');
            const riskControlSettings = document.getElementById('risk-control-settings');
            const outgoingConfigs = document.getElementById('outgoing-configs');
            const backupManagement = document.getElementById('backup-management');
            const systemRestartBtn = document.getElementById('system-restart-btn');
            const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');

            if (apiSecuritySettings) {
                apiSecuritySettings.style.display = isAdmin ? 'block' : 'none';
            }
            if (loginInfoSettings) {
                loginInfoSettings.style.display = isAdmin ? 'flex' : 'none';
            }
            if (riskControlSettings) {
                riskControlSettings.style.display = isAdmin ? 'block' : 'none';
            }
            if (outgoingConfigs) {
                outgoingConfigs.style.display = isAdmin ? 'block' : 'none';
            }
            if (backupManagement) {
                backupManagement.style.display = isAdmin ? 'block' : 'none';
            }
            if (systemRestartBtn) {
                systemRestartBtn.style.display = isAdmin ? 'inline-block' : 'none';
            }
            if (dashboardHotUpdateGroup) {
                dashboardHotUpdateGroup.style.display = isAdmin ? 'inline-flex' : 'none';
            }

            // 如果是管理员，加载所有管理员设置
            if (isAdmin) {
                refreshHotUpdatePreferencesMenu();
                await loadAPISecuritySettings();
                await loadRegistrationSettings();
                await loadLoginInfoSettings();
                await loadRiskControlNightSettings();
                await loadOutgoingConfigs();
            }
        }
    } catch (error) {
        console.error('获取用户信息失败:', error);
        // 出错时隐藏管理员功能
        const loginInfoSettings = document.getElementById('login-info-settings');
        const riskControlSettings = document.getElementById('risk-control-settings');
        const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');
        if (loginInfoSettings) {
            loginInfoSettings.style.display = 'none';
        }
        if (riskControlSettings) {
            riskControlSettings.style.display = 'none';
        }
        if (dashboardHotUpdateGroup) {
            dashboardHotUpdateGroup.style.display = 'none';
        }
    }
}

// 加载API安全设置
async function loadAPISecuritySettings() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const settings = await response.json();

            // 加载QQ回复消息秘钥
            const qqReplySecretKey = settings.qq_reply_secret_key || '';
            const qqReplySecretKeyInput = document.getElementById('qqReplySecretKey');
            if (qqReplySecretKeyInput) {
                qqReplySecretKeyInput.value = qqReplySecretKey;
            }
        }
    } catch (error) {
        console.error('加载API安全设置失败:', error);
        showToast('加载API安全设置失败', 'danger');
    }
}

async function loadRiskControlNightSettings() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('加载夜间风控降频设置失败');
        }

        const settings = await response.json();
        const enabledInput = document.getElementById('riskControlNightModeEnabled');
        const startHourInput = document.getElementById('riskControlNightStartHour');
        const endHourInput = document.getElementById('riskControlNightEndHour');

        if (enabledInput) {
            enabledInput.checked = settings.risk_control_night_mode_enabled === 'true';
        }
        if (startHourInput) {
            startHourInput.value = settings.risk_control_night_start_hour || '1';
        }
        if (endHourInput) {
            endHourInput.value = settings.risk_control_night_end_hour || '6';
        }
    } catch (error) {
        console.error('加载夜间风控降频设置失败:', error);
        showToast('加载夜间风控降频设置失败', 'danger');
    }
}

async function saveRiskControlNightSettings() {
    const enabledInput = document.getElementById('riskControlNightModeEnabled');
    const startHourInput = document.getElementById('riskControlNightStartHour');
    const endHourInput = document.getElementById('riskControlNightEndHour');
    const statusBox = document.getElementById('riskControlNightSettingsStatus');

    if (!enabledInput || !startHourInput || !endHourInput) {
        return;
    }

    const startHour = Number.parseInt(startHourInput.value, 10);
    const endHour = Number.parseInt(endHourInput.value, 10);
    if (Number.isNaN(startHour) || startHour < 0 || startHour > 23 || Number.isNaN(endHour) || endHour < 0 || endHour > 23) {
        showToast('夜间时间必须填写 0-23 的整数小时', 'warning');
        return;
    }

    const payloads = [
        {
            key: 'risk_control_night_mode_enabled',
            value: enabledInput.checked ? 'true' : 'false',
            description: '是否启用夜间风控降频',
        },
        {
            key: 'risk_control_night_start_hour',
            value: String(startHour),
            description: '夜间风控降频开始小时',
        },
        {
            key: 'risk_control_night_end_hour',
            value: String(endHour),
            description: '夜间风控降频结束小时',
        }
    ];

    try {
        for (const item of payloads) {
            const response = await fetch(`/system-settings/${item.key}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({
                    value: item.value,
                    description: item.description,
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `保存 ${item.key} 失败`);
            }
        }

        if (statusBox) {
            statusBox.textContent = `夜间风控降频设置已保存：${enabledInput.checked ? '开启' : '关闭'}，区间 ${String(startHour).padStart(2, '0')}:00 - ${String(endHour).padStart(2, '0')}:00`;
            statusBox.classList.remove('d-none');
        }
        showToast('夜间风控降频设置已保存', 'success');
    } catch (error) {
        console.error('保存夜间风控降频设置失败:', error);
        showToast(`保存夜间风控降频设置失败: ${error.message || '未知错误'}`, 'danger');
    }
}

// 加载防抖延迟设置
async function loadDebounceDelay() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        if (response.ok) {
            const settings = await response.json();
            const val = settings.message_debounce_delay;
            const input = document.getElementById('debounceDelay');
            if (input && val) {
                input.value = parseInt(val) || 3;
            }
        }
    } catch (error) {
        console.error('加载防抖延迟设置失败:', error);
    }
}

// 保存防抖延迟设置
async function saveDebounceDelay() {
    const input = document.getElementById('debounceDelay');
    if (!input) return;
    const val = parseInt(input.value);
    if (isNaN(val) || val < 1 || val > 10) {
        showToast('防抖延迟需在1-10秒之间', 'warning');
        return;
    }
    try {
        const response = await fetch('/system-settings/message_debounce_delay', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                key: 'message_debounce_delay',
                value: String(val),
                description: '消息防抖延迟时间（秒）'
            })
        });
        if (response.ok) {
            showToast('防抖延迟已保存', 'success');
        } else {
            showToast('保存防抖延迟失败', 'danger');
        }
    } catch (error) {
        console.error('保存防抖延迟失败:', error);
        showToast('保存防抖延迟失败', 'danger');
    }
}

// 切换密码可见性
function togglePasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    const icon = document.getElementById(inputId + '-icon');

    if (input && icon) {
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }
}

// 生成随机秘钥
function generateRandomSecretKey() {
    // 生成32位随机字符串
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = 'xianyu_qq_';
    for (let i = 0; i < 24; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }

    const qqReplySecretKeyInput = document.getElementById('qqReplySecretKey');
    if (qqReplySecretKeyInput) {
        qqReplySecretKeyInput.value = result;
        showToast('随机秘钥已生成', 'success');
    }
}

// 更新QQ回复消息秘钥
async function updateQQReplySecretKey() {
    const qqReplySecretKey = document.getElementById('qqReplySecretKey').value.trim();

    if (!qqReplySecretKey) {
        showToast('请输入QQ回复消息API秘钥', 'warning');
        return;
    }

    if (qqReplySecretKey.length < 8) {
        showToast('秘钥长度至少需要8位字符', 'warning');
        return;
    }

    try {
        const response = await fetch('/system-settings/qq_reply_secret_key', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                value: qqReplySecretKey,
                description: 'QQ回复消息API秘钥'
            })
        });

        if (response.ok) {
            showToast('QQ回复消息API秘钥更新成功', 'success');

            // 显示状态信息
            const statusDiv = document.getElementById('qqReplySecretStatus');
            const statusText = document.getElementById('qqReplySecretStatusText');
            if (statusDiv && statusText) {
                statusText.textContent = `秘钥已更新，长度: ${qqReplySecretKey.length} 位`;
                statusDiv.style.display = 'block';

                // 3秒后隐藏状态
                setTimeout(() => {
                    statusDiv.style.display = 'none';
                }, 3000);
            }
        } else {
            const errorData = await response.json();
            showToast(`更新失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('更新QQ回复消息秘钥失败:', error);
        showToast('更新QQ回复消息秘钥失败', 'danger');
    }
}

// 加载外发配置
async function loadOutgoingConfigs() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            const settings = await response.json();
            
            // 渲染外发配置界面
            renderOutgoingConfigs(settings);
        }
    } catch (error) {
        console.error('加载外发配置失败:', error);
        showToast('加载外发配置失败', 'danger');
    }
}

// 渲染外发配置界面
function renderOutgoingConfigs(settings) {
    const container = document.getElementById('outgoing-configs');
    if (!container) return;
    
    let html = '<div class="row">';
    
    // 渲染SMTP配置
    const smtpConfig = outgoingConfigs.smtp;
    html += `
        <div class="col-12">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">
                        <i class="bi ${smtpConfig.icon} text-${smtpConfig.color} me-2"></i>
                        ${smtpConfig.title}
                    </h5>
                </div>
                <div class="card-body">
                    <p class="text-muted">${smtpConfig.description}</p>
                    <form id="smtp-config-form">
                        <div class="row">`;
    
    smtpConfig.fields.forEach(field => {
        const value = settings[field.id] || '';
        html += `
            <div class="col-md-6 mb-3">
                <label for="${field.id}" class="form-label">${field.label}</label>
                ${generateOutgoingFieldHtml(field, value)}
                <div class="form-text">${field.help}</div>
            </div>`;
    });
    
    html += `
                        </div>
                        <div class="text-end">
                            <button type="submit" class="btn btn-primary">
                                <i class="bi bi-save me-1"></i>保存SMTP配置
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>`;
    
    html += '</div>';
    container.innerHTML = html;
    
    // 绑定表单提交事件
    const form = document.getElementById('smtp-config-form');
    if (form) {
        form.addEventListener('submit', saveOutgoingConfigs);
    }
}

// 生成外发配置字段HTML
function generateOutgoingFieldHtml(field, value) {
    switch (field.type) {
        case 'select':
            let options = '';
            field.options.forEach(option => {
                const selected = value === option.value ? 'selected' : '';
                options += `<option value="${option.value}" ${selected}>${option.text}</option>`;
            });
            return `<select class="form-select" id="${field.id}" name="${field.id}" ${field.required ? 'required' : ''}>${options}</select>`;
        
        case 'password':
            return `<input type="password" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
        
        case 'number':
            return `<input type="number" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
        
        case 'email':
            return `<input type="email" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
        
        default:
            return `<input type="text" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
    }
}

// 保存外发配置
async function saveOutgoingConfigs(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    const configs = {};
    
    // 收集表单数据
    for (let [key, value] of formData.entries()) {
        configs[key] = value;
    }
    
    try {
        // 逐个保存配置项
        for (const [key, value] of Object.entries(configs)) {
            const response = await fetch(`/system-settings/${key}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({
                    key: key,
                    value: value,
                    description: `SMTP配置 - ${key}`
                })
            });
            
            if (!response.ok) {
                throw new Error(`保存${key}失败`);
            }
        }
        
        showToast('外发配置保存成功', 'success');
        
        // 重新加载配置
        await loadOutgoingConfigs();
        
    } catch (error) {
        console.error('保存外发配置失败:', error);
        showToast('保存外发配置失败: ' + error.message, 'danger');
    }
}

// 加载注册设置
async function loadRegistrationSettings() {
    try {
        const response = await fetch('/registration-status');
        if (response.ok) {
            const data = await response.json();
            const checkbox = document.getElementById('registrationEnabled');
            if (checkbox) {
                checkbox.checked = data.enabled;
            }
        }
    } catch (error) {
        console.error('加载注册设置失败:', error);
        showToast('加载注册设置失败', 'danger');
    }
}

// 加载默认登录信息设置
async function loadLoginInfoSettings() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const settings = await response.json();
            const checkbox = document.getElementById('showDefaultLoginInfo');
            const captchaCheckbox = document.getElementById('loginCaptchaEnabled');

            if (checkbox && settings.show_default_login_info !== undefined) {
                checkbox.checked = settings.show_default_login_info === 'true';
            }

            if (captchaCheckbox && settings.login_captcha_enabled !== undefined) {
                captchaCheckbox.checked = settings.login_captcha_enabled === 'true';
            } else if (captchaCheckbox) {
                // 默认开启
                captchaCheckbox.checked = true;
            }
        }
    } catch (error) {
        console.error('加载登录信息设置失败:', error);
        showToast('加载登录信息设置失败', 'danger');
    }
}

// 更新登录与注册设置
async function updateLoginInfoSettings() {
    const registrationCheckbox = document.getElementById('registrationEnabled');
    const checkbox = document.getElementById('showDefaultLoginInfo');
    const captchaCheckbox = document.getElementById('loginCaptchaEnabled');
    const statusDiv = document.getElementById('loginInfoStatus');
    const statusText = document.getElementById('loginInfoStatusText');

    try {
        let messages = [];

        // 更新用户注册设置
        if (registrationCheckbox) {
            const regEnabled = registrationCheckbox.checked;
            const regResponse = await fetch('/registration-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ enabled: regEnabled })
            });

            if (regResponse.ok) {
                messages.push(regEnabled ? '用户注册已开启' : '用户注册已关闭');
            } else {
                const errorData = await regResponse.json();
                showToast(`更新注册设置失败: ${errorData.detail || '未知错误'}`, 'danger');
                return;
            }
        }

        // 更新显示默认登录信息设置
        if (checkbox) {
            const enabled = checkbox.checked;
            const response = await fetch('/login-info-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ enabled: enabled })
            });

            if (response.ok) {
                messages.push(enabled ? '默认登录信息显示已开启' : '默认登录信息显示已关闭');
            } else {
                const errorData = await response.json();
                showToast(`更新默认登录信息设置失败: ${errorData.detail || '未知错误'}`, 'danger');
                return;
            }
        }

        // 更新登录验证码设置
        if (captchaCheckbox) {
            const captchaEnabled = captchaCheckbox.checked;
            const captchaResponse = await fetch('/login-captcha-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ enabled: captchaEnabled })
            });

            if (captchaResponse.ok) {
                messages.push(captchaEnabled ? '登录验证码已开启' : '登录验证码已关闭');
            } else {
                const errorData = await captchaResponse.json();
                showToast(`更新登录验证码设置失败: ${errorData.detail || '未知错误'}`, 'danger');
                return;
            }
        }

        // 显示成功消息
        const message = messages.join('，');
        showToast('设置保存成功', 'success');

        // 显示状态信息
        if (statusDiv && statusText) {
            statusText.textContent = message;
            statusDiv.style.display = 'block';

            // 3秒后隐藏状态信息
            setTimeout(() => {
                statusDiv.style.display = 'none';
            }, 3000);
        }
    } catch (error) {
        console.error('更新登录信息设置失败:', error);
        showToast('更新登录信息设置失败', 'danger');
    }
}

// ================================
// 订单管理功能
// ================================

function isOrdersSectionActive() {
    const section = document.getElementById('orders-section');
    return !!section && section.classList.contains('active');
}

function stopOrdersStream() {
    ordersStreamShouldRun = false;

    if (ordersStreamReconnectTimer) {
        clearTimeout(ordersStreamReconnectTimer);
        ordersStreamReconnectTimer = null;
    }

    if (ordersStreamAbortController) {
        ordersStreamAbortController.abort();
        ordersStreamAbortController = null;
    }
}

window.addEventListener('pagehide', stopOrdersStream);

function scheduleOrdersStreamReconnect() {
    if (!ordersStreamShouldRun || !isOrdersSectionActive()) return;
    if (ordersStreamReconnectTimer) return;

    const retryDelay = Math.min(10000, [1000, 2000, 5000, 10000][Math.min(ordersStreamRetryCount, 3)]);
    ordersStreamReconnectTimer = setTimeout(() => {
        ordersStreamReconnectTimer = null;
        startOrdersStream();
    }, retryDelay);
}

function handleOrdersStreamEvent(eventName, payloadText) {
    if (!payloadText) return;
    if (eventName === 'ping' || eventName === 'stream.ready') return;

    try {
        const payload = JSON.parse(payloadText);
        if (eventName === 'order.updated' && payload.order) {
            applyRealtimeOrderUpdate(payload.order);
        }
    } catch (error) {
        console.error('解析订单实时事件失败:', error, payloadText);
    }
}

function applyRealtimeOrderUpdate(order) {
    if (!order || !order.order_id) return;

    const existingIndex = allOrdersData.findIndex(item => item.order_id === order.order_id);
    if (existingIndex === -1) {
        refreshOrdersData();
        return;
    }

    allOrdersData[existingIndex] = {
        ...allOrdersData[existingIndex],
        ...order,
    };

    filterOrders(false);
}

async function consumeOrdersStream(response, controller) {
    if (!response.body) {
        throw new Error('订单实时流不可用');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (controller.signal.aborted) break;

        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split(/\r?\n\r?\n/);
        buffer = chunks.pop() || '';

        chunks.forEach(chunk => {
            let eventName = 'message';
            const dataLines = [];

            chunk.split(/\r?\n/).forEach(line => {
                if (line.startsWith('event:')) {
                    eventName = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                    dataLines.push(line.slice(5).trimStart());
                }
            });

            handleOrdersStreamEvent(eventName, dataLines.join('\n'));
        });
    }
}

async function startOrdersStream() {
    if (!authToken || !isOrdersSectionActive()) return;
    if (ordersStreamAbortController) return;

    ordersStreamShouldRun = true;

    if (ordersStreamReconnectTimer) {
        clearTimeout(ordersStreamReconnectTimer);
        ordersStreamReconnectTimer = null;
    }

    const controller = new AbortController();
    ordersStreamAbortController = controller;

    try {
        const response = await fetch(`${apiBase}/api/orders/stream`, {
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Accept': 'text/event-stream'
            },
            cache: 'no-store',
            signal: controller.signal
        });

        if (response.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }

        if (!response.ok) {
            throw new Error(`订单实时流连接失败: HTTP ${response.status}`);
        }

        ordersStreamRetryCount = 0;
        await consumeOrdersStream(response, controller);
    } catch (error) {
        if (!controller.signal.aborted) {
            ordersStreamRetryCount += 1;
            console.error('订单实时流异常:', error);
            scheduleOrdersStreamReconnect();
        }
    } finally {
        if (ordersStreamAbortController === controller) {
            ordersStreamAbortController = null;
        }

        if (!controller.signal.aborted && ordersStreamShouldRun && isOrdersSectionActive()) {
            scheduleOrdersStreamReconnect();
        }
    }
}

// 加载订单列表
async function loadOrders() {
    try {
        // 先加载Cookie列表用于筛选
        await loadOrderCookieFilter();

        // 加载订单列表
        await refreshOrdersData();

        if (pendingOrderLocator) {
            applyPendingOrderLocator();
        }

        startOrdersStream();
    } catch (error) {
        console.error('加载订单列表失败:', error);
        showToast('加载订单列表失败', 'danger');
    }
}

// 只刷新订单数据，不重新加载筛选器
async function refreshOrdersData() {
    try {
        await loadAllOrders();
    } catch (error) {
        console.error('刷新订单数据失败:', error);
        showToast('刷新订单数据失败', 'danger');
    }
}

// 加载Cookie筛选选项
async function loadOrderCookieFilter() {
    try {
        const select = document.getElementById('orderCookieFilter');
        const previousValue = select ? select.value : '';

        const accounts = await fetchOrderSyncAccounts(true);
        if (select) {
            renderOrderAccountOptions(select, accounts, { includeAllOption: true });

            if (previousValue && accounts.some(account => account.id === previousValue)) {
                select.value = previousValue;
            }
        }
    } catch (error) {
        console.error('加载Cookie选项失败:', error);
    }
}

// 加载所有订单
async function loadAllOrders() {
    try {
        const response = await fetch(`${apiBase}/api/orders`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        const data = await response.json();
        if (data.success) {
            allOrdersData = data.data || [];
            // 历史同步后优先按平台下单时间排序，回退到入库时间
            allOrdersData.sort((a, b) => {
                const bTime = parseUtcDateTime(getOrderPrimarySortTime(b))?.getTime() || 0;
                const aTime = parseUtcDateTime(getOrderPrimarySortTime(a))?.getTime() || 0;
                return bTime - aTime;
            });

            // 应用当前筛选条件
            filterOrders(false);
        } else {
            console.error('加载订单失败:', data.message);
            showToast('加载订单数据失败: ' + data.message, 'danger');
        }
    } catch (error) {
        console.error('加载订单失败:', error);
        showToast('加载订单数据失败，请检查网络连接', 'danger');
    }
}

// 根据Cookie加载订单
async function loadOrdersByCookie() {
    filterOrders(false);
}

function normalizeOrderLocatorKeyword(value) {
    return String(value || '').trim().replace(/@goofish$/i, '');
}

function applyPendingOrderLocator() {
    const locator = pendingOrderLocator;
    if (!locator) return false;

    const searchInput = document.getElementById('orderSearchInput');
    const statusFilter = document.getElementById('orderStatusFilter');
    const cookieFilter = document.getElementById('orderCookieFilter');
    const keyword = normalizeOrderLocatorKeyword(locator.keyword || locator.buyerId || locator.buyerNick || locator.chatId);

    if (searchInput) searchInput.value = keyword;
    if (statusFilter) statusFilter.value = '';
    if (cookieFilter && locator.cookieId) {
        const hasMatchedOption = Array.from(cookieFilter.options || []).some(option => option.value === locator.cookieId);
        cookieFilter.value = hasMatchedOption ? locator.cookieId : '';
    }

    pendingOrderLocator = null;
    filterOrders(true);

    const matchedText = filteredOrdersData.length ? `，已定位到 ${filteredOrdersData.length} 条订单` : '，暂未匹配到订单';
    showToast(`已跳转到独立订单页${matchedText}`, filteredOrdersData.length ? 'success' : 'info');
    return true;
}

function openOrdersFromChat() {
    const buyerKeyword = normalizeOrderLocatorKeyword(chatCurrentToUserId || chatCurrentSenderName || chatCurrentChatId);
    if (!chatCurrentCookieId || !buyerKeyword) {
        showToast('当前会话缺少账号或买家信息，无法定位订单', 'warning');
        return;
    }

    pendingOrderLocator = {
        cookieId: chatCurrentCookieId,
        buyerId: normalizeOrderLocatorKeyword(chatCurrentToUserId),
        buyerNick: chatCurrentSenderName,
        chatId: chatCurrentChatId,
        keyword: buyerKeyword,
    };

    const wasOrdersActive = isOrdersSectionActive();
    showSection('orders');
    if (wasOrdersActive) {
        applyPendingOrderLocator();
    }
}

// 筛选订单
function filterOrders(resetPage = true) {
    const searchKeyword = document.getElementById('orderSearchInput')?.value.toLowerCase() || '';
    const statusFilter = document.getElementById('orderStatusFilter')?.value || '';
    const cookieFilter = document.getElementById('orderCookieFilter')?.value || '';
    const normalizedStatusFilter = statusFilter ? normalizeOrderStatus(statusFilter) : '';

    filteredOrdersData = allOrdersData.filter(order => {
        // 搜索关键词筛选（订单ID、商品ID、买家ID、买家昵称）
        const matchesSearch = !searchKeyword ||
            (order.order_id && order.order_id.toLowerCase().includes(searchKeyword)) ||
            (order.item_id && order.item_id.toLowerCase().includes(searchKeyword)) ||
            (order.buyer_id && order.buyer_id.toLowerCase().includes(searchKeyword)) ||
            (order.buyer_nick && order.buyer_nick.toLowerCase().includes(searchKeyword));

        const matchesCookie = !cookieFilter || order.cookie_id === cookieFilter;
        const matchesStatus = !normalizedStatusFilter || normalizeOrderStatus(order.order_status) === normalizedStatusFilter;

        return matchesSearch && matchesCookie && matchesStatus;
    });

    currentOrderSearchKeyword = searchKeyword;
    if (resetPage) {
        currentOrdersPage = 1; // 重置到第一页
    }

    updateOrdersDisplay();
}

// 更新订单显示
function updateOrdersDisplay() {
    const computedTotalPages = filteredOrdersData.length === 0 ? 0 : Math.ceil(filteredOrdersData.length / ordersPerPage);
    if (computedTotalPages === 0) {
        currentOrdersPage = 1;
    } else {
        currentOrdersPage = Math.min(currentOrdersPage, computedTotalPages);
    }

    displayOrders();
    updateOrdersPagination();
    updateOrdersSearchStats();
}

// 显示订单列表
function displayOrders() {
    const tbody = document.getElementById('ordersTableBody');
    if (!tbody) return;

    if (filteredOrdersData.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="11" class="text-center text-muted py-4">
                    <i class="bi bi-inbox display-6 d-block mb-2"></i>
                    ${currentOrderSearchKeyword ? '没有找到匹配的订单' : '暂无订单数据'}
                </td>
            </tr>
        `;
        return;
    }

    // 计算分页
    totalOrdersPages = Math.ceil(filteredOrdersData.length / ordersPerPage);
    const startIndex = (currentOrdersPage - 1) * ordersPerPage;
    const endIndex = startIndex + ordersPerPage;
    const pageOrders = filteredOrdersData.slice(startIndex, endIndex);

    // 生成表格行
    tbody.innerHTML = pageOrders.map(order => createOrderRow(order)).join('');
}

// 创建订单行HTML
function createOrderRow(order) {
    const statusClass = getOrderStatusClass(order.order_status);
    const statusText = getOrderStatusText(order.order_status);
    const normalizedStatus = normalizeOrderStatus(order.order_status);
    const orderId = escapeHtml(order.order_id || '');
    const itemId = escapeHtml(order.item_id || '-');
    const buyerId = escapeHtml(order.buyer_id || '-');
    const buyerNick = escapeHtml(order.buyer_nick || '-');
    const cookieId = escapeHtml(order.cookie_id || '-');
    const specName = escapeHtml(order.spec_name || '');
    const specValue = escapeHtml(order.spec_value || '');
    const specName2 = escapeHtml(order.spec_name_2 || '');
    const specValue2 = escapeHtml(order.spec_value_2 || '');
    const quantity = escapeHtml(order.quantity || '-');
    const amountDisplay = escapeHtml(formatOrderAmountDisplay(order.amount));
    const isPendingConfirm = normalizedStatus === 'partial_pending_finalize' || order.pending_platform_confirm === true;
    const pendingConfirmError = escapeHtml(order.pending_confirm_error || '');
    const pendingConfirmTitle = pendingConfirmError || (isPendingConfirm ? '卡券已发出，平台确认发货失败，等待补确认' : '');

    // 判断是否可以手动发货（允许多次发货，除了交易关闭的订单）
    const canDeliver = !['cancelled', 'refunding'].includes(normalizedStatus);

    let specHtml = '-';
    if (order.spec_name && order.spec_value) {
        specHtml = `<small class="text-muted">${specName}:</small><br>${specValue}`;
        if (order.spec_name_2 && order.spec_value_2) {
            specHtml += `<br><small class="text-muted">${specName2}:</small><br>${specValue2}`;
        }
    }

    return `
        <tr>
            <td>
                <input type="checkbox" class="order-checkbox" value="${orderId}">
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 120px;" title="${orderId}">
                    ${orderId}
                </span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 100px;" title="${itemId === '-' ? '' : itemId}">
                    ${itemId}
                </span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 80px;" title="${buyerId === '-' ? '' : buyerId}">
                    ${buyerId}
                </span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 100px;" title="${buyerNick === '-' ? '' : buyerNick}">
                    ${buyerNick}
                </span>
            </td>
            <td>
                ${specHtml}
            </td>
            <td>${quantity}</td>
            <td>
                <span class="text-success fw-bold">${amountDisplay}</span>
            </td>
            <td>
                <span class="badge ${statusClass}" title="${pendingConfirmTitle}">${escapeHtml(statusText)}</span>
                ${pendingConfirmError ? `<div class="small text-warning text-truncate mt-1" style="max-width: 140px;" title="${pendingConfirmError}">${pendingConfirmError}</div>` : ''}
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 80px;" title="${cookieId === '-' ? '' : cookieId}">
                    ${cookieId}
                </span>
            </td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    ${isPendingConfirm ? `
                    <button class="btn btn-outline-warning btn-sm order-action-btn" data-order-action="confirm-retry" data-order-id="${orderId}" title="补确认发货（只调用平台确认，不重复发卡券）">
                        <i class="bi bi-check2-circle"></i>
                    </button>` : ''}
                    <button class="btn btn-outline-success btn-sm order-action-btn" data-order-action="deliver" data-order-id="${orderId}" title="手动发货" ${canDeliver ? '' : 'disabled'}>
                        <i class="bi bi-truck"></i>
                    </button>
                    <button class="btn btn-outline-info btn-sm order-action-btn" data-order-action="refresh" data-order-id="${orderId}" title="刷新状态">
                        <i class="bi bi-arrow-repeat"></i>
                    </button>
                    <button class="btn btn-outline-primary btn-sm order-action-btn" data-order-action="detail" data-order-id="${orderId}" title="查看详情">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-outline-danger btn-sm order-action-btn" data-order-action="delete" data-order-id="${orderId}" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `;
}

// 获取订单状态样式类
function getOrderStatusClass(status) {
    const normalizedStatus = normalizeOrderStatus(status);
    const statusMap = {
        'processing': 'bg-warning text-dark',
        'pending_payment': 'bg-warning text-dark',
        'pending_ship': 'bg-info text-white',
        'partial_success': 'bg-primary-subtle text-primary-emphasis',
        'partial_pending_finalize': 'bg-warning-subtle text-warning-emphasis',
        'shipped': 'bg-primary text-white',
        'completed': 'bg-success text-white',
        'success': 'bg-success text-white',
        'refunding': 'bg-warning text-dark',
        'refund_cancelled': 'bg-info text-dark',
        'cancelled': 'bg-secondary text-white',
        'unknown': 'bg-secondary text-white'
    }; 
    return statusMap[normalizedStatus] || statusMap[status] || 'bg-secondary text-white';
}

// 获取订单状态文本
function getOrderStatusText(status) {
    const normalizedStatus = normalizeOrderStatus(status);
    const statusMap = {
        'processing': '处理中',
        'pending_payment': '待付款',
        'pending_ship': '待发货',
        'partial_success': '部分发货',
        'partial_pending_finalize': '待补确认',
        'shipped': '已发货',
        'completed': '交易成功',
        'success': '交易成功',
        'refunding': '申请退款中',
        'refund_cancelled': '退款已撤销',
        'cancelled': '交易关闭',
        'unknown': '未知'
    };
    return statusMap[normalizedStatus] || statusMap[status] || status || '未知';
}

// 更新订单分页
function updateOrdersPagination() {
    const pageInfo = document.getElementById('ordersPageInfo');
    const pageInput = document.getElementById('ordersPageInput');
    const totalPagesSpan = document.getElementById('ordersTotalPages');

    if (pageInfo) {
        const startIndex = (currentOrdersPage - 1) * ordersPerPage + 1;
        const endIndex = Math.min(currentOrdersPage * ordersPerPage, filteredOrdersData.length);
        pageInfo.textContent = `显示第 ${startIndex}-${endIndex} 条，共 ${filteredOrdersData.length} 条记录`;
    }

    if (pageInput) {
        pageInput.value = currentOrdersPage;
    }

    if (totalPagesSpan) {
        totalPagesSpan.textContent = totalOrdersPages;
    }

    // 更新分页按钮状态
    const firstPageBtn = document.getElementById('ordersFirstPage');
    const prevPageBtn = document.getElementById('ordersPrevPage');
    const nextPageBtn = document.getElementById('ordersNextPage');
    const lastPageBtn = document.getElementById('ordersLastPage');

    if (firstPageBtn) firstPageBtn.disabled = currentOrdersPage === 1;
    if (prevPageBtn) prevPageBtn.disabled = currentOrdersPage === 1;
    if (nextPageBtn) nextPageBtn.disabled = currentOrdersPage === totalOrdersPages || totalOrdersPages === 0;
    if (lastPageBtn) lastPageBtn.disabled = currentOrdersPage === totalOrdersPages || totalOrdersPages === 0;
}

// 更新搜索统计信息
function updateOrdersSearchStats() {
    const searchStats = document.getElementById('orderSearchStats');
    const searchStatsText = document.getElementById('orderSearchStatsText');

    if (searchStats && searchStatsText) {
        if (currentOrderSearchKeyword) {
            searchStatsText.textContent = `搜索 "${currentOrderSearchKeyword}" 找到 ${filteredOrdersData.length} 个结果`;
            searchStats.style.display = 'block';
        } else {
            searchStats.style.display = 'none';
        }
    }
}

// 跳转到指定页面
function goToOrdersPage(page) {
    if (page < 1 || page > totalOrdersPages) return;

    currentOrdersPage = page;
    updateOrdersDisplay();
}

// 初始化订单搜索功能
function initOrdersSearch() {
    // 初始化分页大小
    const pageSizeSelect = document.getElementById('ordersPageSize');
    if (pageSizeSelect) {
        ordersPerPage = parseInt(pageSizeSelect.value) || 20;
        pageSizeSelect.addEventListener('change', changeOrdersPageSize);
    }

    // 初始化搜索输入框事件监听器
    const searchInput = document.getElementById('orderSearchInput');
    if (searchInput) {
        // 使用防抖来避免频繁搜索
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                filterOrders();
            }, 300); // 300ms 防抖延迟
        });
    }

    // 初始化页面输入框事件监听器
    const pageInput = document.getElementById('ordersPageInput');
    if (pageInput) {
        pageInput.addEventListener('keydown', handleOrdersPageInput);
    }
}

// 处理分页大小变化
function changeOrdersPageSize() {
    const pageSizeSelect = document.getElementById('ordersPageSize');
    if (pageSizeSelect) {
        ordersPerPage = parseInt(pageSizeSelect.value) || 20;
        currentOrdersPage = 1; // 重置到第一页
        updateOrdersDisplay();
    }
}

// 处理页面输入
function handleOrdersPageInput(event) {
    if (event.key === 'Enter') {
        const pageInput = document.getElementById('ordersPageInput');
        if (pageInput) {
            const page = parseInt(pageInput.value);
            if (page >= 1 && page <= totalOrdersPages) {
                goToOrdersPage(page);
            } else {
                pageInput.value = currentOrdersPage; // 恢复当前页码
                showToast('页码超出范围', 'warning');
            }
        }
    }
}

// 刷新订单列表
async function refreshOrders() {
    await refreshOrdersData();
    showToast('订单列表已刷新', 'success');
}

async function openOrderRecoverModal() {
    try {
        const modalElement = document.getElementById('orderRecoverModal');
        if (!modalElement) return;

        const accounts = await fetchOrderSyncAccounts(true);
        const select = document.getElementById('orderRecoverCookieId');
        renderOrderAccountOptions(select, accounts, { includeAllOption: false });

        const pageFilterValue = document.getElementById('orderCookieFilter')?.value || '';
        if (select && pageFilterValue && Array.from(select.options).some(option => option.value === pageFilterValue)) {
            select.value = pageFilterValue;
        }

        ['orderRecoverOrderId', 'orderRecoverItemId', 'orderRecoverBuyerId'].forEach(id => {
            const input = document.getElementById(id);
            if (input) input.value = '';
        });
        const autoDeliver = document.getElementById('orderRecoverAutoDeliver');
        if (autoDeliver) autoDeliver.checked = true;

        bootstrap.Modal.getOrCreateInstance(modalElement).show();
    } catch (error) {
        console.error('打开订单补抓弹窗失败:', error);
        showToast('加载订单补抓配置失败', 'danger');
    }
}

async function recoverOrderById() {
    const cookieId = String(document.getElementById('orderRecoverCookieId')?.value || '').trim();
    const orderId = String(document.getElementById('orderRecoverOrderId')?.value || '').trim();
    const itemId = String(document.getElementById('orderRecoverItemId')?.value || '').trim();
    const buyerId = String(document.getElementById('orderRecoverBuyerId')?.value || '').trim();
    const autoDeliver = Boolean(document.getElementById('orderRecoverAutoDeliver')?.checked);

    if (!cookieId) {
        showToast('请选择账号', 'warning');
        return;
    }
    if (!/^\d{10,}$/.test(orderId)) {
        showToast('请输入正确的订单ID', 'warning');
        return;
    }

    const submitBtn = document.getElementById('orderRecoverSubmitBtn');
    const originalHtml = submitBtn?.innerHTML || '';
    if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>补抓中';
    }

    try {
        const response = await fetch(`${apiBase}/api/orders/recover`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                cookie_id: cookieId,
                order_id: orderId,
                item_id: itemId || null,
                buyer_id: buyerId || null,
                auto_deliver: autoDeliver
            })
        });

        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success) {
            throw new Error(result.detail || result.message || '订单补抓失败');
        }

        showToast(result.message || '订单补抓完成', result.delivered ? 'success' : 'info');
        bootstrap.Modal.getInstance(document.getElementById('orderRecoverModal'))?.hide();
        await refreshOrdersData();
    } catch (error) {
        console.error('订单补抓失败:', error);
        showToast(error.message || '订单补抓失败', 'danger');
    } finally {
        if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalHtml || '<i class="bi bi-search"></i> 开始补抓';
        }
    }
}

function getOrderPrimarySortTime(order) {
    const platformCreatedAt = String(order?.platform_created_at || '').trim();
    if (platformCreatedAt) {
        return platformCreatedAt;
    }

    const createdAt = String(order?.created_at || '').trim();
    return createdAt || null;
}

function getRelativeBeijingDateInputValue(offsetDays = 0) {
    return getBeijingDateKey(new Date(Date.now() + offsetDays * 24 * 60 * 60 * 1000));
}

async function fetchOrderSyncAccounts(forceRefresh = false) {
    if (!forceRefresh && orderHistorySyncAccounts.length > 0) {
        return orderHistorySyncAccounts;
    }

    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
    });

    if (!response.ok) {
        throw new Error(`获取账号列表失败: HTTP ${response.status}`);
    }

    const accounts = await response.json();
    orderHistorySyncAccounts = Array.isArray(accounts) ? accounts : [];
    return orderHistorySyncAccounts;
}

function formatOrderAccountLabel(account) {
    const accountId = String(account?.id || '').trim();
    const remark = String(account?.remark || '').trim();
    if (remark) {
        return `${remark} (${accountId})`;
    }
    return accountId || '未命名账号';
}

function renderOrderAccountOptions(select, accounts, options = {}) {
    if (!select) return;

    const {
        includeAllOption = false,
        allOptionLabel = '所有账号',
    } = options;

    const previousValue = select.value;
    select.innerHTML = includeAllOption ? `<option value="">${allOptionLabel}</option>` : '';

    (accounts || []).forEach(account => {
        const accountId = String(account?.id || '').trim();
        if (!accountId) return;

        const option = document.createElement('option');
        option.value = accountId;
        option.textContent = formatOrderAccountLabel(account);
        select.appendChild(option);
    });

    if (previousValue && Array.from(select.options).some(option => option.value === previousValue)) {
        select.value = previousValue;
    }
}

function resetOrderHistorySyncProgress() {
    renderOrderHistorySyncJob({
        status: 'idle',
        message: '选择账号和日期范围后即可开始同步。',
        request: {},
        accounts_total: 0,
        accounts_completed: 0,
        orders_discovered: 0,
        orders_processed: 0,
        orders_saved: 0,
        orders_skipped: 0,
        orders_failed: 0,
        matched_orders: 0,
        warnings: [],
    });
}

function setOrderHistorySyncFormDisabled(disabled) {
    [
        'orderHistorySyncCookieId',
        'orderHistorySyncStartDate',
        'orderHistorySyncEndDate',
        'orderHistorySyncMaxOrders',
        'orderHistorySyncFetchDetails',
    ].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.disabled = disabled;
        }
    });

    const startBtn = document.getElementById('orderHistorySyncStartBtn');
    const cancelBtn = document.getElementById('orderHistorySyncCancelBtn');
    if (startBtn) {
        startBtn.disabled = disabled;
        startBtn.innerHTML = disabled
            ? '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>同步中'
            : '<i class="bi bi-play-circle"></i> 开始同步';
    }
    if (cancelBtn) {
        cancelBtn.style.display = disabled ? '' : 'none';
        cancelBtn.disabled = false;
    }
}

function stopOrderHistorySyncPolling() {
    if (orderHistorySyncPollingTimer) {
        clearTimeout(orderHistorySyncPollingTimer);
        orderHistorySyncPollingTimer = null;
    }
}

function scheduleOrderHistorySyncPolling(jobId) {
    stopOrderHistorySyncPolling();
    orderHistorySyncPollingTimer = setTimeout(() => {
        fetchOrderHistorySyncStatus(jobId).catch(error => {
            console.error('轮询历史订单同步状态失败:', error);
        });
    }, 2000);
}

function getOrderHistorySyncStatusMeta(job) {
    const status = String(job?.status || '').toLowerCase();
    const statusMap = {
        idle: { label: '待命', badgeClass: 'bg-secondary text-white', progressClass: 'bg-secondary', title: '未开始' },
        pending: { label: '排队中', badgeClass: 'bg-secondary text-white', progressClass: 'bg-secondary', title: '等待执行' },
        running: { label: '进行中', badgeClass: 'bg-primary text-white', progressClass: 'bg-primary', title: '同步中' },
        completed: { label: '已完成', badgeClass: 'bg-success text-white', progressClass: 'bg-success', title: '同步完成' },
        failed: { label: '失败', badgeClass: 'bg-danger text-white', progressClass: 'bg-danger', title: '同步失败' },
        cancelled: { label: '已取消', badgeClass: 'bg-warning text-dark', progressClass: 'bg-warning', title: '同步已取消' },
    };
    return statusMap[status] || statusMap.idle;
}

function renderOrderHistorySyncJob(job) {
    const statusMeta = getOrderHistorySyncStatusMeta(job);
    const request = job?.request || {};
    const accountsTotal = Number(job?.accounts_total || 0);
    const accountsCompleted = Number(job?.accounts_completed || 0);
    const ordersDiscovered = Number(job?.orders_discovered || 0);
    const matchedOrders = Number(job?.matched_orders || 0);
    const ordersSaved = Number(job?.orders_saved || 0);
    const ordersFailed = Number(job?.orders_failed || 0);
    const ordersProcessed = Number(job?.orders_processed || 0);
    const ordersSkipped = Number(job?.orders_skipped || 0);
    const warnings = Array.isArray(job?.warnings) ? job.warnings : [];

    const statusText = document.getElementById('orderHistorySyncStatusText');
    const messageText = document.getElementById('orderHistorySyncMessageText');
    const statusBadge = document.getElementById('orderHistorySyncStatusBadge');
    const progressBar = document.getElementById('orderHistorySyncProgressBar');
    const accountsStat = document.getElementById('orderHistorySyncAccountsStat');
    const discoveredStat = document.getElementById('orderHistorySyncDiscoveredStat');
    const matchedStat = document.getElementById('orderHistorySyncMatchedStat');
    const savedStat = document.getElementById('orderHistorySyncSavedStat');
    const metaText = document.getElementById('orderHistorySyncMetaText');
    const currentText = document.getElementById('orderHistorySyncCurrentText');
    const warningsWrap = document.getElementById('orderHistorySyncWarningsWrap');
    const warningsContainer = document.getElementById('orderHistorySyncWarnings');
    const cookieSelect = document.getElementById('orderHistorySyncCookieId');
    const startDateInput = document.getElementById('orderHistorySyncStartDate');
    const endDateInput = document.getElementById('orderHistorySyncEndDate');
    const maxOrdersInput = document.getElementById('orderHistorySyncMaxOrders');
    const fetchDetailsInput = document.getElementById('orderHistorySyncFetchDetails');

    if (cookieSelect && Object.prototype.hasOwnProperty.call(request, 'cookie_id')) {
        cookieSelect.value = request.cookie_id || '';
    }
    if (startDateInput && request.start_date) {
        startDateInput.value = request.start_date;
    }
    if (endDateInput && request.end_date) {
        endDateInput.value = request.end_date;
    }
    if (maxOrdersInput && request.max_orders) {
        maxOrdersInput.value = String(request.max_orders);
    }
    if (fetchDetailsInput && Object.prototype.hasOwnProperty.call(request, 'fetch_details')) {
        fetchDetailsInput.checked = Boolean(request.fetch_details);
    }

    if (statusText) {
        statusText.textContent = statusMeta.title;
    }
    if (messageText) {
        messageText.textContent = job?.message || '选择账号和日期范围后即可开始同步。';
    }
    if (statusBadge) {
        statusBadge.className = `badge ${statusMeta.badgeClass}`;
        statusBadge.textContent = statusMeta.label;
    }

    let progressPercent = 0;
    const status = String(job?.status || '').toLowerCase();
    if (status === 'completed' || status === 'failed' || status === 'cancelled') {
        progressPercent = 100;
    } else if (accountsTotal > 0) {
        const accountProgress = accountsCompleted / accountsTotal;
        const orderProgress = matchedOrders > 0 ? (ordersProcessed / matchedOrders) : 0;
        progressPercent = Math.max(accountProgress, orderProgress) * 100;
    } else if (status === 'pending') {
        progressPercent = 8;
    }

    if (progressBar) {
        progressBar.className = `progress-bar ${statusMeta.progressClass}`;
        progressBar.style.width = `${Math.max(0, Math.min(100, progressPercent))}%`;
    }

    if (accountsStat) {
        accountsStat.textContent = `${accountsCompleted} / ${accountsTotal}`;
    }
    if (discoveredStat) {
        discoveredStat.textContent = String(ordersDiscovered);
    }
    if (matchedStat) {
        matchedStat.textContent = String(matchedOrders);
    }
    if (savedStat) {
        savedStat.textContent = `${ordersSaved} / ${ordersFailed}`;
    }

    const requestParts = [
        request.cookie_id ? `账号 ${request.cookie_id}` : '全部账号',
        request.max_orders ? `最多同步 ${request.max_orders} 单` : '',
        request.fetch_details === false ? '仅基础信息' : '含订单详情',
        request.start_date && request.end_date ? `时间范围 ${request.start_date} 至 ${request.end_date}` : '',
    ].filter(Boolean);
    const metaParts = [
        requestParts.join(' · '),
        job?.started_at ? `开始于 ${job.started_at}` : '',
        job?.finished_at ? `结束于 ${job.finished_at}` : '',
    ].filter(Boolean);
    if (metaText) {
        metaText.textContent = metaParts.join(' · ') || '尚未开始任务';
    }

    const currentParts = [];
    if (job?.current_account) {
        currentParts.push(`当前账号: ${job.current_account}`);
    }
    if (job?.current_order_id) {
        currentParts.push(`当前订单: ${job.current_order_id}`);
    }
    if (ordersProcessed > 0 || ordersSkipped > 0) {
        currentParts.push(`已处理 ${ordersProcessed} 单，跳过 ${ordersSkipped} 单`);
    }
    if (currentText) {
        if (matchedOrders > 0 && ordersProcessed > 0) {
            currentParts.unshift(`范围内进度: ${ordersProcessed} / ${matchedOrders}`);
        }
        currentText.textContent = currentParts.join(' · ');
    }

    if (warningsWrap && warningsContainer) {
        if (warnings.length > 0) {
            warningsWrap.style.display = '';
            warningsContainer.innerHTML = warnings.map(message => `
                <div class="border rounded-3 bg-white px-3 py-2 text-muted small">
                    ${escapeHtml(message)}
                </div>
            `).join('');
        } else {
            warningsWrap.style.display = 'none';
            warningsContainer.innerHTML = '';
        }
    }

    setOrderHistorySyncFormDisabled(status === 'pending' || status === 'running');
}

async function openOrderHistorySyncModal() {
    try {
        const modalElement = document.getElementById('orderHistorySyncModal');
        if (!modalElement) return;

        orderHistorySyncModalInstance = bootstrap.Modal.getOrCreateInstance(modalElement);

        const accounts = await fetchOrderSyncAccounts(true);
        const select = document.getElementById('orderHistorySyncCookieId');
        renderOrderAccountOptions(select, accounts, { includeAllOption: true });

        const pageFilterValue = document.getElementById('orderCookieFilter')?.value || '';
        const startDateInput = document.getElementById('orderHistorySyncStartDate');
        const endDateInput = document.getElementById('orderHistorySyncEndDate');
        const maxOrdersInput = document.getElementById('orderHistorySyncMaxOrders');
        const fetchDetailsInput = document.getElementById('orderHistorySyncFetchDetails');

        if (startDateInput && !startDateInput.value) {
            startDateInput.value = getRelativeBeijingDateInputValue(-30);
        }
        if (endDateInput && !endDateInput.value) {
            endDateInput.value = getRelativeBeijingDateInputValue(0);
        }
        if (maxOrdersInput && !maxOrdersInput.value) {
            maxOrdersInput.value = '120';
        }
        if (fetchDetailsInput && !activeOrderHistorySyncJobId) {
            fetchDetailsInput.checked = true;
        }

        if (select && !activeOrderHistorySyncJobId) {
            select.value = pageFilterValue || '';
        }

        if (activeOrderHistorySyncJobId) {
            try {
                await fetchOrderHistorySyncStatus(activeOrderHistorySyncJobId, { silentToast: true });
            } catch (error) {
                if (activeOrderHistorySyncJobId) {
                    throw error;
                }
            }
        }

        if (!activeOrderHistorySyncJobId) {
            resetOrderHistorySyncProgress();
        }

        orderHistorySyncModalInstance.show();
    } catch (error) {
        console.error('打开历史订单同步弹窗失败:', error);
        showToast('加载历史同步配置失败', 'danger');
    }
}

async function startOrderHistorySync() {
    try {
        const cookieId = document.getElementById('orderHistorySyncCookieId')?.value || '';
        const startDate = document.getElementById('orderHistorySyncStartDate')?.value || '';
        const endDate = document.getElementById('orderHistorySyncEndDate')?.value || '';
        const maxOrders = parseInt(document.getElementById('orderHistorySyncMaxOrders')?.value || '120', 10);
        const fetchDetails = Boolean(document.getElementById('orderHistorySyncFetchDetails')?.checked);

        if (!startDate || !endDate) {
            showToast('请选择开始日期和结束日期', 'warning');
            return;
        }
        if (startDate > endDate) {
            showToast('开始日期不能晚于结束日期', 'warning');
            return;
        }
        if (!Number.isFinite(maxOrders) || maxOrders < 1 || maxOrders > 500) {
            showToast('最多同步单数需在 1 到 500 之间', 'warning');
            return;
        }

        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>创建任务中';
        }

        const response = await fetch(`${apiBase}/api/orders/history-sync`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                cookie_id: cookieId || null,
                start_date: startDate,
                end_date: endDate,
                max_orders: maxOrders,
                fetch_details: fetchDetails,
            })
        });

        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success || !result.data) {
            throw new Error(result.detail || result.message || '创建历史订单同步任务失败');
        }

        activeOrderHistorySyncJobId = result.data.job_id;
        orderHistorySyncNotifiedJobId = '';
        renderOrderHistorySyncJob(result.data);
        scheduleOrderHistorySyncPolling(activeOrderHistorySyncJobId);
        showToast('历史订单同步已开始', 'success');
    } catch (error) {
        console.error('创建历史订单同步任务失败:', error);
        showToast(error.message || '创建历史订单同步任务失败', 'danger');
        setOrderHistorySyncFormDisabled(false);
    } finally {
        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn && !startBtn.disabled) {
            startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始同步';
        }
    }
}

async function fetchOrderHistorySyncStatus(jobId, options = {}) {
    if (!jobId) return null;

    const { silentToast = false } = options;
    const response = await fetch(`${apiBase}/api/orders/history-sync/${jobId}`, {
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
    });

    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.success || !result.data) {
        if (response.status === 404) {
            activeOrderHistorySyncJobId = '';
            stopOrderHistorySyncPolling();
            resetOrderHistorySyncProgress();
        }
        throw new Error(result.detail || result.message || '获取历史订单同步状态失败');
    }

    const job = result.data;
    activeOrderHistorySyncJobId = job.job_id || activeOrderHistorySyncJobId;
    renderOrderHistorySyncJob(job);

    const status = String(job?.status || '').toLowerCase();
    if (status === 'pending' || status === 'running') {
        scheduleOrderHistorySyncPolling(job.job_id);
    } else {
        stopOrderHistorySyncPolling();

        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn) {
            startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始同步';
        }

        if (!silentToast && orderHistorySyncNotifiedJobId !== job.job_id) {
            orderHistorySyncNotifiedJobId = job.job_id;
            if (status === 'completed') {
                showToast(job.message || '历史订单同步完成', 'success');
            } else if (status === 'failed') {
                showToast(job.error || job.message || '历史订单同步失败', 'danger');
            } else if (status === 'cancelled') {
                showToast(job.message || '历史订单同步已取消', 'warning');
            }
            await refreshOrdersData();
        }
    }

    return job;
}

async function cancelOrderHistorySync() {
    if (!activeOrderHistorySyncJobId) {
        showToast('当前没有可取消的历史同步任务', 'warning');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/api/orders/history-sync/${activeOrderHistorySyncJobId}/cancel`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success || !result.data) {
            throw new Error(result.detail || result.message || '取消历史订单同步失败');
        }

        stopOrderHistorySyncPolling();
        renderOrderHistorySyncJob(result.data);
        orderHistorySyncNotifiedJobId = result.data.job_id || orderHistorySyncNotifiedJobId;
        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn) {
            startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始同步';
        }
        showToast(result.data.message || '历史订单同步已取消', 'warning');
        await refreshOrdersData();
    } catch (error) {
        console.error('取消历史订单同步失败:', error);
        showToast(error.message || '取消历史订单同步失败', 'danger');
    }
}

// 清空订单筛选条件
function clearOrderFilters() {
    const searchInput = document.getElementById('orderSearchInput');
    const statusFilter = document.getElementById('orderStatusFilter');
    const cookieFilter = document.getElementById('orderCookieFilter');

    if (searchInput) searchInput.value = '';
    if (statusFilter) statusFilter.value = '';
    if (cookieFilter) cookieFilter.value = '';

    filterOrders();
    showToast('筛选条件已清空', 'info');
}

// 显示订单详情
async function showOrderDetail(orderId) {
    try {
        const order = allOrdersData.find(o => o.order_id === orderId);
        if (!order) {
            showToast('订单不存在', 'warning');
            return;
        }

        // 创建模态框内容
        const safeOrderId = escapeHtml(order.order_id || '');
        const safeItemId = escapeHtml(order.item_id || '未知');
        const safeBuyerId = escapeHtml(order.buyer_id || '未知');
        const safeBuyerNick = escapeHtml(order.buyer_nick || '未知');
        const safeCookieId = escapeHtml(order.cookie_id || '未知');
        const safeSpecName = escapeHtml(order.spec_name || '无');
        const safeSpecValue = escapeHtml(order.spec_value || '无');
        const safeSpecName2 = escapeHtml(order.spec_name_2 || '无');
        const safeSpecValue2 = escapeHtml(order.spec_value_2 || '无');
        const safeQuantity = escapeHtml(order.quantity || '1');
        const safeAmount = escapeHtml(formatOrderAmountDisplay(order.amount));
        const safePlatformCreatedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.platform_created_at));
        const safePlatformPaidAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.platform_paid_at));
        const safePlatformCompletedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.platform_completed_at));
        const safeCreatedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.created_at));
        const safeUpdatedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.updated_at));
        const safeStatusText = escapeHtml(getOrderStatusText(order.order_status));
        const safePendingConfirmError = escapeHtml(order.pending_confirm_error || '');
        const pendingConfirmUnits = Number(order.pending_confirm_units || 0);

        const modalContent = `
            <div class="modal fade" id="orderDetailModal" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="bi bi-receipt-cutoff me-2"></i>
                                订单详情
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="row">
                                <div class="col-md-6">
                                    <h6>基本信息</h6>
                                    <table class="table table-sm">
                                        <tr><td>订单ID</td><td>${safeOrderId}</td></tr>
                                        <tr><td>商品ID</td><td>${safeItemId}</td></tr>
                                        <tr><td>买家ID</td><td>${safeBuyerId}</td></tr>
                                        <tr><td>买家昵称</td><td>${safeBuyerNick}</td></tr>
                                        <tr><td>Cookie账号</td><td>${safeCookieId}</td></tr>
                                        <tr><td>订单状态</td><td><span class="badge ${getOrderStatusClass(order.order_status)}">${safeStatusText}</span></td></tr>
                                        ${safePendingConfirmError ? `<tr><td>补确认状态</td><td><span class="badge bg-warning-subtle text-warning-emphasis">待补确认${pendingConfirmUnits ? ` × ${pendingConfirmUnits}` : ''}</span><div class="small text-warning mt-1">${safePendingConfirmError}</div></td></tr>` : ''}
                                    </table>
                                </div>
                                <div class="col-md-6">
                                    <h6>商品信息</h6>
                                    <table class="table table-sm">
                                        <tr><td>规格1名称</td><td>${safeSpecName}</td></tr>
                                        <tr><td>规格1值</td><td>${safeSpecValue}</td></tr>
                                        <tr><td>规格2名称</td><td>${safeSpecName2}</td></tr>
                                        <tr><td>规格2值</td><td>${safeSpecValue2}</td></tr>
                                        <tr><td>数量</td><td>${safeQuantity}</td></tr>
                                        <tr><td>金额</td><td>${safeAmount}</td></tr>
                                    </table>
                                </div>
                            </div>
                            <div class="row mt-3">
                                <div class="col-12">
                                    <h6>时间信息</h6>
                                    <table class="table table-sm">
                                        <tr><td>平台下单时间</td><td>${safePlatformCreatedAt}</td></tr>
                                        <tr><td>平台付款时间</td><td>${safePlatformPaidAt}</td></tr>
                                        <tr><td>平台完成时间</td><td>${safePlatformCompletedAt}</td></tr>
                                        <tr><td>入库时间</td><td>${safeCreatedAt}</td></tr>
                                        <tr><td>更新时间</td><td>${safeUpdatedAt}</td></tr>
                                    </table>
                                </div>
                            </div>
                            <div class="row mt-3">
                                <div class="col-12">
                                    <h6>商品详情</h6>
                                    <div id="itemDetailContent">
                                        <div class="text-center">
                                            <div class="spinner-border spinner-border-sm" role="status">
                                                <span class="visually-hidden">加载中...</span>
                                            </div>
                                            <span class="ms-2">正在加载商品详情...</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 移除已存在的模态框
        const existingModal = document.getElementById('orderDetailModal');
        if (existingModal) {
            existingModal.remove();
        }

        // 添加新模态框到页面
        document.body.insertAdjacentHTML('beforeend', modalContent);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('orderDetailModal'));
        modal.show();

        // 异步加载商品详情
        if (order.item_id) {
            loadItemDetailForOrder(order.item_id, order.cookie_id);
        }

    } catch (error) {
        console.error('显示订单详情失败:', error);
        showToast('显示订单详情失败', 'danger');
    }
}

// 为订单加载商品详情
async function loadItemDetailForOrder(itemId, cookieId) {
    try {
        const token = localStorage.getItem('auth_token');

        // 尝试从数据库获取商品信息
        let response = await fetch(`${apiBase}/items/${cookieId}/${itemId}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const content = document.getElementById('itemDetailContent');
        if (!content) return;

        if (response.ok) {
            const data = await response.json();
            const item = data.item;
            const safeTitle = escapeHtml(item.item_title || '商品标题未知');
            const safeDescription = escapeHtml(item.item_description || '暂无描述');
            const safeCategory = escapeHtml(item.item_category || '未知');
            const safePrice = escapeHtml(item.item_price || '未知');
            const safeDetail = escapeHtml(item.item_detail || '');

            content.innerHTML = `
                <div class="card">
                    <div class="card-body">
                        <h6 class="card-title">${safeTitle}</h6>
                        <p class="card-text">${safeDescription}</p>
                        <div class="row">
                            <div class="col-md-6">
                                <small class="text-muted">分类：${safeCategory}</small>
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">价格：${safePrice}</small>
                            </div>
                        </div>
                        ${item.item_detail ? `
                            <div class="mt-2">
                                <small class="text-muted">详情：</small>
                                <div class="border p-2 mt-1" style="max-height: 200px; overflow-y: auto;">
                                    <small>${safeDetail}</small>
                                </div>
                            </div>
                        ` : ''}
                    </div>
                </div>
            `;
        } else {
            content.innerHTML = `
                <div class="alert alert-warning">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    无法获取商品详情信息
                </div>
            `;
        }
    } catch (error) {
        console.error('加载商品详情失败:', error);
        const content = document.getElementById('itemDetailContent');
        if (content) {
            content.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    加载商品详情失败：${escapeHtml(error.message || '未知错误')}
                </div>
            `;
        }
    }
}

// 删除订单
async function deleteOrder(orderId) {
    try {
        const confirmed = confirm(`确定要删除订单吗？\n\n订单ID: ${orderId}\n\n此操作不可撤销！`);
        if (!confirmed) {
            return;
        }

        const response = await fetch(`${apiBase}/api/orders/${orderId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            showToast('订单删除成功', 'success');
            // 刷新列表
            await refreshOrdersData();
        } else {
            const error = await response.text();
            showToast(`删除失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('删除订单失败:', error);
        showToast('删除订单失败', 'danger');
    }
}

// 批量删除订单
async function batchDeleteOrders() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    if (checkboxes.length === 0) {
        showToast('请先选择要删除的订单', 'warning');
        return;
    }

    const orderIds = Array.from(checkboxes).map(cb => cb.value);
    const confirmed = confirm(`确定要删除选中的 ${orderIds.length} 个订单吗？\n\n此操作不可撤销！`);

    if (!confirmed) return;

    try {
        let successCount = 0;
        let failCount = 0;

        for (const orderId of orderIds) {
            try {
                const response = await fetch(`${apiBase}/api/orders/${orderId}`, {
                    method: 'DELETE',
                    headers: {
                        'Authorization': `Bearer ${authToken}`
                    }
                });

                if (response.ok) {
                    successCount++;
                } else {
                    failCount++;
                }
            } catch (error) {
                failCount++;
            }
        }

        if (successCount > 0) {
            showToast(`成功删除 ${successCount} 个订单${failCount > 0 ? `，${failCount} 个失败` : ''}`,
                     failCount > 0 ? 'warning' : 'success');
            await refreshOrdersData();
        } else {
            showToast('批量删除失败', 'danger');
        }

    } catch (error) {
        console.error('批量删除订单失败:', error);
        showToast('批量删除订单失败', 'danger');
    }
}

// 手动发货订单
async function manualDeliverOrder(orderId) {
    try {
        const confirmed = confirm(`确定要手动发货此订单吗？\n\n订单ID: ${orderId}\n\n系统将根据发货规则自动匹配发货内容并发送给买家。`);
        if (!confirmed) {
            return;
        }

        showToast('正在执行发货...', 'info');

        const response = await fetch(`${apiBase}/api/orders/${orderId}/deliver`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (response.ok) {
            if (result.delivered) {
                showToast(`发货成功！\n${result.message}`, 'success');
                // 刷新今日发货统计
                refreshTodayDeliveryCount();
            } else {
                showToast(`发货失败: ${result.message}`, 'warning');
            }
            // 刷新订单列表
            await refreshOrdersData();
        } else {
            showToast(`发货失败: ${result.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('手动发货失败:', error);
        showToast('手动发货失败: ' + error.message, 'danger');
    }
}

// 手动补确认发货（只调用平台确认，不重复发送卡券）
async function retryOrderPlatformConfirm(orderId) {
    try {
        const confirmed = confirm(`确定要补确认此订单吗？\n\n订单ID: ${orderId}\n\n只会调用闲鱼平台确认发货接口，不会重复发送卡券/发货内容。`);
        if (!confirmed) {
            return;
        }

        showToast('正在补确认发货...', 'info');

        const response = await fetch(`${apiBase}/api/orders/${orderId}/confirm-retry`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (response.ok) {
            if (result.confirmed) {
                showToast(result.message || '补确认成功', 'success');
                refreshTodayDeliveryCount();
            } else if (result.success) {
                showToast(result.message || '没有待补确认记录', 'info');
            } else {
                showToast(`补确认失败: ${result.message || '未知错误'}`, 'warning');
            }
            await refreshOrdersData();
        } else {
            showToast(`补确认失败: ${result.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('补确认发货失败:', error);
        showToast('补确认发货失败: ' + error.message, 'danger');
    }
}

// 刷新订单状态
async function refreshOrderStatus(orderId) {
    try {
        showToast('正在刷新订单状态...', 'info');

        const response = await fetch(`${apiBase}/api/orders/${orderId}/refresh`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (response.ok) {
            if (result.updated) {
                showToast(`订单状态已更新: ${getOrderStatusText(result.new_status)}`, 'success');
            } else {
                showToast(result.message || '订单状态无变化', 'info');
            }
            // 刷新订单列表
            await refreshOrdersData();
        } else {
            showToast(`刷新失败: ${result.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('刷新订单状态失败:', error);
        showToast('刷新订单状态失败: ' + error.message, 'danger');
    }
}

// 切换全选订单
function toggleSelectAllOrders(checkbox) {
    const orderCheckboxes = document.querySelectorAll('.order-checkbox');
    orderCheckboxes.forEach(cb => {
        cb.checked = checkbox.checked;
    });

    updateOrderBatchButtons();
}

// 更新批量操作按钮状态
function updateOrderBatchButtons() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    const batchDeleteBtn = document.getElementById('batchDeleteOrdersBtn');
    const batchRefreshBtn = document.getElementById('batchRefreshOrdersBtn');

    const hasSelection = checkboxes.length > 0;

    if (batchDeleteBtn) {
        batchDeleteBtn.disabled = !hasSelection;
    }
    if (batchRefreshBtn) {
        batchRefreshBtn.disabled = !hasSelection;
    }
}

// 批量刷新订单状态
async function batchRefreshOrders() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    if (checkboxes.length === 0) {
        showToast('请先选择要刷新的订单', 'warning');
        return;
    }

    const orderIds = Array.from(checkboxes).map(cb => cb.value);
    const confirmed = confirm(`确定要刷新选中的 ${orderIds.length} 个订单状态吗？\n\n这可能需要一些时间...`);

    if (!confirmed) return;

    showToast(`正在刷新 ${orderIds.length} 个订单状态...`, 'info');

    let successCount = 0;
    let failCount = 0;

    for (const orderId of orderIds) {
        try {
            const response = await fetch(`${apiBase}/api/orders/${orderId}/refresh`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                successCount++;
            } else {
                failCount++;
            }
        } catch (error) {
            console.error(`刷新订单 ${orderId} 失败:`, error);
            failCount++;
        }
    }

    // 刷新订单列表
    await refreshOrdersData();

    if (failCount === 0) {
        showToast(`成功刷新 ${successCount} 个订单状态`, 'success');
    } else {
        showToast(`刷新完成: ${successCount} 成功, ${failCount} 失败`, 'warning');
    }
}


// 页面加载完成后初始化订单搜索功能
document.addEventListener('DOMContentLoaded', function() {
    // 延迟初始化，确保DOM完全加载
    setTimeout(() => {
        initOrdersSearch();

        const orderHistorySyncModal = document.getElementById('orderHistorySyncModal');
        if (orderHistorySyncModal) {
            orderHistorySyncModal.addEventListener('hidden.bs.modal', () => {
                stopOrderHistorySyncPolling();
            });
        }

        // 绑定复选框变化事件
        document.addEventListener('change', function(e) {
            if (e.target.classList.contains('order-checkbox')) {
                updateOrderBatchButtons();
            }
        });

        document.addEventListener('click', function(e) {
            const actionButton = e.target.closest('.order-action-btn');
            if (!actionButton) return;

            const orderId = actionButton.dataset.orderId;
            const action = actionButton.dataset.orderAction;
            if (!orderId || !action) return;

            if (action === 'deliver') {
                manualDeliverOrder(orderId);
            } else if (action === 'confirm-retry') {
                retryOrderPlatformConfirm(orderId);
            } else if (action === 'refresh') {
                refreshOrderStatus(orderId);
            } else if (action === 'detail') {
                showOrderDetail(orderId);
            } else if (action === 'delete') {
                deleteOrder(orderId);
            }
        });
    }, 100);
});

// ================================
// 用户管理功能
// ================================

// 加载用户管理页面
async function loadUserManagement() {
    console.log('加载用户管理页面');

    // 检查管理员权限
    try {
        const response = await fetch(`${apiBase}/verify`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            if (!result.is_admin) {
                showToast('您没有权限访问用户管理功能', 'danger');
                showSection('dashboard'); // 跳转回仪表盘
                return;
            }
        } else {
            showToast('权限验证失败', 'danger');
            return;
        }
    } catch (error) {
        console.error('权限验证失败:', error);
        showToast('权限验证失败', 'danger');
        return;
    }

    // 加载数据
    await loadUserSystemStats();
    await loadUsers();
}

// 加载用户系统统计信息
async function loadUserSystemStats() {
    try {
        const token = localStorage.getItem('auth_token');

        // 获取用户统计
        const usersResponse = await fetch('/admin/users', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (usersResponse.ok) {
            const usersData = await usersResponse.json();
            document.getElementById('totalUsers').textContent = usersData.users.length;
        }

        // 获取Cookie统计
        const cookiesResponse = await fetch(`${apiBase}/admin/data/cookies`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (cookiesResponse.ok) {
            const cookiesData = await cookiesResponse.json();
            document.getElementById('totalUserCookies').textContent = cookiesData.data ? cookiesData.data.length : 0;
        }

        // 获取卡券统计
        const cardsResponse = await fetch(`${apiBase}/admin/data/cards`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (cardsResponse.ok) {
            const cardsData = await cardsResponse.json();
            document.getElementById('totalUserCards').textContent = cardsData.data ? cardsData.data.length : 0;
        }

    } catch (error) {
        console.error('加载系统统计失败:', error);
    }
}

// 加载用户列表
async function loadUsers() {
    const loadingDiv = document.getElementById('loadingUsers');
    const usersListDiv = document.getElementById('usersList');
    const noUsersDiv = document.getElementById('noUsers');

    // 显示加载状态
    loadingDiv.style.display = 'block';
    usersListDiv.style.display = 'none';
    noUsersDiv.style.display = 'none';

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/admin/users', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            loadingDiv.style.display = 'none';

            if (data.users && data.users.length > 0) {
                usersListDiv.style.display = 'block';
                displayUsers(data.users);
            } else {
                noUsersDiv.style.display = 'block';
            }
        } else {
            throw new Error('获取用户列表失败');
        }
    } catch (error) {
        console.error('加载用户列表失败:', error);
        loadingDiv.style.display = 'none';
        noUsersDiv.style.display = 'block';
        showToast('加载用户列表失败', 'danger');
    }
}

// 显示用户列表
function displayUsers(users) {
    const usersListDiv = document.getElementById('usersList');
    usersListDiv.innerHTML = '';

    users.forEach(user => {
        const userCard = createUserCard(user);
        usersListDiv.appendChild(userCard);
    });
}

// 创建用户卡片
function createUserCard(user) {
    const col = document.createElement('div');
    col.className = 'col-md-6 col-lg-4 mb-3';

    // 使用is_admin字段判断是否为管理员
    const isAdmin = user.is_admin === true;
    const badgeClass = isAdmin ? 'bg-danger' : 'bg-primary';
    const badgeText = isAdmin ? '管理员' : '普通用户';

    // 获取当前登录用户的ID
    let currentUserId = null;
    try {
        const userInfo = JSON.parse(localStorage.getItem('user_info') || '{}');
        currentUserId = userInfo.user_id;
    } catch (e) {
        console.error('解析用户信息失败:', e);
    }
    const isSelf = user.id === currentUserId;

    col.innerHTML = `
        <div class="card user-card h-100">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <h6 class="card-title mb-0">${user.username}</h6>
                    <span class="badge ${badgeClass}">${badgeText}</span>
                </div>
                <p class="card-text text-muted small">
                    <i class="bi bi-envelope me-1"></i>${user.email || '未设置邮箱'}
                </p>
                <p class="card-text text-muted small">
                    <i class="bi bi-calendar me-1"></i>注册时间：${formatDateTime(user.created_at)}
                </p>
                <div class="d-flex justify-content-between align-items-center">
                    <small class="text-muted">
                        Cookie数: ${user.cookie_count || 0} |
                        卡券数: ${user.card_count || 0}
                    </small>
                    <div class="btn-group btn-group-sm">
                        ${!isSelf ? `
                            <button class="btn ${isAdmin ? 'btn-warning' : 'btn-outline-success'}"
                                    onclick="toggleUserAdmin('${user.id}', '${user.username}', ${!isAdmin})"
                                    title="${isAdmin ? '取消管理员权限' : '设置为管理员'}">
                                <i class="bi ${isAdmin ? 'bi-person-dash' : 'bi-person-check'}"></i>
                            </button>
                            <button class="btn btn-outline-danger" onclick="deleteUser('${user.id}', '${user.username}')">
                                <i class="bi bi-trash"></i>
                            </button>
                        ` : `
                            <span class="badge bg-secondary">当前用户</span>
                        `}
                    </div>
                </div>
            </div>
        </div>
    `;

    return col;
}

// 切换用户管理员状态
async function toggleUserAdmin(userId, username, setAdmin) {
    const action = setAdmin ? '设置为管理员' : '取消管理员权限';

    if (!confirm(`确定要将用户 "${username}" ${action}吗？`)) {
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/users/${userId}/admin-status?is_admin=${setAdmin}`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            const data = await response.json();
            showToast(data.message || `用户已${action}`, 'success');

            // 刷新用户列表
            await loadUsers();
        } else {
            const errorData = await response.json();
            showToast(`操作失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('更新用户权限失败:', error);
        showToast('更新用户权限失败', 'danger');
    }
}

// 全局变量用于存储当前要删除的用户信息
let currentDeleteUserId = null;
let currentDeleteUserName = null;
let deleteUserModal = null;

// 删除用户
function deleteUser(userId, username) {
    // 存储要删除的用户信息
    currentDeleteUserId = userId;
    currentDeleteUserName = username;

    // 初始化模态框（如果还没有初始化）
    if (!deleteUserModal) {
        deleteUserModal = new bootstrap.Modal(document.getElementById('deleteUserModal'));
    }

    // 显示确认模态框
    deleteUserModal.show();
}

// 确认删除用户
async function confirmDeleteUser() {
    if (!currentDeleteUserId) return;

    try {
        const token = localStorage.getItem('auth_token');

        const response = await fetch(`/admin/users/${currentDeleteUserId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            deleteUserModal.hide();
            showToast(data.message || '用户删除成功', 'success');

            // 刷新页面数据
            await loadUserSystemStats();
            await loadUsers();
        } else {
            const errorData = await response.json();
            showToast(`删除失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('删除用户失败:', error);
        showToast('删除用户失败', 'danger');
    } finally {
        // 清理状态
        currentDeleteUserId = null;
        currentDeleteUserName = null;
    }
}

// 刷新用户列表
async function refreshUsers() {
    await loadUserSystemStats();
    await loadUsers();
    showToast('用户列表已刷新', 'success');
}

// ================================
// 数据管理功能
// ================================

// 全局变量
let currentTable = '';
let currentData = [];

// 表的中文描述
const tableDescriptions = {
    'users': '用户表',
    'cookies': 'Cookie账号表',
    'cookie_status': 'Cookie状态表',
    'keywords': '关键字表',
    'item_replay': '指定商品回复表',
    'default_replies': '默认回复表',
    'default_reply_records': '默认回复记录表',
    'ai_reply_settings': 'AI回复设置表',
    'ai_conversations': 'AI对话历史表',
    'ai_item_cache': 'AI商品信息缓存表',
    'item_info': '商品信息表',
    'message_notifications': '消息通知表',
    'cards': '卡券表',
    'delivery_rules': '发货规则表',
    'notification_channels': '通知渠道表',
    'user_settings': '用户设置表',
    'system_settings': '系统设置表',
    'email_verifications': '邮箱验证表',
    'captcha_codes': '验证码表',
    'orders': '订单表'
};

// 加载数据管理页面
async function loadDataManagement() {
    console.log('加载数据管理页面');

    // 检查管理员权限
    try {
        const response = await fetch(`${apiBase}/verify`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            if (!result.is_admin) {
                showToast('您没有权限访问数据管理功能', 'danger');
                showSection('dashboard'); // 跳转回仪表盘
                return;
            }
        } else {
            showToast('权限验证失败', 'danger');
            return;
        }
    } catch (error) {
        console.error('权限验证失败:', error);
        showToast('权限验证失败', 'danger');
        return;
    }

    // 重置状态
    currentTable = '';
    currentData = [];

    // 重置界面
    showNoTableSelected();

    // 重置表格选择器
    const tableSelect = document.getElementById('tableSelect');
    if (tableSelect) {
        tableSelect.value = '';
    }
}

// 显示未选择表格状态
function showNoTableSelected() {
    document.getElementById('loadingTable').style.display = 'none';
    document.getElementById('noTableSelected').style.display = 'block';
    document.getElementById('noTableData').style.display = 'none';
    document.getElementById('tableContainer').style.display = 'none';

    // 重置统计信息
    document.getElementById('recordCount').textContent = '-';
    document.getElementById('tableTitle').innerHTML = '<i class="bi bi-table"></i> 数据表';

    // 禁用按钮
    document.getElementById('clearBtn').disabled = true;
}

// 显示加载状态
function showLoading() {
    document.getElementById('loadingTable').style.display = 'block';
    document.getElementById('noTableSelected').style.display = 'none';
    document.getElementById('noTableData').style.display = 'none';
    document.getElementById('tableContainer').style.display = 'none';
}

// 显示无数据状态
function showNoData() {
    document.getElementById('loadingTable').style.display = 'none';
    document.getElementById('noTableSelected').style.display = 'none';
    document.getElementById('noTableData').style.display = 'block';
    document.getElementById('tableContainer').style.display = 'none';
}

// 加载表数据
async function loadTableData() {
    const tableSelect = document.getElementById('tableSelect');
    const selectedTable = tableSelect.value;

    if (!selectedTable) {
        showNoTableSelected();
        return;
    }

    currentTable = selectedTable;
    showLoading();

    const token = localStorage.getItem('auth_token');

    try {
        const response = await fetch(`/admin/data/${selectedTable}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (data.success) {
            currentData = data.data;
            displayTableData(data.data, data.columns);
            updateTableInfo(selectedTable, data.data.length);
        } else {
            showToast('加载数据失败: ' + data.message, 'danger');
            showNoData();
        }
    } catch (error) {
        console.error('加载数据失败:', error);
        showToast('加载数据失败', 'danger');
        showNoData();
    }
}

// 显示表格数据
function displayTableData(data, columns) {
    if (!data || data.length === 0) {
        showNoData();
        return;
    }

    // 显示表格容器
    document.getElementById('loadingTable').style.display = 'none';
    document.getElementById('noTableSelected').style.display = 'none';
    document.getElementById('noTableData').style.display = 'none';
    document.getElementById('tableContainer').style.display = 'block';

    // 生成表头（添加操作列）
    const tableHeaders = document.getElementById('tableHeaders');
    const headerHtml = columns.map(col => `<th>${col}</th>`).join('') + '<th width="100">操作</th>';
    tableHeaders.innerHTML = headerHtml;

    // 生成表格内容（添加删除按钮）
    const tableBody = document.getElementById('tableBody');
    tableBody.innerHTML = data.map((row, index) => {
        const dataCells = columns.map(col => {
            let value = row[col];
            if (value === null || value === undefined) {
                value = '<span class="text-muted">NULL</span>';
            } else if (typeof value === 'string' && value.length > 50) {
                value = `<span title="${escapeHtml(value)}">${escapeHtml(value.substring(0, 50))}...</span>`;
            } else {
                value = escapeHtml(String(value));
            }
            return `<td>${value}</td>`;
        }).join('');

        // 添加操作列（删除按钮）
        const recordId = row.id || row.user_id || index;
        const actionCell = `<td>
            <button class="btn btn-danger btn-sm" onclick="deleteRecordByIndex(${index})" title="删除记录">
                <i class="bi bi-trash"></i>
            </button>
        </td>`;

        return `<tr>${dataCells}${actionCell}</tr>`;
    }).join('');
}

// HTML转义函数
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 更新表格信息
function updateTableInfo(tableName, recordCount) {
    const description = tableDescriptions[tableName] || tableName;
    document.getElementById('tableTitle').innerHTML = `<i class="bi bi-table"></i> ${description}`;
    document.getElementById('recordCount').textContent = recordCount;

    // 启用清空按钮
    document.getElementById('clearBtn').disabled = false;
}

// 刷新表格数据
function refreshTableData() {
    if (currentTable) {
        loadTableData();
        showToast('数据已刷新', 'success');
    } else {
        showToast('请先选择数据表', 'warning');
    }
}

// 导出表格数据
async function exportTableData() {
    if (!currentTable || !currentData || currentData.length === 0) {
        showToast('没有可导出的数据', 'warning');
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/data/${currentTable}/export`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = `${currentTable}_${new Date().toISOString().slice(0, 10)}.xlsx`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showToast('数据导出成功', 'success');
        } else {
            showToast('导出失败', 'danger');
        }
    } catch (error) {
        console.error('导出数据失败:', error);
        showToast('导出数据失败', 'danger');
    }
}

// 清空表格数据
async function clearTableData() {
    if (!currentTable) {
        showToast('请先选择数据表', 'warning');
        return;
    }

    const description = tableDescriptions[currentTable] || currentTable;
    const confirmed = confirm(`确定要清空 "${description}" 的所有数据吗？\n\n此操作不可撤销！`);

    if (!confirmed) return;

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/data/${currentTable}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            showToast(data.message || '数据清空成功', 'success');
            // 重新加载数据
            loadTableData();
        } else {
            const errorData = await response.json();
            showToast(`清空失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('清空数据失败:', error);
        showToast('清空数据失败', 'danger');
    }
}

// 删除记录相关变量
let currentDeleteId = null;
let deleteRecordModal = null;

// 初始化删除记录模态框
function initDeleteRecordModal() {
    if (!deleteRecordModal) {
        deleteRecordModal = new bootstrap.Modal(document.getElementById('deleteRecordModal'));
    }
}

// 通过索引删除记录
function deleteRecordByIndex(index) {
    console.log('deleteRecordByIndex被调用，index:', index);
    console.log('currentData:', currentData);
    console.log('当前currentTable:', currentTable);

    if (!currentData || index >= currentData.length) {
        console.error('无效的索引或数据不存在');
        showToast('删除失败：数据不存在', 'danger');
        return;
    }

    const record = currentData[index];
    console.log('获取到的record:', record);

    deleteRecord(record, index);
}

// 删除记录
function deleteRecord(record, index) {
    console.log('deleteRecord被调用');
    console.log('record:', record);
    console.log('index:', index);
    console.log('当前currentTable:', currentTable);

    initDeleteRecordModal();

    // 尝试多种方式获取记录ID
    currentDeleteId = record.id || record.user_id || record.cookie_id || record.keyword_id ||
                     record.card_id || record.item_id || record.order_id || index;

    console.log('设置currentDeleteId为:', currentDeleteId);
    console.log('record的所有字段:', Object.keys(record));
    console.log('record的所有值:', record);

    // 显示记录信息
    const deleteRecordInfo = document.getElementById('deleteRecordInfo');
    deleteRecordInfo.innerHTML = '';

    Object.keys(record).forEach(key => {
        const div = document.createElement('div');
        div.innerHTML = `<strong>${key}:</strong> ${record[key] || '-'}`;
        deleteRecordInfo.appendChild(div);
    });

    deleteRecordModal.show();
}

// 确认删除记录
async function confirmDeleteRecord() {
    console.log('confirmDeleteRecord被调用');
    console.log('currentDeleteId:', currentDeleteId);
    console.log('currentTable:', currentTable);

    if (!currentDeleteId || !currentTable) {
        console.error('缺少必要参数:', { currentDeleteId, currentTable });
        showToast('删除失败：缺少必要参数', 'danger');
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const url = `/admin/data/${currentTable}/${currentDeleteId}`;
        console.log('发送删除请求到:', url);

        const response = await fetch(url, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        console.log('删除响应状态:', response.status);

        if (response.ok) {
            const data = await response.json();
            console.log('删除成功响应:', data);
            deleteRecordModal.hide();
            showToast(data.message || '删除成功', 'success');
            loadTableData(); // 重新加载数据
        } else {
            const errorData = await response.json();
            console.error('删除失败响应:', errorData);
            showToast(`删除失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('删除记录失败:', error);
        showToast('删除记录失败: ' + error.message, 'danger');
    }
}

// ================================
// 系统日志管理功能
// ================================
let logAutoRefreshInterval = null;
let currentLogLevel = '';
let currentLogCenterTab = 'system';
let taskLogRows = [];
let taskLogCookieOptionsLoaded = false;

// 加载系统日志
async function loadSystemLogs() {
    const token = localStorage.getItem('auth_token');
    const lines = document.getElementById('logLines').value;
    const level = currentLogLevel;

    const loadingDiv = document.getElementById('loadingSystemLogs');
    const logContainer = document.getElementById('systemLogContainer');
    const noLogsDiv = document.getElementById('noSystemLogs');

    loadingDiv.style.display = 'block';
    logContainer.style.display = 'none';
    noLogsDiv.style.display = 'none';

    let url = `/admin/logs?lines=${lines}`;
    if (level) {
        url += `&level=${level}`;
    }

    try {
        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        loadingDiv.style.display = 'none';

        if (data.logs && data.logs.length > 0) {
            displaySystemLogs(data.logs);
            updateLogInfo(data);
            logContainer.style.display = 'block';
        } else {
            noLogsDiv.style.display = 'block';
        }

        // 更新最后更新时间
        document.getElementById('logLastUpdate').textContent =
            '最后更新: ' + new Date().toLocaleTimeString('zh-CN');
    } catch (error) {
        console.error('加载日志失败:', error);
        loadingDiv.style.display = 'none';
        noLogsDiv.style.display = 'block';
        showToast('加载日志失败', 'danger');
    }
}

// 显示系统日志
function displaySystemLogs(logs) {
    const logContainer = document.getElementById('systemLogContainer');
    logContainer.innerHTML = '';

    // 反转日志数组，让最新的日志显示在最上面
    const reversedLogs = [...logs].reverse();

    reversedLogs.forEach(log => {
        const logLine = document.createElement('div');
        logLine.className = 'log-entry';

        // 根据日志级别添加颜色类
        if (log.includes('| INFO |')) {
            logLine.classList.add('INFO');
        } else if (log.includes('| WARNING |')) {
            logLine.classList.add('WARNING');
        } else if (log.includes('| ERROR |')) {
            logLine.classList.add('ERROR');
        } else if (log.includes('| DEBUG |')) {
            logLine.classList.add('DEBUG');
        } else if (log.includes('| CRITICAL |')) {
            logLine.classList.add('CRITICAL');
        }

        logLine.textContent = log;
        logContainer.appendChild(logLine);
    });

    // 自动滚动到顶部（显示最新日志）
    scrollLogToTop();
}

// 更新日志信息
function updateLogInfo(data) {
    document.getElementById('logFileName').textContent = data.log_file || '-';
    document.getElementById('logDisplayLines').textContent = data.total_lines || '-';
}

// 按级别过滤日志
function filterLogsByLevel(level) {
    currentLogLevel = level;

    // 更新过滤按钮状态
    document.querySelectorAll('.filter-badge').forEach(badge => {
        badge.classList.remove('active');
    });
    document.querySelector(`[data-level="${level}"]`).classList.add('active');

    // 更新当前过滤显示
    const filterText = level ? level.toUpperCase() : '全部';
    document.getElementById('logCurrentFilter').textContent = filterText;

    // 重新加载日志
    loadSystemLogs();
}

// 切换日志自动刷新
function toggleLogAutoRefresh() {
    const autoRefresh = document.getElementById('autoRefreshLogs');
    const label = document.getElementById('autoRefreshLogLabel');
    const icon = document.getElementById('autoRefreshLogIcon');

    if (autoRefresh.checked) {
        // 开启自动刷新
        logAutoRefreshInterval = setInterval(loadSystemLogs, 5000); // 每5秒刷新
        label.textContent = '开启 (5s)';
        icon.style.display = 'inline';
        icon.classList.add('auto-refresh-indicator');
    } else {
        // 关闭自动刷新
        if (logAutoRefreshInterval) {
            clearInterval(logAutoRefreshInterval);
            logAutoRefreshInterval = null;
        }
        label.textContent = '关闭';
        icon.style.display = 'none';
        icon.classList.remove('auto-refresh-indicator');
    }
}

// 滚动到日志顶部
function scrollLogToTop() {
    const logContainer = document.getElementById('systemLogContainer');
    logContainer.scrollTop = 0;
}

// 滚动到日志底部
function scrollLogToBottom() {
    const logContainer = document.getElementById('systemLogContainer');
    logContainer.scrollTop = logContainer.scrollHeight;
}

// 切换日志中心页签
function switchLogCenterTab(tabName) {
    currentLogCenterTab = tabName === 'task' ? 'task' : 'system';

    const systemTab = document.getElementById('systemLogsTab');
    const taskTab = document.getElementById('taskLogsTab');
    const systemPane = document.getElementById('systemLogsPane');
    const taskPane = document.getElementById('taskLogsPane');

    if (systemTab) {
        systemTab.classList.toggle('active', currentLogCenterTab === 'system');
        systemTab.setAttribute('aria-selected', currentLogCenterTab === 'system' ? 'true' : 'false');
    }
    if (taskTab) {
        taskTab.classList.toggle('active', currentLogCenterTab === 'task');
        taskTab.setAttribute('aria-selected', currentLogCenterTab === 'task' ? 'true' : 'false');
    }
    if (systemPane) systemPane.classList.toggle('active', currentLogCenterTab === 'system');
    if (taskPane) taskPane.classList.toggle('active', currentLogCenterTab === 'task');

    if (currentLogCenterTab === 'task') {
        initTaskLogsPane();
    } else if (document.getElementById('systemLogContainer')) {
        loadSystemLogs();
    }
}

async function initTaskLogsPane() {
    await loadTaskLogCookieOptions();
    if (!taskLogRows.length) {
        await loadTaskLogs();
    } else {
        renderTaskLogs();
    }
}

async function loadTaskLogCookieOptions() {
    const select = document.getElementById('taskLogCookieFilter');
    if (!select || taskLogCookieOptionsLoaded) return;

    try {
        const response = await fetch(`${apiBase}/cookies/details`, {
            headers: { 'Authorization': `Bearer ${getAuthToken()}` }
        });
        if (!response.ok) return;

        const accounts = await response.json();
        const currentValue = select.value;
        select.innerHTML = '<option value="">全部账号</option>';
        (Array.isArray(accounts) ? accounts : []).forEach(account => {
            const option = document.createElement('option');
            option.value = account.id || '';
            const remark = account.remark ? `（${account.remark}）` : '';
            option.textContent = `${account.id || '未知账号'}${remark}`;
            select.appendChild(option);
        });
        select.value = currentValue;
        taskLogCookieOptionsLoaded = true;
    } catch (error) {
        console.warn('加载任务日志账号筛选失败:', error);
    }
}

async function loadTaskLogs() {
    const loading = document.getElementById('loadingTaskLogs');
    const table = document.getElementById('taskLogsTable');
    const empty = document.getElementById('noTaskLogs');
    const type = document.getElementById('taskLogTypeFilter')?.value || 'all';
    const cookieId = document.getElementById('taskLogCookieFilter')?.value || '';
    const limit = document.getElementById('taskLogLimit')?.value || '100';

    if (loading) loading.style.display = 'block';
    if (table) table.style.display = 'none';
    if (empty) empty.style.display = 'none';

    try {
        const query = new URLSearchParams({ task_type: type, limit, offset: '0' });
        if (cookieId) query.set('cookie_id', cookieId);

        const response = await fetch(`${apiBase}/api/task-logs?${query.toString()}`, {
            headers: { 'Authorization': `Bearer ${getAuthToken()}` }
        });
        if (!response.ok) {
            throw new Error(`任务日志加载失败: HTTP ${response.status}`);
        }
        const data = await response.json();
        taskLogRows = (data.data || []).map(log => normalizeTaskLog(log)).sort((a, b) => {
            const timeA = new Date(a.created_at || 0).getTime();
            const timeB = new Date(b.created_at || 0).getTime();
            return timeB - timeA;
        });

        renderTaskLogs();
        const lastUpdate = document.getElementById('taskLogsLastUpdate');
        if (lastUpdate) lastUpdate.textContent = '最后更新: ' + new Date().toLocaleTimeString('zh-CN');
    } catch (error) {
        console.error('加载任务日志失败:', error);
        if (empty) {
            empty.style.display = 'block';
            empty.innerHTML = `
                <i class="bi bi-exclamation-triangle" style="font-size: 3rem; color: #dc3545;"></i>
                <p class="mt-2 text-danger mb-0">加载任务日志失败：${escapeHtml(error.message || '未知错误')}</p>
            `;
        }
        showToast('加载任务日志失败', 'danger');
    } finally {
        if (loading) loading.style.display = 'none';
    }
}

function normalizeTaskLog(log) {
    const type = log.task_type || 'other_task';
    return {
        ...log,
        task_type: type,
        task_label: log.task_label || getTaskTypeLabel(type),
        display_object: log.object_id || log.order_id || log.item_id || '-',
        created_at: log.created_at || log.updated_at || ''
    };
}

function renderTaskLogs() {
    const tbody = document.getElementById('taskLogsTableBody');
    const table = document.getElementById('taskLogsTable');
    const empty = document.getElementById('noTaskLogs');
    const statusFilter = document.getElementById('taskLogStatusFilter')?.value || 'all';
    if (!tbody || !table || !empty) return;

    const filtered = taskLogRows.filter(log => matchTaskLogStatusFilter(log.status, statusFilter));
    updateTaskLogStats(filtered);

    if (!filtered.length) {
        table.style.display = 'none';
        empty.style.display = 'block';
        empty.innerHTML = `
            <i class="bi bi-journal-text" style="font-size: 3rem; color: #ccc;"></i>
            <p class="mt-2 text-muted mb-0">暂无任务日志</p>
        `;
        tbody.innerHTML = '';
        return;
    }

    tbody.innerHTML = filtered.map(log => `
        <tr>
            <td class="text-nowrap">${escapeHtml(formatTaskLogTime(log.created_at))}</td>
            <td>${renderTaskTypeBadge(log.task_type, log.task_label)}</td>
            <td><span class="task-log-account" title="${escapeHtml(log.cookie_id || '')}">${escapeHtml(log.cookie_id || '-')}</span></td>
            <td><span class="task-log-object" title="${escapeHtml(log.display_object || '')}">${escapeHtml(log.display_object || '-')}</span></td>
            <td>${escapeHtml(log.buyer_nick || log.buyer_id || '-')}</td>
            <td>${renderTaskStatusBadge(log.status)}</td>
            <td class="task-log-message" title="${escapeHtml(log.message || '')}">${escapeHtml(log.message || '-')}</td>
            <td><span class="task-log-batch" title="${escapeHtml(log.batch_id || '')}">${escapeHtml(shortenTaskLogBatchId(log.batch_id))}</span></td>
        </tr>
    `).join('');
    table.style.display = 'table';
    empty.style.display = 'none';
}

function matchTaskLogStatusFilter(status, filter) {
    if (!filter || filter === 'all') return true;
    const value = String(status || '').toLowerCase();
    if (filter === 'success') return ['success', 'partial_success', 'already_rated', 'already_red_flower'].includes(value);
    if (filter === 'skipped') return ['skipped', 'missing_template'].includes(value);
    if (filter === 'cookie_expired') return ['cookie_expired', 'session_expired'].includes(value);
    if (filter === 'processing') return ['processing', 'started', 'running'].includes(value);
    return value === filter;
}

function updateTaskLogStats(rows) {
    const total = rows.length;
    const success = rows.filter(row => matchTaskLogStatusFilter(row.status, 'success')).length;
    const failed = rows.filter(row => String(row.status || '').toLowerCase() === 'failed').length;
    const skipped = rows.filter(row => matchTaskLogStatusFilter(row.status, 'skipped') || matchTaskLogStatusFilter(row.status, 'cookie_expired')).length;

    setTextContent('taskLogTotalCount', total);
    setTextContent('taskLogSuccessCount', success);
    setTextContent('taskLogFailedCount', failed);
    setTextContent('taskLogSkippedCount', skipped);
}

function setTextContent(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = String(value);
}

function getTaskTypeLabel(type) {
    const labels = {
        auto_comment: '自动评价',
        auto_red_flower: '求小红花',
        item_polish: '商品擦亮',
        login_renew: '登录续期',
        cookie_refresh: 'Cookie刷新',
        other_task: '其他任务'
    };
    return labels[type] || '其他任务';
}

function renderTaskTypeBadge(type, label) {
    const config = {
        auto_comment: ['chat-heart', 'task-type-comment'],
        auto_red_flower: ['flower1', 'task-type-red-flower'],
        item_polish: ['stars', 'task-type-polish'],
        login_renew: ['shield-check', 'task-type-login'],
        cookie_refresh: ['arrow-repeat', 'task-type-cookie'],
        other_task: ['box-seam', 'task-type-other']
    };
    const [icon, cls] = config[type] || config.other_task;
    return `<span class="task-type-badge ${cls}"><i class="bi bi-${icon}"></i>${escapeHtml(label || getTaskTypeLabel(type))}</span>`;
}

function renderTaskStatusBadge(status) {
    const value = String(status || '').toLowerCase();
    const map = {
        success: ['bg-success', '成功'],
        failed: ['bg-danger', '失败'],
        skipped: ['bg-secondary', '已跳过'],
        cookie_expired: ['bg-warning text-dark', 'Cookie过期'],
        session_expired: ['bg-warning text-dark', 'Session过期'],
        processing: ['bg-info', '处理中'],
        started: ['bg-info', '处理中'],
        running: ['bg-info', '运行中'],
        partial_success: ['bg-primary', '部分成功'],
        already_rated: ['bg-success', '已评价'],
        already_red_flower: ['bg-success', '已求小红花'],
        missing_template: ['bg-secondary', '缺少模板']
    };
    const [cls, text] = map[value] || ['bg-secondary', status || '未知'];
    return `<span class="badge ${cls}">${escapeHtml(text)}</span>`;
}

function formatTaskLogTime(value) {
    if (!value) return '-';
    const date = parseUtcDateTime(value) || new Date(value);
    if (Number.isNaN(date.getTime())) return value;
    return date.toLocaleString('zh-CN');
}

function shortenTaskLogBatchId(batchId) {
    if (!batchId) return '-';
    const value = String(batchId);
    return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value;
}

// 打开日志导出模态框
function openLogExportModal() {
    const modalElement = document.getElementById('exportLogModal');
    if (!modalElement) {
        console.warn('未找到导出日志模态框元素');
        return;
    }

    resetLogFileModalState();
    const modal = new bootstrap.Modal(modalElement);
    modal.show();
    loadLogFileList();
}

function resetLogFileModalState() {
    const loading = document.getElementById('logFileLoading');
    const list = document.getElementById('logFileList');
    const empty = document.getElementById('logFileEmpty');
    const error = document.getElementById('logFileError');

    if (loading) loading.classList.remove('d-none');
    if (list) list.innerHTML = '';
    if (empty) empty.classList.add('d-none');
    if (error) {
        error.classList.add('d-none');
        error.textContent = '';
    }
}

async function loadLogFileList() {
    const token = localStorage.getItem('auth_token');
    const loading = document.getElementById('logFileLoading');
    const list = document.getElementById('logFileList');
    const empty = document.getElementById('logFileEmpty');
    const error = document.getElementById('logFileError');

    if (!loading || !list || !empty || !error) {
        console.warn('日志文件列表元素缺失');
        return;
    }

    loading.classList.remove('d-none');
    list.innerHTML = '';
    empty.classList.add('d-none');
    error.classList.add('d-none');
    error.textContent = '';

    try {
        const response = await fetch(`${apiBase}/admin/log-files`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        loading.classList.add('d-none');

        if (!response.ok) {
            const message = await response.text();
            error.classList.remove('d-none');
            error.textContent = `加载日志文件失败: ${message || response.status}`;
            return;
        }

        const data = await response.json();
        if (!data.success) {
            error.classList.remove('d-none');
            error.textContent = data.message || '加载日志文件失败';
            return;
        }

        const files = data.files || [];
        if (files.length === 0) {
            empty.classList.remove('d-none');
            return;
        }

        files.forEach(file => {
            const item = document.createElement('div');
            item.className = 'list-group-item d-flex justify-content-between align-items-start flex-wrap gap-3';

            const info = document.createElement('div');
            info.className = 'me-auto';

            const title = document.createElement('div');
            title.className = 'fw-semibold';
            title.textContent = file.name || '未知文件';

            const meta = document.createElement('div');
            meta.className = 'small text-muted';
            const sizeText = typeof file.size === 'number' ? formatFileSize(file.size) : '未知大小';
            const timeText = file.modified_at ? formatLogTimestamp(file.modified_at) : '-';
            meta.textContent = `大小: ${sizeText} · 更新时间: ${timeText}`;

            info.appendChild(title);
            info.appendChild(meta);

            const actions = document.createElement('div');
            actions.className = 'd-flex align-items-center gap-2';

            const downloadBtn = document.createElement('button');
            downloadBtn.type = 'button';
            downloadBtn.className = 'btn btn-sm btn-outline-primary';
            downloadBtn.innerHTML = '<i class="bi bi-download me-1"></i>下载';
            downloadBtn.onclick = () => downloadLogFile(file.name, downloadBtn);

            actions.appendChild(downloadBtn);

            item.appendChild(info);
            item.appendChild(actions);

            list.appendChild(item);
        });
    } catch (err) {
        console.error('加载日志文件失败:', err);
        loading.classList.add('d-none');
        error.classList.remove('d-none');
        error.textContent = '加载日志文件失败，请稍后重试';
    }
}

function refreshLogFileList() {
    resetLogFileModalState();
    loadLogFileList();
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    if (!Number.isFinite(bytes)) return '未知大小';

    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const size = bytes / Math.pow(1024, index);
    return `${size.toFixed(index === 0 ? 0 : 2)} ${units[index]}`;
}

function formatLogTimestamp(isoString) {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) {
        return '-';
    }
    return date.toLocaleString('zh-CN', { hour12: false });
}

async function downloadLogFile(fileName, buttonEl) {
    if (!fileName) {
        showToast('日志文件名无效', 'warning');
        return;
    }

    const token = localStorage.getItem('auth_token');
    if (!token) {
        showToast('请先登录后再导出日志', 'warning');
        return;
    }

    let originalHtml = '';
    if (buttonEl) {
        originalHtml = buttonEl.innerHTML;
        buttonEl.disabled = true;
        buttonEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>下载中...';
    }

    try {
        const response = await fetch(`${apiBase}/admin/logs/export?file=${encodeURIComponent(fileName)}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (!response.ok) {
            const message = await response.text();
            showToast(`日志下载失败: ${message || response.status}`, 'danger');
            return;
        }

        let downloadName = fileName;
        const contentDisposition = response.headers.get('content-disposition');
        if (contentDisposition) {
            const match = contentDisposition.match(/filename="?([^"]+)"?/i);
            if (match && match[1]) {
                downloadName = decodeURIComponent(match[1]);
            }
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = downloadName;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        window.URL.revokeObjectURL(url);

        showToast('日志下载成功', 'success');
    } catch (error) {
        console.error('下载日志文件失败:', error);
        showToast('下载日志文件失败，请稍后重试', 'danger');
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.innerHTML = originalHtml || '<i class="bi bi-download me-1"></i>下载';
        }
    }
}

// ================================
// 风控日志管理功能
// ================================
let currentRiskLogStatus = '';
let currentRiskLogOffset = 0;
const riskLogLimit = 100;
let currentRiskSliderStatsRequestId = 0;

function getRiskSliderStatsRange() {
    const activeButton = document.querySelector('#riskSliderRangeFilter .risk-slider-range-btn.is-active');
    return activeButton?.dataset.range || 'all';
}

function getRiskSliderStatsRangeLabel(rangeValue = 'all') {
    switch (String(rangeValue || '').trim().toLowerCase()) {
        case 'today':
            return '当日';
        case '7d':
            return '近 7 天';
        default:
            return '所有';
    }
}

function onRiskSliderRangeChange(rangeValue = 'all') {
    document.querySelectorAll('#riskSliderRangeFilter .risk-slider-range-btn').forEach((button) => {
        button.classList.toggle('is-active', button.dataset.range === rangeValue);
    });
    const cookieId = document.getElementById('riskLogCookieFilter')?.value || '';
    loadRiskControlSliderStats(cookieId);
}

function setRiskControlSliderStatsLoading(scopeLabel = '全部账号') {
    const scopeElement = document.getElementById('riskSliderScope');
    const successRateElement = document.getElementById('riskSliderSuccessRate');
    const attemptCountElement = document.getElementById('riskSliderAttemptCount');
    const successCountElement = document.getElementById('riskSliderSuccessCount');
    const failureCountElement = document.getElementById('riskSliderFailureCount');
    const recentSuccessElement = document.getElementById('riskSliderRecentSuccess');
    const recentFailureElement = document.getElementById('riskSliderRecentFailure');

    if (scopeElement) scopeElement.textContent = scopeLabel;
    if (successRateElement) successRateElement.textContent = '--';
    if (attemptCountElement) attemptCountElement.textContent = '统计中...';
    if (successCountElement) successCountElement.textContent = '--';
    if (failureCountElement) failureCountElement.textContent = '--';
    if (recentSuccessElement) recentSuccessElement.textContent = '--';
    if (recentFailureElement) recentFailureElement.textContent = '--';
}

function renderRiskControlSliderStats(stats = {}) {
    const scopeElement = document.getElementById('riskSliderScope');
    const successRateElement = document.getElementById('riskSliderSuccessRate');
    const attemptCountElement = document.getElementById('riskSliderAttemptCount');
    const successCountElement = document.getElementById('riskSliderSuccessCount');
    const failureCountElement = document.getElementById('riskSliderFailureCount');
    const recentSuccessElement = document.getElementById('riskSliderRecentSuccess');
    const recentFailureElement = document.getElementById('riskSliderRecentFailure');

    const totalSessions = Number(stats.total_sessions ?? stats.total_attempts ?? 0);
    const successCount = Number(stats.success_count || 0);
    const failureCount = Number(stats.failure_count || 0);
    const processingCount = Number(stats.processing_count || 0);
    const completedSessions = Number(stats.completed_sessions || (successCount + failureCount));
    const successRate = Number.isFinite(Number(stats.success_rate)) ? Number(stats.success_rate).toFixed(1) : '0.0';
    const hasData = Boolean(stats.has_data || totalSessions > 0);
    const recentSuccessText = formatBeijingDateTime(stats.recent_success);
    const recentFailureText = formatBeijingDateTime(stats.recent_failure);
    const rangeLabel = stats.range_label || getRiskSliderStatsRangeLabel(stats.selected_range || getRiskSliderStatsRange());
    let attemptSummary = stats.summary_text || '暂无滑块验证记录';

    if (hasData) {
        if (rangeLabel === '所有') {
            attemptSummary = `累计滑块相关记录 ${totalSessions} 次`;
        } else {
            attemptSummary = `${rangeLabel}滑块相关记录 ${totalSessions} 次`;
        }
        if (processingCount > 0) {
            attemptSummary += `，进行中 ${processingCount} 次`;
        }
    }

    if (scopeElement) scopeElement.textContent = stats.scope_label || '全部账号';
    if (successRateElement) successRateElement.textContent = completedSessions > 0 ? `${successRate}%` : '--';
    if (attemptCountElement) attemptCountElement.textContent = attemptSummary;
    if (successCountElement) successCountElement.textContent = String(successCount);
    if (failureCountElement) failureCountElement.textContent = String(failureCount);
    if (recentSuccessElement) recentSuccessElement.textContent = recentSuccessText;
    if (recentFailureElement) recentFailureElement.textContent = recentFailureText;
}

async function loadRiskControlSliderStats(cookieId = '') {
    const token = localStorage.getItem('auth_token');
    const scopeLabel = cookieId || '全部账号';
    const rangeValue = getRiskSliderStatsRange();
    const rangeLabel = getRiskSliderStatsRangeLabel(rangeValue);
    const requestId = ++currentRiskSliderStatsRequestId;

    setRiskControlSliderStatsLoading(scopeLabel);

    try {
        const params = new URLSearchParams();
        if (cookieId) {
            params.set('cookie_id', cookieId);
        }
        params.set('range_key', rangeValue);
        const url = `/admin/slider-verification-stats?${params.toString()}`;

        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (requestId !== currentRiskSliderStatsRequestId) {
            return;
        }

        if (response.ok && data.success) {
            renderRiskControlSliderStats(data.data || {});
            return;
        }

        renderRiskControlSliderStats({
            scope_label: scopeLabel,
            total_sessions: 0,
            success_count: 0,
            failure_count: 0,
            processing_count: 0,
            completed_sessions: 0,
            success_rate: 0,
            recent_success: '--',
            recent_failure: '--',
            summary_text: rangeValue === 'all' ? '暂无滑块验证记录' : `${rangeLabel}暂无滑块验证记录`,
            selected_range: rangeValue,
            range_label: rangeLabel,
            has_data: false
        });
    } catch (error) {
        console.error('加载滑块验证统计失败:', error);
        if (requestId !== currentRiskSliderStatsRequestId) {
            return;
        }
        renderRiskControlSliderStats({
            scope_label: scopeLabel,
            total_sessions: 0,
            success_count: 0,
            failure_count: 0,
            processing_count: 0,
            completed_sessions: 0,
            success_rate: 0,
            recent_success: '--',
            recent_failure: '--',
            summary_text: rangeValue === 'all' ? '暂无滑块验证记录' : `${rangeLabel}暂无滑块验证记录`,
            selected_range: rangeValue,
            range_label: rangeLabel,
            has_data: false
        });
    }
}

function getRiskLogFilters() {
    return {
        cookieId: document.getElementById('riskLogCookieFilter')?.value || '',
        eventType: document.getElementById('riskLogEventTypeFilter')?.value || '',
        triggerScene: document.getElementById('riskLogTriggerSceneFilter')?.value || '',
        dateFrom: document.getElementById('riskLogDateFrom')?.value || '',
        dateTo: document.getElementById('riskLogDateTo')?.value || '',
        sessionId: (document.getElementById('riskLogSessionFilter')?.value || '').trim(),
        processingStatus: currentRiskLogStatus,
        limit: parseInt(document.getElementById('riskLogLimit')?.value, 10) || 100,
    };
}

function hasActiveRiskLogFilters(filters = {}) {
    return Boolean(
        filters.cookieId ||
        filters.processingStatus ||
        filters.eventType ||
        filters.triggerScene ||
        filters.dateFrom ||
        filters.dateTo ||
        filters.sessionId
    );
}

async function fetchRiskControlLogsPage(token, {
    cookieId = '',
    processingStatus = '',
    eventType = '',
    triggerScene = '',
    dateFrom = '',
    dateTo = '',
    sessionId = '',
    resultCode = '',
    limit = 100,
    offset = 0,
} = {}) {
    const params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
    });

    if (cookieId) params.set('cookie_id', cookieId);
    if (processingStatus) params.set('processing_status', processingStatus);
    if (eventType) params.set('event_type', eventType);
    if (triggerScene) params.set('trigger_scene', triggerScene);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (sessionId) params.set('session_id', sessionId);
    if (resultCode) params.set('result_code', resultCode);

    const response = await fetch(`/admin/risk-control-logs?${params.toString()}`, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });

    return response.json();
}

function needsClientSideRiskLogFilter(logs, processingStatus) {
    if (!processingStatus || !Array.isArray(logs) || logs.length === 0) {
        return false;
    }

    return logs.some(log => String(log.processing_status || '') !== processingStatus);
}

async function fetchRiskControlLogsWithClientFilter(token, {
    cookieId = '',
    processingStatus = '',
    eventType = '',
    triggerScene = '',
    dateFrom = '',
    dateTo = '',
    sessionId = '',
    resultCode = '',
    limit = 100,
    offset = 0,
} = {}) {
    const batchSize = 500;
    let fetchOffset = 0;
    let total = 0;
    const matchedLogs = [];

    while (true) {
        const pageData = await fetchRiskControlLogsPage(token, {
            cookieId,
            eventType,
            triggerScene,
            dateFrom,
            dateTo,
            sessionId,
            resultCode,
            limit: batchSize,
            offset: fetchOffset
        });

        const pageLogs = Array.isArray(pageData.data) ? pageData.data : [];
        total = pageData.total || total || pageLogs.length;

        matchedLogs.push(...pageLogs.filter(log => String(log.processing_status || '') === processingStatus));

        fetchOffset += pageLogs.length;
        if (pageLogs.length === 0 || fetchOffset >= total) {
            break;
        }
    }

    return {
        success: true,
        data: matchedLogs.slice(offset, offset + limit),
        total: matchedLogs.length,
        limit,
        offset,
        filter_mode: 'client'
    };
}

// 加载风控日志
async function loadRiskControlLogs(offset = 0) {
    const token = localStorage.getItem('auth_token');
    const filters = getRiskLogFilters();
    const cookieId = filters.cookieId;
    const limit = filters.limit;
    currentRiskLogOffset = offset;

    loadRiskControlSliderStats(cookieId);

    const loadingDiv = document.getElementById('loadingRiskLogs');
    const logContainer = document.getElementById('riskLogContainer');
    const noLogsDiv = document.getElementById('noRiskLogs');

    loadingDiv.style.display = 'block';
    logContainer.style.display = 'none';
    noLogsDiv.style.display = 'none';

    try {
        let data = await fetchRiskControlLogsPage(token, {
            ...filters,
            offset,
        });

        if (needsClientSideRiskLogFilter(data.data, filters.processingStatus)) {
            data = await fetchRiskControlLogsWithClientFilter(token, {
                ...filters,
                offset,
            });
        }

        loadingDiv.style.display = 'none';

        if (data.success && data.data && data.data.length > 0) {
            displayRiskControlLogs(data.data);
            updateRiskLogInfo(data);
            updateRiskLogPagination(data);
            logContainer.style.display = 'block';
        } else {
            noLogsDiv.style.display = 'block';
            updateRiskLogInfo({total: 0, data: []});
            updateRiskLogPagination({total: 0});
        }

    } catch (error) {
        console.error('加载风控日志失败:', error);
        loadingDiv.style.display = 'none';
        noLogsDiv.style.display = 'block';
        updateRiskLogPagination({total: 0});
        const countElement = document.getElementById('riskLogCount');
        const paginationInfo = document.getElementById('riskLogPaginationInfo');
        if (countElement) {
            countElement.textContent = '加载失败';
        }
        if (paginationInfo) {
            paginationInfo.textContent = '风控日志加载失败，请重试';
        }
        showToast('加载风控日志失败', 'danger');
    }
}

// 显示风控日志
function getRiskEventCategoryMeta(eventType) {
    const normalizedType = String(eventType || '').trim();

    if (normalizedType === 'unknown') {
        return {
            label: '身份验证',
            className: 'risk-event-category-trigger'
        };
    }

    if (['slider_captcha', 'face_verify', 'sms_verify', 'qr_verify', 'token_expired'].includes(normalizedType)) {
        return {
            label: '风控触发',
            className: 'risk-event-category-trigger'
        };
    }

    if (normalizedType === 'cookie_refresh') {
        return {
            label: 'Cookie刷新',
            className: 'risk-event-category-refresh'
        };
    }

    if (normalizedType === 'password_error') {
        return {
            label: '登录异常',
            className: 'risk-event-category-error'
        };
    }

    return {
        label: normalizedType || '-',
        className: 'risk-event-category-neutral'
    };
}

function getRiskTriggerSceneLabel(triggerScene) {
    const normalizedScene = String(triggerScene || '').trim();
    const sceneLabels = {
        token_refresh: 'Token刷新',
        auto_cookie_refresh: '自动Cookie刷新',
        manual_password_refresh: '手动账密刷新',
        manual_qr_refresh: '手动扫码刷新',
        password_login: '密码登录',
        qr_login: '扫码登录'
    };

    return sceneLabels[normalizedScene] || normalizedScene || '-';
}

function getRiskCaptchaEngineMeta(log = {}) {
    const meta = log && typeof log.event_meta === 'object' && log.event_meta ? log.event_meta : {};
    const normalizedEngine = String(log.captcha_engine || meta.captcha_engine || '').trim().toLowerCase();
    const engineMap = {
        playwright: { label: 'Playwright', className: 'bg-primary' },
        drissionpage: { label: 'Drission', className: 'bg-info text-dark' },
        remote: { label: '远程', className: 'bg-dark' },
        real_mouse: { label: '真实鼠标', className: 'bg-success' },
        manual: { label: '手动', className: 'bg-secondary' }
    };

    if (!normalizedEngine) {
        return { label: '-', className: 'bg-light text-muted border', raw: '' };
    }

    return {
        ...(engineMap[normalizedEngine] || { label: normalizedEngine, className: 'bg-secondary' }),
        raw: normalizedEngine
    };
}

function renderRiskCaptchaEngineCell(log = {}) {
    const engine = getRiskCaptchaEngineMeta(log);
    const title = engine.raw ? `验证引擎: ${engine.raw}` : '暂无验证引擎记录';
    return `<span class="badge ${engine.className}" title="${escapeHtml(title)}">${escapeHtml(engine.label)}</span>`;
}

function formatRiskDuration(durationMs) {
    const value = Number(durationMs);
    if (!Number.isFinite(value) || value <= 0) {
        return '--';
    }
    if (value < 1000) {
        return `${Math.round(value)} ms`;
    }
    if (value < 60000) {
        return `${(value / 1000).toFixed(1)} s`;
    }
    return `${(value / 60000).toFixed(1)} min`;
}

function formatRiskSessionId(sessionId, sessionDisplay = '') {
    const text = String(sessionId || '').trim();
    if (text) {
        return text;
    }
    const fallback = String(sessionDisplay || '').trim();
    return fallback || '--';
}

function renderRiskLogSummaryCell(log) {
    const descriptionText = log.event_description_display || log.event_description || '-';
    const description = escapeHtml(descriptionText);
    const resultCode = log.result_code
        ? `<div class="small text-muted mt-1">结果代码: ${escapeHtml(log.result_code)}</div>`
        : '';
    return `
        <div class="risk-log-summary-cell" title="${description}">${description}</div>
        ${resultCode}
    `;
}

function renderRiskLogOutcomeCell(log) {
    const processingResultText = log.processing_result_display || log.processing_result || '';
    const errorMessageText = log.error_message_display || log.error_message || '';
    const processingResult = processingResultText
        ? `<div class="text-wrap">${escapeHtml(processingResultText)}</div>`
        : '';
    const errorMessage = errorMessageText
        ? `<div class="small text-danger mt-1">${escapeHtml(errorMessageText)}</div>`
        : '';
    const fallbackText = !processingResult && !errorMessage
        ? '<span class="text-muted">-</span>'
        : '';
    return `
        <div class="risk-log-outcome-cell">
            ${processingResult}
            ${errorMessage}
            ${fallbackText}
        </div>
    `;
}

function displayRiskControlLogs(logs) {
    const tableBody = document.getElementById('riskLogTableBody');
    tableBody.innerHTML = '';

    logs.forEach(log => {
        const row = document.createElement('tr');

        // 格式化时间
        const createdAt = formatDateTime(log.created_at);

        // 状态标签
        let statusBadge = '';
        switch(log.processing_status) {
            case 'processing':
                statusBadge = '<span class="badge bg-warning">处理中</span>';
                break;
            case 'success':
                statusBadge = '<span class="badge bg-success">成功</span>';
                break;
            case 'failed':
                statusBadge = '<span class="badge bg-danger">失败</span>';
                break;
            default:
                statusBadge = '<span class="badge bg-secondary">未知</span>';
        }

        const eventCategory = getRiskEventCategoryMeta(log.event_type);
        const eventCategoryBadge = `
            <span
                class="badge risk-event-category-badge ${eventCategory.className}"
                title="原始类型: ${escapeHtml(log.event_type || '-')}"
            >
                ${escapeHtml(eventCategory.label)}
            </span>
        `;
        const triggerSceneLabel = getRiskTriggerSceneLabel(log.trigger_scene);
        const triggerSceneBadge = `
            <span class="badge bg-light text-dark border" title="触发场景: ${escapeHtml(log.trigger_scene || '-')}">
                ${escapeHtml(triggerSceneLabel)}
            </span>
        `;
        const sessionIdDisplay = formatRiskSessionId(log.session_id, log.session_display);
        const sessionTitle = escapeHtml(log.session_id || log.session_display || '-');
        const durationText = formatRiskDuration(log.duration_ms);

        row.innerHTML = `
            <td class="text-nowrap">${createdAt}</td>
            <td class="text-nowrap">${escapeHtml(log.cookie_id || '-')}</td>
            <td class="text-nowrap">${eventCategoryBadge}</td>
            <td class="text-nowrap">${triggerSceneBadge}</td>
            <td>${statusBadge}</td>
            <td class="text-nowrap">${renderRiskCaptchaEngineCell(log)}</td>
            <td class="risk-log-cell-summary">${renderRiskLogSummaryCell(log)}</td>
            <td class="risk-log-cell-outcome">${renderRiskLogOutcomeCell(log)}</td>
            <td class="text-nowrap">${escapeHtml(durationText)}</td>
            <td class="risk-log-cell-session" title="${sessionTitle}">${escapeHtml(sessionIdDisplay)}</td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteRiskControlLog(${log.id})" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        `;

        tableBody.appendChild(row);
    });
}

// 更新风控日志信息
function updateRiskLogInfo(data) {
    const countElement = document.getElementById('riskLogCount');
    const paginationInfo = document.getElementById('riskLogPaginationInfo');
    const hasFilters = hasActiveRiskLogFilters(getRiskLogFilters());
    const total = data.total || 0;
    const currentCount = data.data ? data.data.length : 0;

    if (countElement) {
        countElement.textContent = hasFilters ? `筛选结果: ${total} 条` : `总计: ${total} 条`;
    }

    if (paginationInfo) {
        if (currentCount === 0 || total === 0) {
            paginationInfo.textContent = hasFilters ? `显示第 0-0 条，匹配 0 条记录` : '显示第 0-0 条，共 0 条记录';
            return;
        }

        const start = currentRiskLogOffset + 1;
        const end = Math.min(currentRiskLogOffset + currentCount, total);
        paginationInfo.textContent = hasFilters
            ? `显示第 ${start}-${end} 条，匹配 ${total} 条记录`
            : `显示第 ${start}-${end} 条，共 ${total} 条记录`;
    }
}

// 更新风控日志分页
function updateRiskLogPagination(data) {
    const pagination = document.getElementById('riskLogPagination');
    const limit = parseInt(document.getElementById('riskLogLimit').value);
    const total = data.total || 0;
    const totalPages = Math.ceil(total / limit);
    const currentPage = Math.floor(currentRiskLogOffset / limit) + 1;

    pagination.innerHTML = '';

    if (totalPages <= 1) return;

    // 上一页
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${currentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="loadRiskControlLogs(${(currentPage - 2) * limit})">上一页</a>`;
    pagination.appendChild(prevLi);

    // 页码
    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, currentPage + 2);

    for (let i = startPage; i <= endPage; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === currentPage ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="loadRiskControlLogs(${(i - 1) * limit})">${i}</a>`;
        pagination.appendChild(li);
    }

    // 下一页
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${currentPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="loadRiskControlLogs(${currentPage * limit})">下一页</a>`;
    pagination.appendChild(nextLi);
}

// 按状态过滤风控日志
function filterRiskLogsByStatus(status) {
    currentRiskLogStatus = status;

    // 更新过滤按钮状态
    document.querySelectorAll('.filter-badge[data-status]').forEach(badge => {
        badge.classList.remove('active');
    });
    const activeBadge = document.querySelector(`.filter-badge[data-status="${status}"]`);
    if (activeBadge) {
        activeBadge.classList.add('active');
    }

    // 重新加载日志
    loadRiskControlLogs(0);
}

// 加载账号筛选选项
async function loadCookieFilterOptions() {
    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/admin/cookies', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            const select = document.getElementById('riskLogCookieFilter');

            // 清空现有选项，保留"全部账号"
            select.innerHTML = '<option value="">全部账号</option>';

            if (data.success && data.cookies) {
                data.cookies.forEach(cookie => {
                    const option = document.createElement('option');
                    option.value = cookie.cookie_id;
                    // 优先显示备注，其次显示用户名，都没有则不显示括号
                    const displayName = cookie.nickname || cookie.username || '';
                    option.textContent = displayName ? `${cookie.cookie_id} (${displayName})` : cookie.cookie_id;
                    select.appendChild(option);
                });
            }
        }
    } catch (error) {
        console.error('加载账号选项失败:', error);
    }
}

// 删除风控日志记录
async function deleteRiskControlLog(logId) {
    if (!confirm('确定要删除这条风控日志记录吗？')) {
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/risk-control-logs/${logId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (data.success) {
            showToast('删除成功', 'success');
            loadRiskControlLogs(currentRiskLogOffset);
        } else {
            showToast(data.message || '删除失败', 'danger');
        }
    } catch (error) {
        console.error('删除风控日志失败:', error);
        showToast('删除失败', 'danger');
    }
}

// 清空风控日志
async function clearRiskControlLogs() {
    if (!confirm('确定要清空所有风控日志吗？此操作不可恢复！')) {
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');

        // 调用后端批量清空接口（管理员）
        const response = await fetch('/admin/data/risk_control_logs', {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (response.ok) {
            showToast('风控日志已清空', 'success');
            loadRiskControlLogs(0);
        } else {
            showToast(data.detail || data.message || '清空失败', 'danger');
        }
    } catch (error) {
        console.error('清空风控日志失败:', error);
        showToast('清空失败', 'danger');
    }
}

// ================================
// 商品搜索功能
// ================================
let searchResultsData = [];
let currentSearchPage = 1;
let searchPageSize = 20;
let totalSearchPages = 0;

// 初始化商品搜索功能
function initItemSearch() {
    const searchForm = document.getElementById('itemSearchForm');
    if (searchForm) {
        searchForm.addEventListener('submit', handleItemSearch);
    }
}

// 处理商品搜索
async function handleItemSearch(event) {
    event.preventDefault();

    const keyword = document.getElementById('searchKeyword').value.trim();
    const totalPages = parseInt(document.getElementById('searchTotalPages').value) || 1;
    const pageSize = parseInt(document.getElementById('searchPageSize').value) || 20;

    if (!keyword) {
        showToast('请输入搜索关键词', 'warning');
        return;
    }

    // 显示搜索状态
    showSearchStatus(true);
    hideSearchResults();

    try {
        // 检查是否有有效的cookies账户
        const cookiesCheckResponse = await fetch('/cookies/check', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
            }
        });

        if (cookiesCheckResponse.ok) {
            const cookiesData = await cookiesCheckResponse.json();
            if (!cookiesData.hasValidCookies) {
                showToast('搜索失败：系统中不存在有效的账户信息。请先在Cookie管理中添加有效的闲鱼账户。', 'warning');
                showSearchStatus(false);
                return;
            }
        }

        const token = localStorage.getItem('auth_token');
        
        // 启动会话检查器（在搜索过程中检查是否有验证会话）
        let sessionChecker = null;
        let checkCount = 0;
        const maxChecks = 30; // 最多检查30次（30秒）
        let isSearchCompleted = false; // 标记搜索是否完成
        
        sessionChecker = setInterval(async () => {
            // 如果搜索已完成，停止检查
            if (isSearchCompleted) {
                if (sessionChecker) {
                    clearInterval(sessionChecker);
                    sessionChecker = null;
                }
                return;
            }
            
            try {
                checkCount++;
                const checkResponse = await authenticatedFetch('/api/captcha/sessions');
                const checkData = await checkResponse.json();
                
                if (checkData.sessions && checkData.sessions.length > 0) {
                    for (const session of checkData.sessions) {
                        if (!session.completed) {
                            console.log(`🎨 检测到验证会话: ${session.session_id}`);
                            if (sessionChecker) {
                                clearInterval(sessionChecker);
                                sessionChecker = null;
                            }
                            
                            const accountId = String(session.account_id || currentCookieId || '').trim();
                            if (accountId) {
                                await openRemoteVerificationConsole(accountId);
                                showToast('检测到滑块验证，请在实时验证窗口中完成操作', 'warning');
                            } else {
                                showToast('检测到滑块验证，但无法确定所属账号，请从账号管理打开实时验证', 'danger');
                            }
                            return;
                        }
                    }
                }
                
                // 如果检查次数超过限制，停止检查
                if (checkCount >= maxChecks) {
                    if (sessionChecker) {
                        clearInterval(sessionChecker);
                        sessionChecker = null;
                    }
                }
            } catch (error) {
                console.error('检查验证会话失败:', error);
            }
        }, 1000); // 每秒检查一次
        
        // 使用 Promise 包装，以便使用 finally
        const fetchPromise = authenticatedFetch('/items/search_multiple', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                keyword: keyword,
                total_pages: totalPages
            })
        });

        // 请求完成后，停止会话检查器
        fetchPromise.finally(() => {
            isSearchCompleted = true;
            if (sessionChecker) {
                clearInterval(sessionChecker);
                sessionChecker = null;
                console.log('✅ 搜索完成，已停止会话检查器');
            }
        });

        const response = await fetchPromise;
        console.log('API响应状态:', response.status);

        if (response.ok) {
            const data = await response.json();
            console.log('API返回的完整数据:', data);

            // 检查是否需要滑块验证
            if (data.need_captcha || data.status === 'need_verification') {
                console.log('检测到需要滑块验证');
                showSearchStatus(false);
                
                const accountId = String(data.account_id || currentCookieId || '').trim();
                if (accountId) {
                    await openRemoteVerificationConsole(accountId);
                    showToast('请在实时验证窗口完成操作，然后重新发起搜索', 'warning');
                } else {
                    showToast('检测到滑块验证，请从账号管理打开实时验证', 'danger');
                }
                showNoSearchResults();
                return;
            }

            // 正常搜索结果（无需验证）
            // 修复字段名：使用data.data而不是data.items
            searchResultsData = data.data || [];
            console.log('设置searchResultsData:', searchResultsData);
            console.log('searchResultsData长度:', searchResultsData.length);
            console.log('完整响应数据:', data);

            searchPageSize = pageSize;
            currentSearchPage = 1;
            totalSearchPages = Math.ceil(searchResultsData.length / searchPageSize);

            if (data.error) {
                showToast(`搜索完成，但遇到问题: ${data.error}`, 'warning');
            }

            showSearchStatus(false);
            
            // 确保显示搜索结果
            if (searchResultsData.length > 0) {
            displaySearchResults();
            updateSearchStats(data);
            } else {
                console.warn('搜索结果为空，显示无结果提示');
                showNoSearchResults();
            }
        } else {
            const errorData = await response.json();
            showSearchStatus(false);
            showToast(`搜索失败: ${errorData.detail || '未知错误'}`, 'danger');
            showNoSearchResults();
        }
    } catch (error) {
        console.error('搜索商品失败:', error);
        showSearchStatus(false);
        showToast('搜索商品失败', 'danger');
        showNoSearchResults();
    }
}

// 显示搜索状态
function showSearchStatus(isSearching) {
    const statusDiv = document.getElementById('searchStatus');
    const progressDiv = document.getElementById('searchProgress');

    if (isSearching) {
        statusDiv.style.display = 'block';
        progressDiv.textContent = '正在搜索商品数据...';
    } else {
        statusDiv.style.display = 'none';
    }
}

// 隐藏搜索结果
function hideSearchResults() {
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('searchResultStats').style.display = 'none';
    document.getElementById('noSearchResults').style.display = 'none';
}

// 显示搜索结果
function displaySearchResults() {
    if (searchResultsData.length === 0) {
        showNoSearchResults();
        return;
    }

    const startIndex = (currentSearchPage - 1) * searchPageSize;
    const endIndex = startIndex + searchPageSize;
    const pageItems = searchResultsData.slice(startIndex, endIndex);

    const container = document.getElementById('searchResultsContainer');
    container.innerHTML = '';

    pageItems.forEach(item => {
        const itemCard = createItemCard(item);
        container.appendChild(itemCard);
    });

    updateSearchPagination();
    document.getElementById('searchResults').style.display = 'block';
}

// 创建商品卡片
function createItemCard(item) {
    console.log('createItemCard被调用，item数据:', item);
    console.log('item的所有字段:', Object.keys(item));

    const col = document.createElement('div');
    col.className = 'col-md-6 col-lg-4 col-xl-3 mb-4';

    // 修复字段映射：使用main_image而不是image_url
    const imageUrl = item.main_image || item.image_url || 'https://via.placeholder.com/200x200?text=图片加载失败';
    const wantCount = item.want_count || 0;

    console.log('处理后的数据:', {
        title: item.title,
        price: item.price,
        seller_name: item.seller_name,
        imageUrl: imageUrl,
        wantCount: wantCount,
        url: item.item_url || item.url
    });

    col.innerHTML = `
        <div class="card item-card h-100">
            <img src="${escapeHtml(imageUrl)}" class="item-image" alt="${escapeHtml(item.title)}"
                 onerror="this.src='https://via.placeholder.com/200x200?text=图片加载失败'"
                 style="width: 100%; height: 200px; object-fit: cover; border-radius: 10px;">
            <div class="card-body d-flex flex-column">
                <h6 class="card-title" title="${escapeHtml(item.title)}">
                    ${escapeHtml(item.title.length > 50 ? item.title.substring(0, 50) + '...' : item.title)}
                </h6>
                <div class="price mb-2" style="color: #e74c3c; font-weight: bold; font-size: 1.2em;">
                    ${escapeHtml(item.price)}
                </div>
                <div class="seller-name mb-2" style="color: #6c757d; font-size: 0.9em;">
                    <i class="bi bi-person me-1"></i>
                    ${escapeHtml(item.seller_name)}
                </div>
                ${wantCount > 0 ? `<div class="want-count mb-2">
                    <i class="bi bi-heart-fill me-1" style="color: #ff6b6b;"></i>
                    <span class="badge bg-danger">${wantCount}人想要</span>
                </div>` : ''}
                <div class="mt-auto">
                    <a href="${escapeHtml(item.item_url || item.url)}" target="_blank" class="btn btn-primary btn-sm w-100">
                        <i class="bi bi-eye me-1"></i>查看详情
                    </a>
                </div>
            </div>
        </div>
    `;

    return col;
}

// 更新搜索统计
function updateSearchStats(data) {
    document.getElementById('totalItemsFound').textContent = searchResultsData.length;
    document.getElementById('totalPagesSearched').textContent = data.total_pages || 0;
    document.getElementById('currentDisplayPage').textContent = currentSearchPage;
    document.getElementById('totalDisplayPages').textContent = totalSearchPages;
    document.getElementById('searchResultStats').style.display = 'block';
}

// 更新搜索分页
function updateSearchPagination() {
    const paginationContainer = document.getElementById('searchPagination');
    paginationContainer.innerHTML = '';

    if (totalSearchPages <= 1) return;

    const pagination = document.createElement('nav');
    pagination.innerHTML = `
        <ul class="pagination">
            <li class="page-item ${currentSearchPage === 1 ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="changeSearchPage(${currentSearchPage - 1})">上一页</a>
            </li>
            ${generateSearchPageNumbers()}
            <li class="page-item ${currentSearchPage === totalSearchPages ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="changeSearchPage(${currentSearchPage + 1})">下一页</a>
            </li>
        </ul>
    `;

    paginationContainer.appendChild(pagination);
}

// 生成搜索分页页码
function generateSearchPageNumbers() {
    let pageNumbers = '';
    const maxVisiblePages = 5;
    let startPage = Math.max(1, currentSearchPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalSearchPages, startPage + maxVisiblePages - 1);

    if (endPage - startPage + 1 < maxVisiblePages) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }

    for (let i = startPage; i <= endPage; i++) {
        pageNumbers += `
            <li class="page-item ${i === currentSearchPage ? 'active' : ''}">
                <a class="page-link" href="#" onclick="changeSearchPage(${i})">${i}</a>
            </li>
        `;
    }

    return pageNumbers;
}

// 切换搜索页面
function changeSearchPage(page) {
    if (page < 1 || page > totalSearchPages || page === currentSearchPage) return;

    currentSearchPage = page;
    displaySearchResults();
    updateSearchStats({ total_pages: document.getElementById('totalPagesSearched').textContent });
}

// 显示无搜索结果
function showNoSearchResults() {
    document.getElementById('noSearchResults').style.display = 'block';
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('searchResultStats').style.display = 'none';
}

// 导出搜索结果
function exportSearchResults() {
    if (searchResultsData.length === 0) {
        showToast('没有可导出的搜索结果', 'warning');
        return;
    }

    try {
        // 准备导出数据
        const exportData = searchResultsData.map(item => ({
            '商品标题': item.title,
            '价格': item.price,
            '卖家': item.seller_name,
            '想要人数': item.want_count || 0,
            '商品链接': item.url,
            '图片链接': item.image_url
        }));

        // 转换为CSV格式
        const headers = Object.keys(exportData[0]);
        const csvContent = [
            headers.join(','),
            ...exportData.map(row => headers.map(header => `"${row[header] || ''}"`).join(','))
        ].join('\n');

        // 创建下载链接
        const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', `商品搜索结果_${new Date().toISOString().slice(0, 10)}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        showToast('搜索结果导出成功', 'success');
    } catch (error) {
        console.error('导出搜索结果失败:', error);
        showToast('导出搜索结果失败', 'danger');
    }
}

// ================================
// 版本管理功能
// ================================







// 默认版本号（当无法读取 version.txt 时使用）
const DEFAULT_VERSION = 'v2.0.5';

// 当前本地版本号（动态从 version.txt 读取）
let LOCAL_VERSION = DEFAULT_VERSION;

// 缓存远程版本信息
let remoteVersionInfo = null;
const HOT_UPDATE_STORAGE_KEYS = {
    autoCheckDisabled: 'hot_update_auto_check_disabled',
    ignoredVersion: 'hot_update_ignored_version'
};

function isHotUpdateAutoCheckEnabled() {
    return localStorage.getItem(HOT_UPDATE_STORAGE_KEYS.autoCheckDisabled) !== 'true';
}

function setHotUpdateAutoCheckEnabled(enabled) {
    localStorage.setItem(HOT_UPDATE_STORAGE_KEYS.autoCheckDisabled, enabled ? 'false' : 'true');
}

function getIgnoredHotUpdateVersion() {
    return localStorage.getItem(HOT_UPDATE_STORAGE_KEYS.ignoredVersion) || '';
}

function setIgnoredHotUpdateVersion(version) {
    if (version) {
        localStorage.setItem(HOT_UPDATE_STORAGE_KEYS.ignoredVersion, version);
    }
}

function getHotUpdateTargetVersion(updateInfo = remoteVersionInfo) {
    return updateInfo?.new_version || (updateInfo?.has_update ? updateInfo?.version : '') || '';
}

function shouldSuppressHotUpdateHint(updateInfo = remoteVersionInfo) {
    const targetVersion = getHotUpdateTargetVersion(updateInfo);
    return !isHotUpdateAutoCheckEnabled() || (!!targetVersion && getIgnoredHotUpdateVersion() === targetVersion);
}

function refreshHotUpdateButtonState(updateInfo = remoteVersionInfo) {
    const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');
    const dashboardHotUpdateBtn = document.getElementById('dashboardHotUpdateBtn');
    const dashboardHotUpdateMenuBtn = document.getElementById('dashboardHotUpdateMenuBtn');
    if (!dashboardHotUpdateGroup || !dashboardHotUpdateBtn || !dashboardHotUpdateMenuBtn) return;

    dashboardHotUpdateBtn.disabled = false;
    dashboardHotUpdateBtn.innerHTML = '<i class="bi bi-cloud-download me-1"></i>检查更新';
    dashboardHotUpdateMenuBtn.disabled = false;
    dashboardHotUpdateGroup.classList.remove('has-update', 'is-loading');

    const hasUpdate = Boolean(updateInfo && (updateInfo.has_update || updateInfo.new_version));
    if (!hasUpdate || shouldSuppressHotUpdateHint(updateInfo)) {
        return;
    }

    dashboardHotUpdateGroup.classList.add('has-update');
    dashboardHotUpdateBtn.innerHTML = `<i class="bi bi-cloud-download me-1"></i>有新版本 ${getHotUpdateTargetVersion(updateInfo)}`;
}

function updateHotUpdatePreferenceStatus(message = '', type = 'info') {
    if (message) {
        showToast(message, type === 'success' ? 'success' : 'info');
    }
}

function refreshHotUpdatePreferencesMenu() {
    const autoCheckToggle = document.getElementById('dashboardHotUpdateAutoCheckToggle');
    const ignoredVersionHint = document.getElementById('dashboardHotUpdatePreferenceHint');
    const clearIgnoredBtn = document.getElementById('dashboardClearIgnoredVersionBtn');
    const ignoredVersion = getIgnoredHotUpdateVersion();

    if (autoCheckToggle) {
        autoCheckToggle.textContent = isHotUpdateAutoCheckEnabled() ? '关闭自动检查' : '开启自动检查';
    }

    if (ignoredVersionHint) {
        const autoCheckText = isHotUpdateAutoCheckEnabled() ? '自动检查：已开启' : '自动检查：已关闭';
        ignoredVersionHint.textContent = ignoredVersion
            ? `${autoCheckText} · 已忽略 ${ignoredVersion}`
            : `${autoCheckText} · 当前未忽略任何版本`;
    }

    if (clearIgnoredBtn) {
        clearIgnoredBtn.disabled = !ignoredVersion;
    }
}

function toggleHotUpdateAutoCheck() {
    const nextEnabled = !isHotUpdateAutoCheckEnabled();
    setHotUpdateAutoCheckEnabled(nextEnabled);
    refreshHotUpdatePreferencesMenu();
    refreshHotUpdateButtonState();
    updateHotUpdatePreferenceStatus(
        nextEnabled
            ? '自动检查更新已开启，当前浏览器进入系统时会自动检测'
            : '自动检查更新已关闭，仍可手动点击“检查更新”',
        'success'
    );
}

function clearIgnoredUpdateVersion(showFeedback = true) {
    localStorage.removeItem(HOT_UPDATE_STORAGE_KEYS.ignoredVersion);
    refreshHotUpdatePreferencesMenu();
    refreshHotUpdateButtonState();
    if (showFeedback) {
        updateHotUpdatePreferenceStatus('已清除忽略版本设置', 'success');
    }
}

// 本地版本历史（远程服务禁用时使用）
const LOCAL_VERSION_HISTORY = {
    version: 'v2.0.5',
    intro: '本系统仅供个人学习研究使用，请勿用于商业用途。如有问题或建议，欢迎反馈。',
    versionHistory: [
        {
            version: 'v2.0.5',
            date: '2026-07-10',
            updates: [
                '【修复】API 卡券 GET 请求同样替换动态参数占位符，避免 {order_id} 等 query 参数原样发送导致卡密串用',
                '【修复】编辑 API 卡券时正确格式化嵌套 JSON headers/params，保存时保留表单未覆盖的扩展配置',
                '【修复】退款完成后的“交易关闭”轻量提醒支持按会话 sid 唯一回填，避免订单长期停留在退款中',
                '【修复】本账号作为买家咨询或购买别人商品时，不再触发自动回复或 AI 回复'
            ]
        },
        {
            version: 'v2.0.4',
            date: '2026-07-07',
            updates: [
                '【新功能】新增 noVNC 手动风控接管入口，Docker 环境可通过浏览器远程查看并人工处理滑块、验证码和账号风险提示',
                '【新功能】新增远程滑块服务与统一滑块兜底编排，外部服务可接收验证 URL 并回传有效 x5sec Cookie',
                '【优化】风控日志与滑块统计展示验证引擎来源，便于区分 Playwright、DrissionPage、远程和手动处理链路',
                '【优化】商品发布支持类目提示，参与闲鱼类目推荐并在类目路径查询失败时给出更明确处理建议',
                '【优化】补强订单恢复与待补确认处理，支持按订单信息恢复待补订单并衔接自动发货流程',
                '【修复】严格校验滑块 x5sec 结果，避免无效 Cookie 或未通过滑块的结果被误判为成功',
                '【修复】修复浏览器进程清理、账号任务残留和验证完成后历史截图误报，提升登录/验证收尾稳定性',
                '【文档】整理 README Star History 区块 HTML 缩进，保持文档结构一致'
            ]
        },
        {
            version: 'v2.0.3',
            date: '2026-06-16',
            updates: [
                '【新功能】新增待补确认订单补偿能力，发货后平台确认失败的订单会记录待补确认状态并提供补偿入口',
                '【新功能】在线客服会话新增拉黑入口，便于快速处理异常买家或商品会话',
                '【优化】商品发布支持填写类目提示，参与闲鱼类目推荐并在类目路径查询失败时给出明确处理建议',
                '【优化】会话预览优先显示最新消息，补全客服会话头像昵称，并将客服订单入口跳转到独立订单页',
                '【修复】停止终态订单重复补确认，避免已完成、已关闭等终态订单被重复处理',
                '【文档】精简 README 并拆分部署、配置、使用、FAQ 和发版说明文档'
            ]
        },
        {
            version: 'v2.0.2',
            date: '2026-06-03',
            updates: [
                '【新功能】新增黑名单管理能力，支持按买家、账号和商品维护拦截规则，自动回复、客服手动发送和发货流程会统一识别黑名单',
                '【新功能】重写在线客服为直连闲鱼 IM 会话体验，支持账号连接状态、远程会话列表、历史消息分页和实时消息合并展示',
                '【优化】在线客服三栏界面新增账号连接/断开、IM 来源标识、未读数、加载更多会话和更早消息入口，客服处理更集中',
                '【修复】修复在线客服拉取会话时新建临时 WebSocket 导致主监听连接被挤下线的问题，改为复用主连接按 mid 分发 IM 响应',
                '【修复】补强在线客服远程消息解析，文本、图片、卡片等消息可正常展示，并在 IM 异常时回退本地缓存和订单会话入口',
            ]
        },
        {
            version: 'v2.0.1',
            date: '2026-05-28',
            updates: [
                '【新功能】新增商品发布素材管理与批量发布能力，支持素材集中维护并批量执行发布任务',
                '【新功能】新增历史订单补评价与自动求小红花流程，补齐订单售后运营自动化能力',
                '【新功能】新增统一任务日志中心，集中查看任务执行日志并提升排查效率',
                '【优化】仪表盘公告卡片支持摘要展示并优化卡片布局，同时允许关闭公告提示',
                '【修复】修复在线客服 Web 端自发消息不显示、消息时间 UTC 偏移和系统文案污染买家昵称等问题',
                '【修复】修复滑块验证后 _m_h5_tk 域名落点导致 Token 刷新非法请求，账密登录后优先快照 goofish 域 Token',
                '【修复】修复自动评价接口调用链路，改为本地评价接口并补齐补评任务',
            ]
        },
        {
            version: 'v2.0.0',
            date: '2026-05-19',
            updates: [
                '【重要】本次为大版本升级，包含登录、扫码、滑块验证、Token 刷新、在线客服、商品发布、历史订单同步和数据库结构等重要调整',
                '【新功能】新增商品发布能力，补齐商品发布相关接口、页面与发布工具模块，完善商品运营流程',
                '【新功能】新增在线客服三栏界面，支持会话列表、消息流、账号上下文与实时消息展示，客服处理更集中',
                '【新功能】新增聊天会话持久化与历史补拉能力，补强聊天消息入库、会话恢复与历史消息读取，减少重启后的上下文丢失',
                '【优化】重构扫码登录、账密登录、滑块验证、Session/Profile 复用和 Token 刷新流程，提升复杂风控场景下的恢复稳定性',
                '【优化】新增夜间风控降频与连续失败保护策略，减少异常账号持续触发风控',
                '【修复】修复自动回复并发去重问题，减少重复消息、重复任务或多实例竞争导致的重复回复',
                '【修复】修复订单买家昵称污染，拦截“工作台通知”“等待你发货”“买家”等系统文案写入订单买家昵称，并在订单列表展示时回退真实聊天昵称',
                '【修复】修复自动发货通知买家名错误，发货失败通知优先从订单与聊天记录解析真实买家昵称，避免显示系统标题或固定“买家”',
                '【优化】优化历史订单同步容错，单个账号 Cookie 失效、权限不足或接口异常时跳过该账号并继续同步其他账号，同时返回明确处理建议',
                '【修复】修复停止脚本噪音，停止服务时优先清理项目相关 Node 子进程，减少 shutdown 阶段反复输出 Error: write EPIPE',
                '【优化】补强热更新检测、忽略版本、版本读取和清单生成能力'
            ]
        },
        {
            version: 'v1.9.3',
            date: '2026-04-15',
            updates: [
                '【新功能】新增账号风控保护状态，检测到高风险登录提示时自动禁用账号并同步展示保护状态',
                '【修复】命中“账号存在风险 / 请前往闲鱼客户端处理”等提示后立即停止后续自动登录重试，避免持续触发更强风控',
                '【修复】后端补充账号 status_note 状态说明字段，禁用接口和账号详情接口统一返回保护状态，重新启用账号时自动清空',
                '【优化】账号列表和仪表盘新增风控保护徽标与待恢复统计提示，便于快速识别需要去闲鱼客户端处理的账号'
            ]
        },
        {
            version: 'v1.9.2',
            date: '2026-04-10',
            updates: [
                '【修复】运行态总览统一按 WS / Session / Token / 业务流 四条主链路统计，避免出现 1 / 5 与 0 / 4 混用',
                '【修复】运行态优先读取账号真实活跃实例，临时 XianyuLive 实例不再注册到全局实例表，减少业务消息流误判未就绪',
                '【优化】账号详情运行态总览调整为左侧四个状态卡、右侧链路就绪摘要卡，桌面端信息分区更清晰',
                '【优化】业务消息流补充连接未就绪与恢复中的兜底展示，运行态短时异常时前端自动重试刷新更平滑'
            ]
        },
        {
            version: 'v1.9.1',
            date: '2026-04-10',
            updates: [
                '【新功能】新增业务消息流看门狗，区分心跳包与真实业务包，长时间只有心跳时会主动关闭旧 WebSocket 并触发重连',
                '【新功能】账号运行态新增消息流诊断字段，补充最近非心跳业务包、同步包、真实买家消息与假在线重连时间，便于识别“连接已通但消息停滞”',
                '【优化】仪表盘账号卡片和账号详情页新增“消息流”状态，并将链路就绪判断扩展到业务消息流',
                '【优化】前端对连接中、重连中和短时异常状态增加自动重试刷新，减少运行态展示滞后'
            ]
        },
        {
            version: 'v1.9.0',
            date: '2026-04-08',
            updates: [
                '【新功能】升级账号保活链路与账号诊断能力，账号页按实际链路展示 WS / Session / Token / 轻保活 等运行状态，仪表盘账号卡片新增运行态摘要',
                '【新功能】重写历史订单同步并切换卖家工作台接口，补齐订单平台时间字段链路，支持在前端查看同步入口、状态面板与任务进度',
                '【修复】收紧历史订单同步时间范围与数量限制，降低大范围同步导致的异常与超时风险',
                '【修复】收紧登录表单识别，找不到账号框/密码框时先复检已登录态和验证页；验证类型不明时不再默认按人脸处理，前后端展示与通知统一按实际验证类型显示',
                '【修复】修复账号重新启用后资料被清空的问题，避免恢复启用时覆盖已有配置',
                '【优化】调整自动回复优先级顺序，减少多规则命中时的回复偏差',
                '【新功能】接入 GitHub 公告栏，仪表盘支持展示公告横幅并可点击查看历史公告记录'
            ]
        },
        {
            version: 'v1.8.4',
            date: '2026-04-05',
            updates: [
                '【修复】修复订单详情规格解析失败导致自动发货被阻断的问题（by @82762294）',
                '【优化】滑块验证统计新增当日 / 7天 / 所有范围筛选，统计卡片文案与交互更清晰',
                '【优化】风控日志“处理结果”展示简化，移除前端元数据展开信息，排查更直观',
                '【优化】账号管理入口与说明文案更新，明确扫码登录、账密登录、手动刷新 Cookie 与导入 Cookie 的使用场景'
            ]
        },
        {
            version: 'v1.8.3',
            date: '2026-04-05',
            updates: [
                '【修复】修复有头模式白屏：完整反检测脚本会覆盖浏览器核心API导致页面无法渲染，有头模式改用轻量反检测脚本',
                '【修复】修复自动刷新Session过期导致滑块连败：自动Token刷新改用干净上下文，避免持久化上下文中过期Session数据导致风控升级',
                '【修复】修复刷新模式登录状态假象：新增服务端Session有效性验证，过期时自动清除Cookie并重新登录',
                '【修复】修复持久化上下文页面异常：无登录iframe且无已登录态时自动清除Cookie和缓存并重新加载',
                '【优化】Token预检新增最多3次渐进重试，应对密码登录Cookie在服务端生效延迟',
                '【优化】滑块策略权重调整，降低低成功率conservative策略权重，提高standard策略权重',
                '【优化】滑块第3次及以后重试优先使用学习参数加大抖动变体，增加重试间隔降低反爬触发风险',
                '【优化】密码登录复用完整浏览器画像配置，与captcha验证流程保持一致，自动刷新路径同步启用策略学习'
            ]
        },
        {
            version: 'v1.8.2',
            date: '2026-04-04',
            updates: [
                '【修复】修复 Token 刷新循环因 last_token_refresh_status 属性未初始化导致崩溃的问题',
                '【修复】修复手动刷新认证预检因 asyncio 局部变量遮蔽导致 UnboundLocalError 的问题'
            ]
        },
        {
            version: 'v1.8.1',
            date: '2026-04-03',
            updates: [
                '【修复】滑块恢复与令牌刷新链路更稳定，滑块成功后会及时回写有效会话 Cookie，并保护关键会话字段不被不完整快照覆盖',
                '【修复】手动刷新后的任务交接与初始化鉴权恢复，新增 Token 预检、交接恢复窗口、恢复锁和鉴权失败冷静期，减少 WebSocket 已连通但因 Token 获取失败反复重试',
                '【修复】统一通知派发路径并收口验证通知，修正推送冷却、人脸验证通知类型/文案/模板渲染，以及定时刷新误报',
                '【优化】账密登录与手动刷新流程里的滑块验证也会写入风控日志和滑块统计，风控排查口径更完整',
                '【修复】取消订单后的系统卡片不再覆盖真实 buyer_id，避免订单买家信息被异常值污染',
                '【优化】多数量纯文本卡券消息支持批量合并发送，减少重复刷屏，卡券发货提示更简洁'
            ]
        },
        {
            version: 'v1.8.0',
            date: '2026-04-01',
            updates: [
                '【新功能】风控日志升级为结构化会话链路，覆盖滑块验证、Token 过期、账密登录和扫码刷新，支持统一追踪结果、场景与脱敏元数据',
                '【优化】风控看板重构为结构化会话统计，新增更准确的滑块成功/失败会话统计、筛选能力与响应式详情展示，排查风控更直观',
                '【优化】滑块验证反检测全面增强，加入稳定指纹配置、拟人轨迹、Cookie 预热与多轮重试策略，提升验证通过率与稳定性',
                '【修复】手动刷新、扫码登录与密码登录流程增加互斥保护、失败退避和状态收口，减少刷新互踩、扫码回滚误判与登录风控残留',
                '【修复】扫码/密码登录链路补强浏览器侧 Cookie 稳定化、前置登录态校验和人脸/滑块兜底判断，登录成功判定更可靠',
                '【优化】图片上传新增错误类型追踪，调用方可按错误原因给出更准确的提示与处理',
                '【修复】商品管理区分“同步商品”和“刷新列表”，同步指定页/所有页时会强制拉取已有商品的最新详情，避免本地缓存长期陈旧'
            ]
        },
        {
            version: 'v1.7.5',
            date: '2026-03-24',
            updates: [
                '【修复】修复扫码登录遇到人脸验证时直接返回外部链接导致验证会话丢失的问题，改为在服务端保持原始会话并生成验证二维码',
                '【修复】修复扫码成功后仍可能再次进入滑块验证的问题，新增真实 Cookie 合并与首次 Token 预热保护',
                '【优化】优化扫码风控状态收口，增加浏览器侧兜底判定，验证完成后可更稳定进入登录成功',
                '【优化】优化扫码登录前端提示，减少重复提示并统一验证过程中的状态反馈'
            ]
        },
        {
            version: 'v1.7.4',
            date: '2026-03-22',
            updates: [
                '【修复】收紧订单号提取规则，避免普通消息中的 messageId 被误识别为订单号并生成处理中假订单',
                '【修复】统一销售统计口径并跳过空金额/脏金额订单，修复销售额卡片获取失败的问题',
                '【优化】重构仪表盘账号概览、订单数据看板、销售趋势与发货日志展示，关键信息更清晰易读',
                '【优化】发货日志拆分规则、匹配结果、触发方式和规格状态列，并简化规格状态显示便于快速排查'
            ]
        },
        {
            version: 'v1.7.3',
            date: '2026-03-21',
            updates: [
                '【修复】热更新清单改为优先读取上一版 Release 资产中的 update_files.json，避免 deleted_files 丢失',
                '【修复】修正同版本下热更新可能回滚清单生成脚本的问题，补齐删除清单并完善后续版本生成逻辑'
            ]
        },
        {
            version: 'v1.7.2',
            date: '2026-03-20',
            updates: [
                '【新功能】账号列表新增商品一键擦亮入口，可批量执行当前在售商品擦亮',
                '【新功能】新增每日定时擦亮任务，支持按账号配置启用状态、执行时段与随机延迟',
                '【优化】后台新增定时任务调度与执行结果记录，便于查看下次执行时间和最近运行情况',
                '【优化】管理端补充擦亮相关操作入口与设置弹窗，日常运营更方便',
                '【优化】账号管理页调整列表列宽与仪表盘赞助按钮样式，提升界面可读性与交互一致性'
            ]
        },
        {
            version: 'v1.7.1',
            date: '2026-03-19',
            updates: [
                '【修复】订单规格识别改为优先读取结构化订单响应中的 skuInfo 和数量，减少页面元素缺失导致的漏识别',
                '【修复】正文兜底不再把标题、描述等冒号文案误判为第二规格，避免单规格订单被错误识别为双规格',
                '【优化】正文规格过滤只保留更像真实 SKU 字段的候选，进一步过滤时间、广告文案和无关文本',
                '【修复】订单明确解析为单规格时自动清空历史残留的第二规格字段，避免旧脏数据继续影响发货匹配',
                '【优化】多规格商品复用缓存时要求金额、状态和主规格同时有效，降低脏缓存复用风险'
            ]
        },
        {
            version: 'v1.7.0',
            date: '2026-03-19',
            updates: [
                '【修复】系统消息状态优先级与消息分流逻辑重构，阻止订单状态回退并减少系统通知噪音',
                '【修复】扩展订单消息的订单号提取来源，增强 sid 兜底查单与近邻回退，减少简化消息和终态待处理消息漏单',
                '【优化】订单详情优先采用结构化状态信号，补刷增加状态门控与冷却，降低误判和高频补刷',
                '【修复】无规格商品规则匹配与 sid 兜底发货链路收紧，降低串单和误发风险',
                '【修复】小刀订单新增成功证据持久化，在缺少完整待发货卡片时也能继续自动发货兜底',
                '【修复】闲鱼币抵扣订单金额识别，优先保留或推导真实实付金额，避免把原价误判为成交价',
                '【优化】发货日志新增“已跳过”状态，并过滤成功后的重复 skipped 记录，日志展示更清晰'
            ]
        },
        {
            version: 'v1.6.1',
            date: '2026-03-13',
            updates: [
                '【修复】简化消息buyer_id误提取：区分sid与PNM格式消息，不再将会话ID误提取为buyer_id，避免防串单校验误判',
                '【修复】无效buyer_id污染订单数据：新增buyer_id可信度校验，识别unknown_user等无效占位符，防串单时自动跳过',
                '【修复】数据库buyer_id被占位符覆盖：更新订单时跳过无效buyer_id覆盖已有有效值，新增订单时自动净化为空值',
                '【新功能】PNM格式消息解析：从message[4]提取buyer_id、买家昵称和商品ID，提升订单信息完整度',
                '【修复】订单详情锁事件循环不匹配：Web API调用时自动重建锁，避免跨事件循环死锁',
                '【优化】统一buyer_id/user_id默认值：各处unknown_user占位符改为None，避免脏数据进入订单与发货链路'
            ]
        },
        {
            version: 'v1.6.0',
            date: '2026-03-12',
            updates: [
                '【修复】通知渠道邮件表单使用独立字段标识，避免与系统 SMTP 配置冲突后保存时误提示“请填写SMTP服务器”',
                '【优化】通知渠道邮件标题与正文抬头统一为“闲鱼管理系统通知”，并精简验证码邮件文案',
                '【优化】系统品牌文案统一调整为“闲鱼管理系统”，同步更新登录页、注册页、浏览器标题、API 文档与统计服务说明',
                '【修复】风控日志记录数徽标文字垂直居中，处理状态筛选恢复可用，筛选结果与条数统计保持一致',
                '【优化】风控验证通知文案调整为“自动回复功能暂时无法使用”，避免系统名与功能名混淆'
            ]
        },
        {
            version: 'v1.5.9',
            date: '2026-03-11',
            updates: [
                '【修复】买家昵称过滤系统文案，避免订单和发货日志写入错误昵称',
                '【修复】小刀订单商品归属增加回退校验，避免缓存未命中时误跳过自动发货',
                '【修复】连续下单场景下旧关单消息串到新订单的问题',
                '【修复】sid 简化消息将已处理订单误报为未找到订单的问题',
                '【优化】前端时间显示与销售统计统一按北京时间口径处理'
            ]
        },
        {
            version: 'v1.5.8',
            date: '2026-03-11',
            updates: [
                '【新功能】热更新弹窗新增“本次跳过”和“忽略此版本”，支持按版本跳过当前更新提示',
                '【优化】仪表盘检查更新入口改为按钮组，新增自动检查开关和忽略版本管理，设置仅当前浏览器生效'
            ]
        },
        {
            version: 'v1.5.7',
            date: '2026-03-11',
            updates: [
                '【修复】单规格订单自动发货改为优先精确匹配，精确规则未命中时支持降级到普通关键字规则',
                '【优化】单规格降级兜底仅在唯一命中一条普通规则时放行，避免多规则误发',
                '【优化】发货日志新增“单规兜底”标签，便于区分精确命中和普通规则兜底'
            ]
        },
        {
            version: 'v1.5.6',
            date: '2026-03-11',
            updates: [
                '【修复】补发 captcha_control.html 热更新资源，避免 v1.5.5 下该文件因 Release 资源未刷新而反复提示更新',
                '【优化】热更新检查日志改为汇总输出，不再逐文件打印“文件已是最新”'
            ]
        },
        {
            version: 'v1.5.5',
            date: '2026-03-11',
            updates: [
                '【新功能】热更新清单改为自动扫描 Python、HTML、静态资源和前端源码文件，无需手动维护白名单',
                '【新功能】新增发版预检查脚本，可在发布前检查版本号、改名/删除文件和未跟踪热更新文件',
                '【新功能】热更新支持按清单删除旧文件，删除前会自动备份，降低改名和清理残留文件的风险',
                '【优化】update_files.json 改为由 GitHub Actions 自动生成并上传到 Release，仓库内不再手动维护'
            ]
        },
        {
            version: 'v1.5.4',
            date: '2026-03-10',
            updates: [
                '【修复】补充 .gitattributes 行尾规则，统一 Python、JS、HTML、CSS 等热更新相关文本文件使用 LF',
                '【修复】避免因 CRLF/LF 行尾差异导致同版本下仍被误判为可热更新文件'
            ]
        },
        {
            version: 'v1.5.3',
            date: '2026-03-10',
            updates: [
                '【修复】热更新执行权限改为按管理员身份校验，不再强依赖用户名必须为 admin',
                '【修复】前端更新失败提示优先展示后端 detail 信息，避免只显示“未知错误”'
            ]
        },
        {
            version: 'v1.5.2',
            date: '2026-03-10',
            updates: [
                '【新功能】GitHub Actions 在创建 Release 前自动生成并上传 update_files.json，无需手动维护更新清单',
                '【优化】热更新检测前会实时刷新本地版本号，本地版本变更后无需重启服务即可重新检查更新',
                '【修复】热更新执行权限改为按管理员身份判断，不再强依赖用户名必须为 admin',
                '【修复】前端更新失败提示补充后端 detail 信息，避免只显示“未知错误”'
            ]
        },
        {
            version: 'v1.5.1',
            date: '2026-03-10',
            updates: [
                '【新功能】接入 GitHub Releases 在线更新，支持从最新 Release 读取 update_files.json 检查热更新',
                '【新功能】仪表盘版本区新增管理员可见的“检查更新”入口，可直接执行热更新',
                '【优化】更新清单解析兼容 GitHub 资产返回 application/octet-stream 的场景，避免检查更新失败',
                '【优化】版本区样式统一为 badge 视觉，并修复版本号与更新入口的垂直居中显示',
                '【新功能】新增 GitHub Actions 自动发布工作流，push 到 main 且版本变化后可自动创建 tag 和 Release'
            ]
        },
        {
            version: 'v1.5.0',
            date: '2026-03-10',
            updates: [
                '【新功能】Cookie、密码等敏感字段使用 Fernet 加密存储，启动时自动迁移历史明文数据',
                '【新功能】多数量发货收尾状态机，消息发送与卡密消费/确认发货分阶段提交，避免脏数据',
                '【新功能】批量数据卡密预占机制，发货前预占、发送后确认，启动时自动恢复过期预占',
                '【新功能】发货进度追踪表，支持多数量订单分单元进度查询与状态聚合',
                '【新功能】订单事件中心 OrderEventHub，按用户广播订单更新，支持 SSE 实时流推送',
                '【新功能】仪表盘新增销售额统计面板与趋势曲线图，支持当日销售额显示及自动刷新（by @Mangor2021）',
                '【新功能】添加卡券时可自动生成对应的发货规则（by @Mangor2021）',
                '【优化】新增 partial_success（部分发货）和 partial_pending_finalize（部分待收尾）中间状态',
                '【优化】退款撤销回退增强，新增 pre_refund_status 字段持久化退款前状态，支持跨重启回退',
                '【优化】新增外部状态合并保护，防止粗粒度状态覆盖内部精细发货进度',
                '【优化】规格识别容错增强，过滤备案信息、时间戳、URL 等误识别字段',
                '【优化】订单缓存复用条件扩展为金额+状态+规格综合判断，减少不必要的浏览器抓取',
                '【优化】账号列表接口不再返回完整 Cookie 和密码原文，改为脱敏展示',
                '【优化】销售额数据按用户账号隔离，修复多用户场景下数据串读',
                '【优化】发货日志记录拼接规格模式上下文，便于排查',
                '【优化】新增 message_hash + 强关联键精准消息匹配框架',
                '【优化】发货成功后激活订单级延迟锁，防止短时间内重复发货',
                '【优化】销售额曲线变化增加平滑过渡动画，时间按钮改为属性匹配（by @Mangor2021）',
                '【修复】自动确认发货 Session 跨事件循环复用导致 timeout 错误，改为每次创建独立 Session',
                '【修复】自动确认发货请求沿用主实例 HTTP 代理配置',
                '【修复】前端 showToast 从 innerHTML 改为 DOM 构建，防止 XSS 注入',
                '【修复】账号编辑与默认回复模态框 DOM ID 冲突导致数据串写',
                '【修复】复制 Cookie 改为按需 API 获取，列表页不再暴露原文',
                '【修复】前端订单状态筛选项与后端状态体系对齐',
                '【修复】批量删除按钮默认 disabled，全选复选框 ID 修正',
                '【修复】增加系统消息过滤关键字，修复商品信息变更后误触发自动回复（by @Mangor2021）'
            ]
        },
        {
            version: 'v1.3.4',
            date: '2026-03-03',
            updates: [
                '【优化】无规格商品自动发货改为单次详情尝试并强制按普通规则匹配，避免误识别规格干扰',
                '【优化】规格商品在缺失规格时新增“唯一规则安全兜底”，仅唯一命中时放行，提升单规格场景成功率',
                '【修复】规格匹配失败后的普通规则兜底查询补充 user_id 过滤，避免跨账号规则误命中',
                '【修复】补充 pending_payment 内部状态映射，减少未映射状态告警'
            ]
        },
        {
            version: 'v1.3.3',
            date: '2026-03-03',
            updates: [
                '【优化】增强订单详情解析稳定性，新增刷新重试、文本兜底与金额多选择器提取，降低偶发规格/金额缺失',
                '【优化】新增结构化解析日志 ORDER_DETAIL_PARSE_ALERT / ORDER_DETAIL_PARSE_RECOVERED，便于快速排查异常账号与订单',
                '【优化】避免空值和 unknown 状态覆盖已有有效订单字段，减少后续发货链路受脏数据影响',
                '【修复】SQL日志敏感参数统一脱敏（password/proxy_pass/smtp_password/admin_password_hash）',
                '【修复】默认管理员初始化日志移除明文密码提示',
                '【修复】订单金额前端显示优化，避免重复货币符号并统一空值显示'
            ]
        },
        {
            version: 'v1.3.2',
            date: '2026-03-02',
            updates: [
                '【新功能】仪表盘新增发货日志面板，与账号详情5:5并排展示，支持查看最近发货事件',
                '【新功能】新增发货日志接口 /delivery-logs/recent，支持按用户读取最近发货日志',
                '【优化】自动发货与手动发货统一记录真实发货事件，包含规则关键词、匹配模式（精确/兜底）、渠道（自动/手动）与失败原因',
                '【优化】新增 delivery_logs 数据表与索引，提升发货日志可追溯性与查询效率',
                '【修复】自动确认发货失败后改为直接阻断发货，避免异常订单继续下发卡密',
                '【修复】简化消息路径取消重复确认，统一由 _auto_delivery 执行一次确认，降低漏发风险',
                '【修复】小刀流程调整为两阶段：待刀成仅免拼，成功小刀待发货才自动发货',
                '【修复】自动发货关键字仅允许系统消息触发，并加强 sid 兜底订单一致性校验'
            ]
        },
        {
            version: 'v1.3.1',
            date: '2026-03-02',
            updates: [
                '【新功能】AI回复配置新增API类型能力，支持OpenAI Chat/Responses、Gemini、Anthropic、Azure OpenAI、Ollama',
                '【修复】修复DashScope兼容模式被误判为百炼应用导致报错“未找到app_id”的问题',
                '【优化】AI配置预设支持api_type维度，保存/切换/自动匹配更准确',
                '【优化】AI回复配置弹窗全量重构，按连接层/策略层/语义层/验证层分区并适配移动端与暗色模式',
                '【优化】下线API类型中的“DashScope（百炼应用）”入口，历史值自动映射为自动识别',
                '【优化】提示词三个输入框高度统一，提升编辑体验',
                '【优化】浏览器标题统一为“闲鱼管理系统”'
            ]
        },
        {
            version: 'v1.3.0',
            date: '2026-03-01',
            updates: [
                '【新功能】回复延迟配置：账号列表卡片头部新增回复延迟设置控件，支持界面配置防抖延迟时间（1-10秒），修改后实时生效无需重启',
                '【优化】系统消息过滤：优化系统消息过滤关键字，改为部分匹配方式，避免因符号、空格差异导致漏匹配（PR #4 by @Mangor2021）',
                '【优化】系统消息过滤：进一步优化关键字精确度，避免误匹配买家正常消息（如"已发货"改为"你已发货"）',
                '【优化】账号列表：表格内容居中显示，调整列宽分配，提升整体布局美观度'
            ]
        },
        {
            version: 'v1.2.9',
            date: '2026-02-26',
            updates: [
                '【新功能】仪表盘新增订单数据看板，展示订单总数、销售总金额、订单完成率、当日订单数',
                '【优化】仪表盘统计卡片将“总订单数”调整为“商品总数”，并同步更新图标与统计逻辑',
                '【优化】统一订单完成率统计口径（分子：交易成功；分母：待发货+已发货+交易成功+交易关闭）',
                '【优化】新增订单状态归一化兼容（success/finished、pending_ship/delivered/cancelled）并统一展示',
                '【修复】将退款中状态文案明确为“申请退款中”，并修正手动发货按钮禁用条件'
            ]
        },
        {
            version: 'v1.2.8',
            date: '2026-02-26',
            updates: [
                '【修复】优化侧边栏切换逻辑，避免切换菜单时主内容区出现白屏闪烁',
                '【修复】优化全局loading遮罩显示策略（延迟展示+并发计数），降低仪表盘和账号管理切换时的闪白感',
                '【优化】暗色模式可读性增强：提升账号管理扫码按钮提示文案与仪表盘总账号图标的对比度',
                '【修复】恢复loading出现时的鼠标悬停焦点表现，避免交互反馈丢失',
                '【修复】修复页面刷新时短暂回退默认蓝色主题的问题，首屏优先应用缓存主题色'
            ]
        },
        {
            version: 'v1.2.7',
            date: '2026-02-21',
            updates: [
                '【新功能】AI配置预设：支持保存/切换/删除常用的API配置组合（模型、密钥、地址），一键切换不同AI服务',
                '【修复】修复OpenAI兼容API的base_url缺少/v1后缀导致请求404的问题'
            ]
        },
        {
            version: 'v1.2.6',
            date: '2026-02-18',
            updates: [
                '【优化】AI回复配置：修复模型下拉框HTML标签错误，更新可用模型列表（新增deepseek-v3.2、kimi-k2.5等）',
                '【优化】自定义提示词：从单JSON输入改为议价/技术/一般三个独立输入框，操作更直观',
                '【优化】关键词输入：输入框改为多行文本域，支持竖线和换行分隔批量添加',
                '【新功能】关键词回复内容支持就地编辑，无需重新添加即可修改回复文本',
                '【优化】暗色模式全面适配：关键词管理、账号管理、扫码登录弹窗、全局滚动条',
                '【修复】关键词输入区域布局错乱问题'
            ]
        },
        {
            version: 'v1.2.5',
            date: '2026-02-12',
            updates: [
                '【新功能】风控日志新增令牌过期、Cookie刷新等事件类型，支持7种状态显示',
                '【优化】滑块验证异常和导入失败事件同步写入风控日志数据库'
            ]
        },
        {
            version: 'v1.2.4',
            date: '2026-02-08',
            updates: [
                '【新功能】优化验证类型检测，精确区分人脸/短信/二维码/账密错误',
                '【新功能】新增 {verification_type} 模板变量',
                '【新功能】风控日志支持多种事件类型',
                '【修复】修复密码登录时 db_manager 变量作用域问题',
                '【修复】移除通知中的【闲鱼通知】前缀'
            ]
        },
        {
            version: 'v1.2.3',
            date: '2026-02-08',
            updates: [
                '【新功能】新增通知模板自定义功能，支持7种通知类型',
                '【新功能】暗色模式新增跟随系统选项',
                '【修复】修复飞书通知签名验证失败的问题',
                '【修复】修复通知内容重复显示账号ID和时间的问题'
            ]
        },
        {
            version: 'v1.2.2',
            date: '2026-01-29',
            updates: [
                '【修复】修复下单时买家昵称提取错误的问题',
                '【修复】修复点击导航链接会刷新页面的问题',
                '【修复】修复暗色模式刷新页面闪烁问题',
                '【修复】修复递归搜索误提取tradeId等非商品ID的问题',
                '【修复】修复订单管理商品ID提取错误的问题'
            ]
        },
        {
            version: 'v1.2.1',
            date: '2026-01-28',
            updates: [
                '【新功能】新增暗色模式支持，可在系统设置中切换主题',
                '【新功能】下单时自动获取并保存买家昵称'
            ]
        },
        {
            version: 'v1.2.0',
            date: '2026-01-28',
            updates: [
                '【优化】大幅优化滑块验证重试策略',
                '【优化】缩短滑块验证重试等待时间'
            ]
        },
        {
            version: 'v1.1.9',
            date: '2026-01-28',
            updates: [
                '【修复】修复交易关闭时订单状态不更新的问题'
            ]
        },
        {
            version: 'v1.1.8',
            date: '2026-01-28',
            updates: [
                '【优化】优化滑块验证策略',
                '【新功能】添加滑块验证优化代码'
            ]
        },
        {
            version: 'v1.1.7',
            date: '2026-01-28',
            updates: [
                '【菜单管理】新增拖拽排序功能',
                '【菜单管理】按住拖动图标可调整菜单顺序',
                '【菜单管理】菜单顺序自动保存到用户配置',
                '【版本信息】点击版本号可查看更新日志',
                '【侧边栏】使用CSS order属性实现菜单重排序',
                '【修复】修复菜单排序后管理员功能和登出按钮位置错乱的问题'
            ]
        },
        {
            version: 'v1.1.6',
            date: '2026-01-27',
            updates: [
                '【菜单管理】新增侧边栏菜单显示/隐藏功能',
                '【菜单管理】在系统设置中可自定义显示哪些菜单项',
                '【菜单管理】仪表盘和系统设置为必选项，其他菜单可自由开关',
                '【菜单管理】设置自动保存到用户配置，刷新后保持'
            ]
        },
        {
            version: 'v1.1.5',
            date: '2026-01-27',
            updates: [
                '【主题设置】新增主题颜色自定义功能',
                '【主题设置】提供9种预设颜色（靛蓝、紫罗兰、蓝色、青色、绿色、橙色、红色、粉色、灰色）',
                '【主题设置】支持颜色选择器自定义任意颜色',
                '【主题设置】支持直接输入颜色代码',
                '【系统设置】主题设置界面简化，操作更直观',
                '【系统设置】系统重启按钮移至页面标题栏右侧'
            ]
        },
        {
            version: 'v1.1.4',
            date: '2026-01-27',
            updates: [
                '【订单管理】新增买家昵称列，方便识别买家身份',
                '【订单管理】订单搜索支持按买家昵称搜索',
                '【自动补全】买家发消息时自动补全历史订单昵称',
                '【订单详情】弹窗中显示买家昵称信息'
            ]
        },
        {
            version: 'v1.1.3',
            date: '2026-01-27',
            updates: [
                '【系统设置】优化"登录与注册设置"卡片布局',
                '【系统设置】增大各选项间距，提升视觉体验',
                '【系统设置】保存按钮使用大号样式并占满卡片宽度',
                '【系统设置】状态提示移至按钮上方显示'
            ]
        },
        {
            version: 'v1.1.2',
            date: '2026-01-27',
            updates: [
                '【在线客服】修复页面底部白色空白区域问题',
                '【系统设置】重新组织页面布局（两行两列）',
                '【系统设置】合并"注册设置"到"登录与注册设置"',
                '【侧边栏】优化折叠功能'
            ]
        },
        {
            version: 'v1.1.1',
            date: '2026-01-27',
            updates: [
                '【在线客服】优化账号密码显示布局，移至选择框旁边',
                '【在线客服】刷新账号列表时自动重置账号密码显示',
                '【API】cookies/details接口新增返回password字段',
                '【UI】添加favicon图标，更新登录注册页面Logo',
                '【配置】默认端口从8080修改为8090'
            ]
        },
        {
            version: 'v1.1.0',
            date: '2026-01-25',
            updates: [
                '添加登录页面验证码开关功能',
                '优化订单管理功能',
                '添加手动发货和刷新订单状态功能',
                '完善双规格自动发货功能',
                '修复自动发货模块语法错误导致账号无法启动的问题',
                '添加手动重启功能'
            ]
        },
        {
            version: 'v1.0.0',
            date: '2026-01-24',
            updates: [
                '闲鱼管理系统初始版本'
            ]
        }
    ]
};

/**
 * 加载系统版本号并检查更新
 */
async function loadSystemVersion() {
    try {
        // 先从 version.txt 动态读取本地版本号
        try {
            const versionResponse = await fetch('/static/version.txt?t=' + Date.now());
            if (versionResponse.ok) {
                LOCAL_VERSION = (await versionResponse.text()).trim();
                currentSystemVersion = LOCAL_VERSION;
            }
        } catch (e) {
            console.warn('无法读取本地版本文件，使用默认版本');
            LOCAL_VERSION = DEFAULT_VERSION;
        }
        
        // 显示当前本地版本
        document.getElementById('versionNumber').textContent = LOCAL_VERSION;
        
        // 添加点击事件，显示更新日志
        const systemVersionBadge = document.getElementById('systemVersion');
        if (systemVersionBadge) {
            systemVersionBadge.style.cursor = 'pointer';
            systemVersionBadge.title = '点击查看更新日志';
            systemVersionBadge.onclick = () => showChangelogModal();
        }

        refreshHotUpdateButtonState();

        if (!isHotUpdateAutoCheckEnabled()) {
            return;
        }

        // 调用后端检查更新（复用热更新接口）
        try {
            const checkResult = await checkHotUpdate();
            if (checkResult && checkResult.has_update) {
                refreshHotUpdateButtonState(checkResult);
            }
        } catch (e) {
            console.warn('版本检查失败:', e.message);
        }

    } catch (error) {
        console.error('版本加载失败:', error);
        document.getElementById('versionNumber').textContent = '未知';
    }
}

/**
 * 获取更新信息（使用缓存或本地版本历史）
 */
async function getUpdateInfo() {
    // 如果已有缓存的远程版本信息，映射为前端期望的字段格式
    if (remoteVersionInfo) {
        return {
            version: remoteVersionInfo.new_version || remoteVersionInfo.version,
            updates: remoteVersionInfo.changelog || remoteVersionInfo.updates,
            description: remoteVersionInfo.description,
            releaseDate: remoteVersionInfo.release_date || remoteVersionInfo.releaseDate,
            downloadUrl: remoteVersionInfo.downloadUrl,
            altDownloadUrl: remoteVersionInfo.altDownloadUrl,
            installMethods: remoteVersionInfo.installMethods,
            notice: remoteVersionInfo.notice
        };
    }

    // 使用本地版本历史作为兜底
    remoteVersionInfo = LOCAL_VERSION_HISTORY;
    return remoteVersionInfo;
}

/**
 * 显示更新信息（点击"有更新"标签时调用）
 */
async function showUpdateInfo(newVersion) {
    const updateInfo = await getUpdateInfo();
    if (!updateInfo) return;

    // 构建更新内容列表
    let updateList = '';
    if (updateInfo.updates && updateInfo.updates.length > 0) {
        updateList = updateInfo.updates.map(item => `<li style="color: #333; margin-bottom: 8px; line-height: 1.5; font-size: 15px;"><i class="bi bi-check-circle-fill me-2" style="color: #28a745;"></i>${item}</li>`).join('');
    }
    
    // 构建安装方式区域
    let installSection = '';
    if (updateInfo.installMethods && updateInfo.installMethods.length > 0) {
        installSection = updateInfo.installMethods.map(method => {
            let content = '';
            
            // 如果有步骤说明（如Docker安装）
            if (method.steps && method.steps.length > 0) {
                content = `
                    <div style="background: #2d3748; color: #e2e8f0; padding: 12px 14px; border-radius: 6px; font-family: 'Consolas', 'Monaco', monospace; font-size: 13px; line-height: 1.6;">
                        ${method.steps.map(step => `<div style="margin-bottom: 6px;">${step}</div>`).join('')}
                    </div>
                `;
            }
            
            // 如果有下载链接（如EXE下载）
            if (method.downloads && method.downloads.length > 0) {
                content = `
                    <div class="d-flex flex-wrap gap-2">
                        ${method.downloads.map(dl => `
                            <a href="${dl.url}" target="_blank" class="btn btn-sm" style="background: #5a67d8; color: #fff; border: none; font-size: 14px; padding: 8px 16px;">
                                <i class="bi bi-cloud-download me-1"></i>${dl.name}
                                ${dl.extra ? `<small style="margin-left: 4px; opacity: 0.85;">(${dl.extra})</small>` : ''}
                            </a>
                        `).join('')}
                    </div>
                `;
            }
            
            return `
                <div style="margin-bottom: 10px; border-radius: 8px; overflow: hidden; border: 1px solid #e0e0e0;">
                    <div class="d-flex align-items-center justify-content-between" style="background: #5a67d8; color: #fff; padding: 8px 12px;">
                        <span style="font-size: 14px; font-weight: 600;"><i class="bi ${method.icon || 'bi-box'} me-1"></i>${method.name}</span>
                        ${method.description ? `<small style="opacity: 0.85; font-size: 13px;">${method.description}</small>` : ''}
                    </div>
                    <div style="background: #fff; padding: 12px;">${content}</div>
                </div>
            `;
        }).join('');
    }
    
    // 兼容旧格式：构建下载按钮（如果有下载地址）
    let downloadSection = '';
    if (!installSection && updateInfo.downloadUrl) {
        downloadSection = `
            <div class="d-grid gap-2 mt-4">
                <a href="${updateInfo.downloadUrl}" target="_blank" class="btn btn-success btn-lg">
                    <i class="bi bi-download me-2"></i>立即下载新版本
                </a>
            </div>
        `;
    }
    
    // 兼容旧格式：构建备用下载地址
    let altDownloadSection = '';
    if (!installSection && updateInfo.altDownloadUrl) {
        altDownloadSection = `
            <div class="text-center mt-2">
                <a href="${updateInfo.altDownloadUrl}" target="_blank" class="text-muted small">
                    <i class="bi bi-link-45deg me-1"></i>备用下载地址
                </a>
            </div>
        `;
    }

    const modalHtml = `
        <div class="modal fade" id="updateModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">
                <div class="modal-content" style="border: none; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.15);">
                    <!-- 头部 -->
                    <div class="modal-header py-3" style="background: linear-gradient(135deg, #667eea 0%, #5a67d8 100%); border: none;">
                        <h5 class="modal-title mb-0" style="color: #fff; font-weight: 600; font-size: 18px;">
                            <i class="bi bi-stars me-2"></i>发现新版本
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <!-- 内容 -->
                    <div class="modal-body py-4 px-4" style="background: linear-gradient(180deg, #f0f4ff 0%, #f8fafc 100%);">
                        <!-- 版本对比 -->
                        <div class="d-flex align-items-center justify-content-center gap-4 mb-4 p-3 rounded-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                            <div class="text-center">
                                <div style="color: #666; font-size: 14px; margin-bottom: 4px;">当前</div>
                                <div><span class="badge" style="background: #6c757d; color: #fff; font-size: 14px; padding: 6px 12px;">${LOCAL_VERSION}</span></div>
                            </div>
                            <i class="bi bi-arrow-right" style="color: #28a745; font-size: 1.5rem;"></i>
                            <div class="text-center">
                                <div style="color: #28a745; font-size: 14px; margin-bottom: 4px;">最新</div>
                                <div><span class="badge" style="background: linear-gradient(135deg, #28a745, #20c997); color: #fff; font-size: 14px; padding: 6px 12px;">${updateInfo.version}</span></div>
                            </div>
                            ${updateInfo.releaseDate ? `<span style="color: #888; font-size: 14px; margin-left: 12px;"><i class="bi bi-calendar3 me-1"></i>${updateInfo.releaseDate}</span>` : ''}
                        </div>
                        
                        <!-- 版本简介 -->
                        ${updateInfo.description ? `
                        <div class="rounded-3 p-3 mb-3" style="background: linear-gradient(135deg, #e3f2fd, #bbdefb); color: #1565c0; font-size: 15px; line-height: 1.5;">
                            <i class="bi bi-info-circle me-2"></i>${updateInfo.description}
                        </div>
                        ` : ''}
                        
                        <!-- 更新内容 -->
                        <div class="mb-3">
                            <div class="mb-2" style="color: #444; font-size: 16px; font-weight: 600;"><i class="bi bi-list-check me-2"></i>更新内容</div>
                            <div class="rounded-3 p-3" style="max-height: 180px; overflow-y: auto; background: #fff; border: 1px solid #e8ecf0; box-shadow: inset 0 1px 3px rgba(0,0,0,0.04);">
                                ${updateList ? `<ul class="list-unstyled mb-0">${updateList}</ul>` : '<span style="color: #999; font-size: 15px;">暂无</span>'}
                            </div>
                        </div>
                        
                        <!-- 重要提示 -->
                        ${updateInfo.notice ? `
                        <div class="rounded-3 p-3 mb-3" style="background: linear-gradient(135deg, #fff3cd, #ffeeba); color: #856404; font-size: 15px; line-height: 1.5;">
                            <i class="bi bi-exclamation-triangle me-2"></i><strong>注意：</strong>${updateInfo.notice}
                        </div>
                        ` : ''}
                        
                        <!-- 安装方式 -->
                        ${installSection ? `
                        <div class="mb-2" style="color: #444; font-size: 16px; font-weight: 600;"><i class="bi bi-download me-2"></i>安装/升级方式</div>
                        ${installSection}
                        ` : ''}
                        
                        <!-- 兼容旧格式：下载按钮 -->
                        ${downloadSection}
                        ${altDownloadSection}
                    </div>
                    <!-- 底部 -->
                    <div class="modal-footer py-3" style="background: #fff; border-top: 1px solid #e8ecf0;">
                        <button type="button" class="btn" style="background: #f0f0f0; color: #666; border: none; font-size: 15px; padding: 8px 20px;" data-bs-dismiss="modal">
                            <i class="bi bi-x-lg me-1"></i>稍后再说
                        </button>
                        <button type="button" class="btn" id="hotUpdateBtn" style="background: linear-gradient(135deg, #28a745, #20c997); color: #fff; border: none; font-size: 15px; padding: 8px 20px;" onclick="performHotUpdate()">
                            <i class="bi bi-cloud-download me-1"></i>一键热更新
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // 移除已存在的模态框
    const existingModal = document.getElementById('updateModal');
    if (existingModal) {
        existingModal.remove();
    }

    // 添加新的模态框
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('updateModal'));
    modal.show();
}

// =============================================================================
// 最新权益弹窗功能
// =============================================================================

/**
 * 显示更新日志弹窗
 */
function showChangelogModal() {
    const changelogContent = document.getElementById('changelogContent');
    if (!changelogContent) return;

    // 从 LOCAL_VERSION_HISTORY 统一读取，避免维护两份数据
    const prefixTypeMap = {
        '新功能': 'feature',
        '优化': 'optimize',
        '修复': 'fix'
    };
    const changelog = LOCAL_VERSION_HISTORY.versionHistory.map(v => ({
        version: v.version,
        date: v.date,
        changes: v.updates.map(text => {
            let type = 'feature';
            let cleanText = text;
            const match = text.match(/^【(.+?)】(.+)$/);
            if (match) {
                if (prefixTypeMap[match[1]]) {
                    type = prefixTypeMap[match[1]];
                    cleanText = match[2];
                } else {
                    // 模块名前缀（如【菜单管理】），保留完整文本
                    type = 'feature';
                    cleanText = text;
                }
            }
            return { type, text: cleanText };
        })
    }));

    // 生成HTML
    const html = changelog.map(release => {
        const changesHtml = release.changes.map(change => {
            let icon, color;
            switch (change.type) {
                case 'feature':
                    icon = 'bi-plus-circle-fill';
                    color = '#28a745';
                    break;
                case 'optimize':
                    icon = 'bi-arrow-up-circle-fill';
                    color = '#17a2b8';
                    break;
                case 'fix':
                    icon = 'bi-wrench';
                    color = '#dc3545';
                    break;
                default:
                    icon = 'bi-dot';
                    color = '#6c757d';
            }
            return `
                <div class="d-flex align-items-start mb-2">
                    <i class="bi ${icon} me-2" style="color: ${color}; margin-top: 3px;"></i>
                    <span>${change.text}</span>
                </div>
            `;
        }).join('');

        return `
            <div class="changelog-version mb-4">
                <div class="d-flex align-items-center mb-2">
                    <span class="badge bg-primary me-2">${release.version}</span>
                    <small class="text-muted">${release.date}</small>
                </div>
                <div class="ps-2 border-start border-2" style="border-color: var(--primary-color) !important;">
                    ${changesHtml}
                </div>
            </div>
        `;
    }).join('');

    changelogContent.innerHTML = html;

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('changelogModal'));
    modal.show();
}

/**
 * 显示最新权益弹窗
 */
async function showBenefitsModal() {
    try {
        // 获取权益信息（使用缓存或重新请求）
        const benefitsData = await getBenefitsInfo();
        
        if (!benefitsData || !benefitsData.benefits || benefitsData.benefits.length === 0) {
            showToast('暂无权益信息', 'info');
            return;
        }
        
        // 构建权益列表
        const benefitsList = benefitsData.benefits.map(benefit => `
            <a href="${benefit.url}" target="_blank" class="benefit-item" style="text-decoration: none; display: block; margin-bottom: 12px; border-radius: 12px; overflow: hidden; border: 1px solid #e8ecf0; transition: all 0.3s ease; box-shadow: 0 2px 8px rgba(0,0,0,0.04);">
                <div style="background: linear-gradient(135deg, ${benefit.color || '#667eea'}20, ${benefit.color || '#667eea'}10); padding: 16px; display: flex; align-items: center; gap: 16px;">
                    <div style="width: 50px; height: 50px; border-radius: 12px; background: ${benefit.color || '#667eea'}; display: flex; align-items: center; justify-content: center; flex-shrink: 0;">
                        <i class="bi ${benefit.icon || 'bi-gift'}" style="font-size: 24px; color: #fff;"></i>
                    </div>
                    <div style="flex: 1;">
                        <div style="font-size: 16px; font-weight: 600; color: #333; margin-bottom: 4px;">${benefit.name}</div>
                        <div style="font-size: 14px; color: #666;">${benefit.description || ''}</div>
                    </div>
                    <i class="bi bi-arrow-right-circle" style="font-size: 20px; color: ${benefit.color || '#667eea'};"></i>
                </div>
            </a>
        `).join('');
        
        const modalHtml = `
            <div class="modal fade" id="benefitsModal" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered modal-dialog-scrollable">
                    <div class="modal-content" style="border: none; border-radius: 16px; overflow: hidden; box-shadow: 0 12px 40px rgba(0,0,0,0.15);">
                        <!-- 头部 -->
                        <div class="modal-header py-3" style="background: linear-gradient(135deg, #ff6b6b 0%, #feca57 50%, #48dbfb 100%); border: none;">
                            <h5 class="modal-title mb-0" style="color: #fff; font-weight: 700; font-size: 20px; text-shadow: 0 1px 2px rgba(0,0,0,0.1);">
                                <i class="bi bi-gift me-2"></i>最新权益 · 薅羊毛专区
                            </h5>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                        <!-- 内容 -->
                        <div class="modal-body py-4 px-4" style="background: linear-gradient(180deg, #fef9f3 0%, #f8fafc 100%);">
                            <!-- 提示区域 -->
                            <div class="rounded-3 p-3 mb-4" style="background: linear-gradient(135deg, #fff8e1, #ffecb3); color: #e65100; font-size: 14px; line-height: 1.6; border: 1px dashed #ffcc80;">
                                <i class="bi bi-lightbulb me-2"></i>
                                <strong>温馨提示：</strong>以下是精选的优质权益资源，点击即可跳转查看详情。持续更新中~
                            </div>
                            
                            <!-- 权益列表 -->
                            <div class="benefits-list">
                                ${benefitsList}
                            </div>
                            
                            <!-- 底部说明 -->
                            <div class="text-center mt-3" style="color: #999; font-size: 13px;">
                                <i class="bi bi-info-circle me-1"></i>
                                以上权益由系统推荐，如有问题请联系管理员
                            </div>
                        </div>
                        <!-- 底部 -->
                        <div class="modal-footer py-3" style="background: #fff; border-top: 1px solid #e8ecf0;">
                            <button type="button" class="btn" style="background: linear-gradient(135deg, #667eea, #764ba2); color: #fff; border: none; font-size: 15px; padding: 10px 24px; border-radius: 8px;" data-bs-dismiss="modal">
                                <i class="bi bi-x-lg me-1"></i>关闭
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            <style>
                .benefit-item:hover {
                    transform: translateX(5px);
                    box-shadow: 0 4px 16px rgba(0,0,0,0.1) !important;
                }
            </style>
        `;
        
        // 移除已存在的模态框
        const existingModal = document.getElementById('benefitsModal');
        if (existingModal) {
            existingModal.remove();
        }
        
        // 添加新的模态框
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('benefitsModal'));
        modal.show();
        
    } catch (error) {
        console.error('显示权益弹窗失败:', error);
        showToast('获取权益信息失败', 'danger');
    }
}

/**
 * 获取权益信息（使用缓存或重新请求）
 */
async function getBenefitsInfo() {
    // 如果已有缓存的远程版本信息并包含权益，直接使用
    if (remoteVersionInfo && remoteVersionInfo.benefits) {
        return remoteVersionInfo;
    }

    // 从远程获取权益信息
    try {
        const response = await fetch('http://116.196.116.76/version.php', {
            method: 'GET',
            headers: {
                'Accept': 'application/json'
            }
        });

        if (!response.ok) {
            showToast('获取权益信息失败: 网络错误', 'danger');
            return null;
        }

        const result = await response.json();

        if (result.error || !result.success) {
            showToast('获取权益信息失败: ' + (result.message || '未知错误'), 'danger');
            return null;
        }

        remoteVersionInfo = result.data;
        return remoteVersionInfo;

    } catch (error) {
        console.error('获取权益信息失败:', error);
        showToast('获取权益信息失败: ' + error.message, 'danger');
        return null;
    }
}

/**
 * 显示版本信息弹窗
 */
async function showVersionInfo(version) {
    // 尝试获取远程版本信息
    const versionInfo = await getUpdateInfo();
    
    // 构建项目介绍
    const intro = versionInfo?.intro || '此版本为本人利用业余时间开发，功能可能不完善，欢迎大家提出建议和bug，我会尽快修复。此版本纯粹免费，没有任何收费项目，请大家放心使用。如果大家觉得这个项目对你有帮助，可以请我喝杯咖啡，支持我继续开发。';
    
    // 构建版本历史
    let versionHistoryHtml = '';
    if (versionInfo?.versionHistory && versionInfo.versionHistory.length > 0) {
        versionHistoryHtml = versionInfo.versionHistory.map((item, index) => {
            const isLatest = index === 0;
            const bgClass = isLatest ? 'background: linear-gradient(135deg, #e8f5e9, #c8e6c9);' : 'background: #f8f9fa;';
            const borderColor = isLatest ? 'border-left: 4px solid #28a745;' : 'border-left: 4px solid #dee2e6;';
            const badgeStyle = isLatest ? 'background: linear-gradient(135deg, #28a745, #20c997); color: #fff;' : 'background: #6c757d; color: #fff;';
            
            return `
                <div class="mb-3 p-3 rounded-3" style="${bgClass} ${borderColor}">
                    <div class="d-flex align-items-center justify-content-between mb-2">
                        <div>
                            <span class="badge me-2" style="${badgeStyle} font-size: 14px; padding: 6px 12px;">${item.version}</span>
                            ${isLatest ? '<span class="badge bg-success" style="font-size: 12px;">最新</span>' : ''}
                        </div>
                        ${item.date ? `<small style="color: #888; font-size: 13px;"><i class="bi bi-calendar3 me-1"></i>${item.date}</small>` : ''}
                    </div>
                    <ul class="mb-0 ps-3" style="font-size: 14px; line-height: 1.8; color: #444;">
                        ${item.updates.map(u => `<li>${u}</li>`).join('')}
                    </ul>
                </div>
            `;
        }).join('');
    } else {
        // 兜底：使用默认的版本历史
        versionHistoryHtml = `
            <div class="mb-3 p-3 rounded-3" style="background: linear-gradient(135deg, #e8f5e9, #c8e6c9); border-left: 4px solid #28a745;">
                <div class="d-flex align-items-center justify-content-between mb-2">
                    <div>
                        <span class="badge me-2" style="background: linear-gradient(135deg, #28a745, #20c997); color: #fff; font-size: 14px; padding: 6px 12px;">${version}</span>
                        <span class="badge bg-success" style="font-size: 12px;">当前</span>
                    </div>
                </div>
                <ul class="mb-0 ps-3" style="font-size: 14px; line-height: 1.8; color: #444;">
                    <li>当前使用的版本</li>
                </ul>
            </div>
        `;
    }
    
    const modalHtml = `
        <div class="modal fade" id="versionInfoModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered modal-lg modal-dialog-scrollable">
                <div class="modal-content" style="border: none; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.15);">
                    <div class="modal-header py-3" style="background: linear-gradient(135deg, #667eea 0%, #5a67d8 100%); border: none;">
                        <h5 class="modal-title" style="color: #fff; font-weight: 600; font-size: 18px;">
                            <i class="bi bi-info-circle me-2"></i>版本信息
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body py-4" style="background: linear-gradient(180deg, #f0f4ff 0%, #f8fafc 100%); max-height: 70vh;">
                        <!-- 当前版本 -->
                        <div class="mb-4">
                            <h6 style="color: #444; font-size: 16px; font-weight: 600;"><i class="bi bi-tag me-2"></i>当前版本</h6>
                            <div class="p-3 rounded-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                                <h4 class="mb-0" style="color: #5a67d8; font-size: 24px;">${version}</h4>
                            </div>
                        </div>
                        
                        <!-- 版本介绍 -->
                        <div class="mb-4">
                            <h6 style="color: #444; font-size: 16px; font-weight: 600;"><i class="bi bi-star me-2"></i>版本介绍</h6>
                            <div class="p-3 rounded-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                                <div style="font-size: 15px; line-height: 1.7; color: #555;">
                                    <i class="bi bi-check-circle-fill text-success me-2"></i>
                                    <strong>说明</strong>：${intro}
                                </div>
                            </div>
                        </div>
                        
                        <!-- 更新日志 -->
                        <div class="mb-3">
                            <h6 style="color: #444; font-size: 16px; font-weight: 600;"><i class="bi bi-clock-history me-2"></i>更新日志</h6>
                            <div class="rounded-3 p-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06); max-height: 350px; overflow-y: auto;">
                                ${versionHistoryHtml}
                            </div>
                        </div>
                        
                        <!-- 页脚 -->
                        <div class="text-center mt-4">
                            <small style="color: #888; font-size: 14px;">
                                <i class="bi bi-github me-1"></i>
                                闲鱼管理系统 | 让店铺管理更轻松
                            </small>
                        </div>
                    </div>
                    <div class="modal-footer py-3" style="background: #fff; border-top: 1px solid #e8ecf0;">
                        <button type="button" class="btn" style="background: #6c757d; color: #fff; font-size: 15px; padding: 8px 24px;" data-bs-dismiss="modal">关闭</button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // 移除旧的弹窗（如果存在）
    const oldModal = document.getElementById('versionInfoModal');
    if (oldModal) {
        oldModal.remove();
    }

    // 添加新弹窗到页面
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // 显示弹窗
    const modal = document.getElementById('versionInfoModal');
    const modalInstance = new bootstrap.Modal(modal);
    modalInstance.show();

    // 弹窗关闭后删除DOM元素
    modal.addEventListener('hidden.bs.modal', function () {
        modal.remove();
    });
}

// =============================================================================
// 热更新功能
// =============================================================================

/**
 * 检查热更新
 * 调用后端API检查是否有可用的文件更新
 */
async function checkHotUpdate() {
    try {
        const response = await fetch('/api/update/check', {
            method: 'GET',
            headers: {
                'Accept': 'application/json',
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (!response.ok) {
            console.warn('热更新检查请求失败:', response.status);
            return null;
        }
        
        const result = await response.json();
        
        if (!result.success) {
            console.warn('热更新检查返回错误:', result.message);
            return null;
        }

        if (result.data) {
            remoteVersionInfo = result.data;
        }
        
        return result.data;
        
    } catch (error) {
        console.error('热更新检查失败:', error);
        return null;
    }
}

/**
 * 执行热更新
 * 下载并安装所有可用更新
 */
async function performHotUpdate() {
    setHotUpdateButtonsLoading();
    
    try {
        // 先检查是否有更新
        const checkResult = await checkHotUpdate();
        
        if (!checkResult) {
            showToast('检查更新失败，请稍后重试', 'danger');
            resetHotUpdateBtn();
            return;
        }
        
        if (!checkResult.has_update) {
            showToast('已是最新版本，无需更新', 'info');
            resetHotUpdateBtn();
            return;
        }
        
        // 显示确认对话框
        const dialogAction = await showHotUpdateConfirmDialog(checkResult);
        
        if (dialogAction !== 'confirm') {
            if (dialogAction === 'ignore') {
                const ignoredVersion = getHotUpdateTargetVersion(checkResult);
                setIgnoredHotUpdateVersion(ignoredVersion);
                refreshHotUpdatePreferencesMenu();
                refreshHotUpdateButtonState(checkResult);
                updateHotUpdatePreferenceStatus(`已忽略版本 ${ignoredVersion}`, 'success');
            }
            resetHotUpdateBtn();
            return;
        }
        
        // 显示更新进度
        showHotUpdateProgress();
        
        // 执行更新
        const response = await fetch('/api/update/apply', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        const result = await response.json();
        
        // 关闭进度弹窗
        closeHotUpdateProgress();
        
        if (result.success && result.data.success) {
            // 更新成功
            const updateData = result.data;
            const updatedCount = updateData.updated_files?.length || 0;
            const deletedCount = updateData.deleted_files?.length || 0;
            
            if (updateData.needs_restart) {
                // 需要重启
                showHotUpdateRestartDialog(updateData);
            } else {
                // 不需要重启，刷新页面即可
                showToast(`更新成功！更新 ${updatedCount} 个文件，删除 ${deletedCount} 个旧文件`, 'success');
                
                // 3秒后刷新页面
                setTimeout(() => {
                    window.location.reload();
                }, 3000);
            }
        } else {
            showToast('更新失败: ' + (result.detail || result.message || result.data?.message || '未知错误'), 'danger');
        }
        
    } catch (error) {
        console.error('热更新执行失败:', error);
        showToast('更新失败: ' + error.message, 'danger');
        closeHotUpdateProgress();
    } finally {
        resetHotUpdateBtn();
    }
}

/**
 * 重置热更新按钮状态
 */
function resetHotUpdateBtn() {
    const hotUpdateBtn = document.getElementById('hotUpdateBtn');
    if (hotUpdateBtn) {
        hotUpdateBtn.disabled = false;
        hotUpdateBtn.innerHTML = '<i class="bi bi-cloud-download me-1"></i>一键热更新';
    }
    refreshHotUpdateButtonState();
}

function setHotUpdateButtonsLoading() {
    const hotUpdateBtn = document.getElementById('hotUpdateBtn');
    if (hotUpdateBtn) {
        hotUpdateBtn.disabled = true;
        hotUpdateBtn.innerHTML = '<i class="bi bi-arrow-repeat spin me-1"></i>检查更新中...';
    }
    const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');
    const dashboardHotUpdateBtn = document.getElementById('dashboardHotUpdateBtn');
    const dashboardHotUpdateMenuBtn = document.getElementById('dashboardHotUpdateMenuBtn');
    if (dashboardHotUpdateBtn) {
        dashboardHotUpdateBtn.disabled = true;
        dashboardHotUpdateBtn.innerHTML = '<i class="bi bi-arrow-repeat spin me-1"></i>检查更新中...';
    }
    if (dashboardHotUpdateMenuBtn) {
        dashboardHotUpdateMenuBtn.disabled = true;
    }
    if (dashboardHotUpdateGroup) {
        dashboardHotUpdateGroup.classList.add('is-loading');
    }
}

/**
 * 显示热更新确认对话框
 */
async function showHotUpdateConfirmDialog(updateInfo) {
    return new Promise((resolve) => {
        const filesInfo = updateInfo.files && updateInfo.files.length > 0
            ? updateInfo.files.map(f => `<li><code>${f.path}</code> ${f.requires_restart ? '<span class="badge bg-warning">需重启</span>' : ''}</li>`).join('')
            : '<li>本次无新增或覆盖文件</li>';
        const deletedFilesInfo = updateInfo.deleted_files && updateInfo.deleted_files.length > 0
            ? updateInfo.deleted_files.map(f => `<li><code>${f.path}</code> ${f.requires_restart ? '<span class="badge bg-warning">需重启</span>' : ''}</li>`).join('')
            : '';
        
        const totalSizeKB = (updateInfo.total_size / 1024).toFixed(2);
        const deletedCount = updateInfo.deleted_files_count || 0;
        const deleteSection = deletedCount > 0 ? `
                            <div class="mb-3">
                                <div style="color: #444; font-size: 14px; font-weight: 600; margin-bottom: 8px;">
                                    <i class="bi bi-trash me-1"></i>将删除以下旧文件：
                                </div>
                                <div style="max-height: 120px; overflow-y: auto; background: #fff3f3; border-radius: 8px; padding: 12px; border: 1px solid #f5c2c7;">
                                    <ul class="list-unstyled mb-0" style="font-size: 13px;">
                                        ${deletedFilesInfo}
                                    </ul>
                                </div>
                            </div>
        ` : '';
        
        const modalHtml = `
            <div class="modal fade" id="hotUpdateConfirmModal" tabindex="-1">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content" style="border: none; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.15);">
                        <div class="modal-header py-3" style="background: linear-gradient(135deg, #28a745 0%, #20c997 100%); border: none;">
                            <h5 class="modal-title mb-0" style="color: #fff; font-weight: 600; font-size: 18px;">
                                <i class="bi bi-cloud-download me-2"></i>确认热更新
                            </h5>
                            <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body py-4 px-4" style="background: linear-gradient(180deg, #f0fff4 0%, #f8fafc 100%);">
                            <div class="d-flex align-items-center justify-content-between mb-3 p-3 rounded-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                                <div>
                                    <div style="color: #666; font-size: 14px;">当前版本</div>
                                    <div style="font-size: 18px; font-weight: 600; color: #6c757d;">${updateInfo.current_version}</div>
                                </div>
                                <i class="bi bi-arrow-right" style="color: #28a745; font-size: 1.5rem;"></i>
                                <div>
                                    <div style="color: #28a745; font-size: 14px;">目标版本</div>
                                    <div style="font-size: 18px; font-weight: 600; color: #28a745;">${updateInfo.new_version}</div>
                                </div>
                            </div>
                            
                            <div class="mb-3 p-3 rounded-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span style="color: #666;"><i class="bi bi-files me-1"></i>更新文件数</span>
                                    <span style="font-weight: 600; color: #333;">${updateInfo.files_count} 个</span>
                                </div>
                                <div class="d-flex justify-content-between align-items-center mb-2">
                                    <span style="color: #666;"><i class="bi bi-trash me-1"></i>删除旧文件数</span>
                                    <span style="font-weight: 600; color: #333;">${deletedCount} 个</span>
                                </div>
                                <div class="d-flex justify-content-between align-items-center">
                                    <span style="color: #666;"><i class="bi bi-hdd me-1"></i>下载大小</span>
                                    <span style="font-weight: 600; color: #333;">${totalSizeKB} KB</span>
                                </div>
                            </div>
                            
                            <div class="mb-3">
                                <div style="color: #444; font-size: 14px; font-weight: 600; margin-bottom: 8px;">
                                    <i class="bi bi-list-check me-1"></i>将更新以下文件：
                                </div>
                                <div style="max-height: 150px; overflow-y: auto; background: #f8f9fa; border-radius: 8px; padding: 12px;">
                                    <ul class="list-unstyled mb-0" style="font-size: 13px;">
                                        ${filesInfo}
                                    </ul>
                                </div>
                            </div>
                            ${deleteSection}
                            
                            <div class="rounded-3 p-3" style="background: linear-gradient(135deg, #fff3cd, #ffeeba); color: #856404; font-size: 14px;">
                                <i class="bi bi-exclamation-triangle me-2"></i>
                                <strong>提示：</strong>更新和删除前都会自动备份原文件，如遇问题可恢复。
                            </div>
                        </div>
                        <div class="modal-footer py-3" style="background: #fff; border-top: 1px solid #e8ecf0;">
                            <button type="button" class="btn btn-link text-decoration-none me-auto px-0" style="color: #6c757d;" id="hotUpdateIgnoreBtn">
                                忽略此版本
                            </button>
                            <button type="button" class="btn" style="background: #f0f0f0; color: #666; border: none; font-size: 15px; padding: 8px 20px;" data-bs-dismiss="modal" id="hotUpdateCancelBtn">
                                本次跳过
                            </button>
                            <button type="button" class="btn" style="background: linear-gradient(135deg, #28a745, #20c997); color: #fff; border: none; font-size: 15px; padding: 8px 20px;" id="hotUpdateConfirmBtn">
                                <i class="bi bi-check-lg me-1"></i>立即更新
                            </button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // 移除已存在的模态框
        const existingModal = document.getElementById('hotUpdateConfirmModal');
        if (existingModal) {
            existingModal.remove();
        }
        
        document.body.insertAdjacentHTML('beforeend', modalHtml);
        
        const modalElement = document.getElementById('hotUpdateConfirmModal');
        const modal = new bootstrap.Modal(modalElement);
        let resolved = false;

        const finish = (action) => {
            if (resolved) return;
            resolved = true;
            modal.hide();
            resolve(action);
        };
        
        // 绑定按钮事件
        document.getElementById('hotUpdateConfirmBtn').onclick = () => {
            finish('confirm');
        };
        
        document.getElementById('hotUpdateCancelBtn').onclick = () => {
            finish('skip');
        };

        document.getElementById('hotUpdateIgnoreBtn').onclick = () => {
            finish('ignore');
        };
        
        modalElement.addEventListener('hidden.bs.modal', () => {
            modalElement.remove();
            if (!resolved) {
                resolved = true;
                resolve('skip');
            }
        });
        
        modal.show();
    });
}

/**
 * 显示热更新进度
 */
function showHotUpdateProgress() {
    const modalHtml = `
        <div class="modal fade" id="hotUpdateProgressModal" tabindex="-1" data-bs-backdrop="static" data-bs-keyboard="false">
            <div class="modal-dialog modal-dialog-centered modal-sm">
                <div class="modal-content" style="border: none; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.15);">
                    <div class="modal-body py-4 px-4 text-center" style="background: linear-gradient(180deg, #f0f4ff 0%, #f8fafc 100%);">
                        <div class="spinner-border text-primary mb-3" role="status" style="width: 3rem; height: 3rem;">
                            <span class="visually-hidden">Loading...</span>
                        </div>
                        <h5 style="color: #333; font-weight: 600;">正在更新...</h5>
                        <p id="hotUpdateProgressText" style="color: #666; font-size: 14px; margin-bottom: 0;">正在下载更新文件</p>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // 移除已存在的模态框
    const existingModal = document.getElementById('hotUpdateProgressModal');
    if (existingModal) {
        existingModal.remove();
    }
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    const modal = new bootstrap.Modal(document.getElementById('hotUpdateProgressModal'));
    modal.show();
}

/**
 * 关闭热更新进度
 */
function closeHotUpdateProgress() {
    const modal = document.getElementById('hotUpdateProgressModal');
    if (modal) {
        const bsModal = bootstrap.Modal.getInstance(modal);
        if (bsModal) {
            bsModal.hide();
        }
        setTimeout(() => modal.remove(), 300);
    }
}

/**
 * 显示需要重启的对话框
 */
function showHotUpdateRestartDialog(updateData) {
    const modalHtml = `
        <div class="modal fade" id="hotUpdateRestartModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content" style="border: none; border-radius: 14px; overflow: hidden; box-shadow: 0 8px 32px rgba(0,0,0,0.15);">
                    <div class="modal-header py-3" style="background: linear-gradient(135deg, #ffc107 0%, #ff9800 100%); border: none;">
                        <h5 class="modal-title mb-0" style="color: #fff; font-weight: 600; font-size: 18px;">
                            <i class="bi bi-arrow-repeat me-2"></i>更新完成，需要重启
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body py-4 px-4" style="background: linear-gradient(180deg, #fffbf0 0%, #f8fafc 100%);">
                        <div class="text-center mb-4">
                            <i class="bi bi-check-circle-fill" style="font-size: 64px; color: #28a745;"></i>
                        </div>
                        
                        <div class="mb-3 p-3 rounded-3" style="background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.06);">
                            <p style="color: #333; font-size: 16px; margin-bottom: 8px;">
                                <strong>更新成功！</strong>
                            </p>
                            <p style="color: #666; font-size: 14px; margin-bottom: 0;">
                                共更新 <strong>${updateData.updated_files.length}</strong> 个文件到版本 <strong>${updateData.new_version}</strong>
                            </p>
                        </div>
                        
                        <div class="rounded-3 p-3" style="background: linear-gradient(135deg, #fff3cd, #ffeeba); color: #856404; font-size: 14px;">
                            <i class="bi bi-exclamation-triangle me-2"></i>
                            <strong>注意：</strong>部分更新的文件需要重启应用才能生效。
                        </div>
                    </div>
                    <div class="modal-footer py-3" style="background: #fff; border-top: 1px solid #e8ecf0;">
                        <button type="button" class="btn" style="background: #f0f0f0; color: #666; border: none; font-size: 15px; padding: 8px 20px;" data-bs-dismiss="modal">
                            稍后重启
                        </button>
                        <button type="button" class="btn" style="background: linear-gradient(135deg, #ffc107, #ff9800); color: #fff; border: none; font-size: 15px; padding: 8px 20px;" onclick="restartApplication()">
                            <i class="bi bi-arrow-repeat me-1"></i>立即重启
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    // 移除已存在的模态框
    const existingModal = document.getElementById('hotUpdateRestartModal');
    if (existingModal) {
        existingModal.remove();
    }
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    
    const modal = new bootstrap.Modal(document.getElementById('hotUpdateRestartModal'));
    modal.show();
}

/**
 * 重启应用
 */
async function restartApplication() {
    try {
        showToast('正在重启应用...', 'info');
        
        const response = await fetch('/api/update/restart', {
            method: 'POST',
            headers: {
                'Accept': 'application/json',
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        const result = await response.json();
        
        if (result.success) {
            showToast('应用正在重启，页面将在5秒后自动刷新...', 'success');
            
            // 5秒后刷新页面
            setTimeout(() => {
                window.location.reload();
            }, 5000);
        } else {
            showToast('重启失败: ' + result.message, 'danger');
        }
        
    } catch (error) {
        console.error('重启应用失败:', error);
        showToast('重启失败: ' + error.message, 'danger');
    }
}

// 添加CSS动画
const hotUpdateStyle = document.createElement('style');
hotUpdateStyle.textContent = `
    @keyframes spin {
        from { transform: rotate(0deg); }
        to { transform: rotate(360deg); }
    }
    .spin {
        animation: spin 1s linear infinite;
    }
`;
document.head.appendChild(hotUpdateStyle);

// ==================== 在线客服IM功能 ====================

let chatCurrentCookieId = '';
let chatCurrentChatId = '';
let chatCurrentToUserId = '';
let chatCurrentSenderName = '';
let chatCurrentItemId = '';
let chatSessionsCache = [];
let chatAccountsCache = [];
let chatCurrentAccount = null;
let chatSessionsNextCursor = null;
let chatSessionsHasMore = false;
let chatMessagesNextCursor = null;
let chatMessagesHasMore = false;
let chatMessagesSource = 'remote_im';
let chatOldestMsgId = null;
let chatSseAbortController = null;
let chatSseRetryCount = 0;
let chatSseShouldRun = false;
let chatUserInfoCache = {};
let chatUserInfoHydrationTimer = null;
const CHAT_USER_INFO_MISS_TTL_MS = 10 * 60 * 1000;
let chatBlacklistState = { loading: false, blacklisted: false, can_unblock: false, scope: '', record: null, account_record: null };

function buildSafeCheckboxId(prefix, rawValue) {
    const normalized = String(rawValue || '')
        .trim()
        .replace(/[^a-zA-Z0-9_-]+/g, '_')
        .replace(/^_+|_+$/g, '');
    return `${prefix}_${normalized || 'item'}`;
}

function normalizeChatSessionPreview(content, contentType) {
    if (Number(contentType) === 2) return '[图片]';
    const text = String(content || '').trim();
    if (!text) return '[暂无文本内容]';
    const hiddenMarkers = new Set(['[系统消息]', '[空消息]', '点击补拉该会话历史消息']);
    if (hiddenMarkers.has(text)) return '[系统/占位消息]';
    return text;
}

function resolveSessionDisplayName(session) {
    return session?.fish_nick
        || session?.buyer_name_resolved
        || session?.buyer_name
        || (session?.direction === 2 ? (session?.sender_name || session?.sender_id || session?.chat_id) : (session?.sender_name || session?.chat_id))
        || session?.chat_id
        || '-';
}

function resolveSessionAvatar(session) {
    if (session?.avatar) {
        return { type: 'image', value: session.avatar };
    }
    const displayName = resolveSessionDisplayName(session);
    return { type: 'text', value: (displayName || '?').charAt(0).toUpperCase() };
}

function buildChatUserInfoCacheKey(cookieId, chatId) {
    return `${String(cookieId || '').trim()}::${String(chatId || '').trim().replace(/@goofish$/i, '')}`;
}

function isValidChatDisplayName(value) {
    const text = String(value || '').trim();
    if (!text || text === '-' || text === '未知用户') return false;
    if (/^\d+$/.test(text)) return false;
    return !['工作台通知', '订单', '交易消息', '买家', '全部'].includes(text);
}

function applyChatUserInfoToSession(session, info) {
    if (!session || !info) return { session, changed: false };
    const updates = {};
    const avatar = String(info.avatar || '').trim();
    const nick = String(info.fish_nick || info.buyer_name_resolved || '').trim();
    const senderId = String(info.sender_id || '').trim();

    if (avatar && avatar !== String(session.avatar || '')) {
        updates.avatar = avatar;
    }
    if (isValidChatDisplayName(nick)) {
        if (nick !== String(session.fish_nick || '')) updates.fish_nick = nick;
        if (nick !== String(session.buyer_name_resolved || '')) updates.buyer_name_resolved = nick;
        if (!isValidChatDisplayName(session.buyer_name) || String(session.buyer_name || '').trim() === String(session.buyer_id || '').trim()) {
            updates.buyer_name = nick;
        }
        if (!isValidChatDisplayName(session.sender_name) || String(session.sender_name || '').trim() === String(session.sender_id || '').trim()) {
            updates.sender_name = nick;
        }
    }
    if (senderId && !session.sender_id) {
        updates.sender_id = senderId;
    }

    return Object.keys(updates).length > 0
        ? { session: { ...session, ...updates }, changed: true }
        : { session, changed: false };
}

function applyCachedChatUserInfosToSessions() {
    if (!chatCurrentCookieId || !chatSessionsCache.length) return false;
    let changed = false;
    chatSessionsCache = chatSessionsCache.map(session => {
        const cacheKey = buildChatUserInfoCacheKey(chatCurrentCookieId, session?.chat_id);
        const cached = chatUserInfoCache[cacheKey];
        if (!cached || cached.__miss) return session;
        const result = applyChatUserInfoToSession(session, cached);
        changed = changed || result.changed;
        return result.session;
    });
    return changed;
}

function shouldHydrateChatSessionUserInfo(session) {
    if (!chatCurrentCookieId || !session?.chat_id) return false;
    const cacheKey = buildChatUserInfoCacheKey(chatCurrentCookieId, session.chat_id);
    const cached = chatUserInfoCache[cacheKey];
    if (cached?.__miss && Date.now() - Number(cached.cachedAt || 0) < CHAT_USER_INFO_MISS_TTL_MS) return false;

    const displayName = resolveSessionDisplayName(session);
    return !session.avatar || !isValidChatDisplayName(displayName);
}

function syncActiveChatHeaderName() {
    if (!chatCurrentChatId) return;
    const currentSession = chatSessionsCache.find(session => session.chat_id === chatCurrentChatId);
    if (!currentSession) return;
    chatCurrentSenderName = resolveSessionDisplayName(currentSession);
    const headerName = document.getElementById('chatHeaderName');
    if (headerName) headerName.textContent = chatCurrentSenderName;
}

function getChatBlacklistScopeLabel(scope) {
    return { item: '商品级', account: '账号级', user: '用户级' }[scope] || '其他范围';
}

function resetChatBlacklistState() {
    chatBlacklistState = { loading: false, blacklisted: false, can_unblock: false, scope: '', record: null, account_record: null };
    renderChatBlacklistButton();
}

function renderChatBlacklistButton() {
    const btn = document.getElementById('chatBlacklistBtn');
    const text = document.getElementById('chatBlacklistBtnText');
    if (!btn) return;

    const hasBuyer = Boolean(chatCurrentCookieId && chatCurrentChatId && chatCurrentToUserId);
    btn.classList.remove('btn-outline-danger', 'btn-danger', 'btn-outline-secondary', 'btn-outline-warning');

    if (!hasBuyer) {
        btn.disabled = true;
        btn.title = '缺少买家ID，无法拉黑';
        btn.classList.add('btn-outline-secondary');
        btn.innerHTML = '<i class="bi bi-person-slash"></i><span class="d-none d-xl-inline ms-1" id="chatBlacklistBtnText">拉黑</span>';
        return;
    }

    if (chatBlacklistState.loading) {
        btn.disabled = true;
        btn.title = '正在查询黑名单状态';
        btn.classList.add('btn-outline-secondary');
        btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span><span class="d-none d-xl-inline ms-1" id="chatBlacklistBtnText">检查中</span>';
        return;
    }

    if (chatBlacklistState.blacklisted) {
        if (chatBlacklistState.can_unblock) {
            btn.disabled = false;
            btn.title = '解除当前账号级黑名单';
            btn.classList.add('btn-outline-warning');
            btn.innerHTML = '<i class="bi bi-person-check"></i><span class="d-none d-xl-inline ms-1" id="chatBlacklistBtnText">解除拉黑</span>';
        } else {
            btn.disabled = true;
            btn.title = `已命中${getChatBlacklistScopeLabel(chatBlacklistState.scope)}黑名单，请到黑名单管理解除`;
            btn.classList.add('btn-outline-secondary');
            btn.innerHTML = '<i class="bi bi-shield-lock"></i><span class="d-none d-xl-inline ms-1" id="chatBlacklistBtnText">已拉黑</span>';
        }
        return;
    }

    btn.disabled = false;
    btn.title = '将当前买家加入当前账号黑名单';
    btn.classList.add('btn-outline-danger');
    btn.innerHTML = '<i class="bi bi-person-slash"></i><span class="d-none d-xl-inline ms-1" id="chatBlacklistBtnText">拉黑</span>';
    if (text) text.textContent = '拉黑';
}

async function refreshChatBlacklistStatus() {
    if (!chatCurrentCookieId || !chatCurrentToUserId) {
        resetChatBlacklistState();
        return;
    }

    const cookieId = chatCurrentCookieId;
    const buyerId = chatCurrentToUserId;
    chatBlacklistState = { ...chatBlacklistState, loading: true };
    renderChatBlacklistButton();

    try {
        const token = getAuthToken();
        const response = await fetch(`${apiBase}/api/chat/blacklist-status?cookie_id=${encodeURIComponent(cookieId)}&buyer_id=${encodeURIComponent(buyerId)}`, {
            headers: token ? { 'Authorization': `Bearer ${token}` } : {},
            cache: 'no-store',
        });
        if (response.status === 401) {
            stopChatStream();
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const result = await response.json();
        if (cookieId !== chatCurrentCookieId || buyerId !== chatCurrentToUserId) return;
        const data = result?.data || {};
        chatBlacklistState = {
            loading: false,
            blacklisted: Boolean(data.blacklisted),
            can_unblock: Boolean(data.can_unblock),
            scope: data.scope || '',
            record: data.record || null,
            account_record: data.account_record || null,
        };
    } catch (error) {
        console.debug('查询客服黑名单状态失败:', error);
        chatBlacklistState = { ...chatBlacklistState, loading: false };
    }
    renderChatBlacklistButton();
}

async function toggleChatBlacklist() {
    if (!chatCurrentCookieId || !chatCurrentToUserId) {
        showToast('当前会话缺少买家ID，无法拉黑', 'warning');
        return;
    }
    if (chatBlacklistState.blacklisted && !chatBlacklistState.can_unblock) {
        showToast(`该买家命中${getChatBlacklistScopeLabel(chatBlacklistState.scope)}黑名单，请到黑名单管理解除`, 'warning');
        return;
    }

    const action = chatBlacklistState.blacklisted ? 'unblock' : 'block';
    const actionLabel = action === 'block' ? '拉黑' : '解除拉黑';
    const confirmMessage = action === 'block'
        ? `确认将买家 ${chatCurrentSenderName || chatCurrentToUserId} 加入当前账号黑名单吗？\n\n加入后自动回复、客服发送和发货流程都会拦截该买家。`
        : `确认解除买家 ${chatCurrentSenderName || chatCurrentToUserId} 的当前账号黑名单吗？`;
    if (!window.confirm(confirmMessage)) return;

    chatBlacklistState = { ...chatBlacklistState, loading: true };
    renderChatBlacklistButton();

    try {
        const result = await fetchJSON(`${apiBase}/api/chat/blacklist-toggle`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cookie_id: chatCurrentCookieId,
                buyer_id: chatCurrentToUserId,
                buyer_nick: chatCurrentSenderName || '',
                action,
                reason: '在线客服手动拉黑',
            }),
        });
        const data = result?.data || {};
        chatBlacklistState = {
            loading: false,
            blacklisted: Boolean(data.blacklisted),
            can_unblock: Boolean(data.can_unblock),
            scope: data.scope || '',
            record: data.record || null,
            account_record: data.account_record || null,
        };
        showToast(result.message || `${actionLabel}成功`, result.success === false ? 'warning' : 'success');
    } catch (error) {
        console.error(`${actionLabel}失败:`, error);
        chatBlacklistState = { ...chatBlacklistState, loading: false };
        showToast(`${actionLabel}失败`, 'danger');
    }
    renderChatBlacklistButton();
}

function rerenderChatSessionsAfterUserInfoUpdate() {
    syncActiveChatHeaderName();
    const keyword = String(document.getElementById('chatSearchInput')?.value || '').trim();
    if (keyword) {
        filterChatSessions();
    } else {
        renderChatSessions(chatSessionsCache);
    }
}

function applyChatUserInfosToSessions(users) {
    if (!users || typeof users !== 'object') return false;
    let changed = false;
    chatSessionsCache = chatSessionsCache.map(session => {
        const chatId = String(session?.chat_id || '').trim().replace(/@goofish$/i, '');
        const info = users[chatId];
        if (!info) return session;
        const result = applyChatUserInfoToSession(session, info);
        changed = changed || result.changed;
        return result.session;
    });
    if (changed) {
        rerenderChatSessionsAfterUserInfoUpdate();
    }
    return changed;
}

function scheduleChatUserInfoHydration(sessions) {
    if (chatUserInfoHydrationTimer) {
        clearTimeout(chatUserInfoHydrationTimer);
    }
    chatUserInfoHydrationTimer = setTimeout(() => {
        chatUserInfoHydrationTimer = null;
        hydrateChatUserInfos(sessions);
    }, 120);
}

async function hydrateChatUserInfos(sessions) {
    if (!chatCurrentCookieId || !Array.isArray(sessions) || !sessions.length) return;
    if (applyCachedChatUserInfosToSessions()) {
        rerenderChatSessionsAfterUserInfoUpdate();
        return;
    }

    const seen = new Set();
    const queries = [];
    for (const session of sessions) {
        const chatId = String(session?.chat_id || '').trim().replace(/@goofish$/i, '');
        if (!chatId || seen.has(chatId) || !shouldHydrateChatSessionUserInfo(session)) continue;
        seen.add(chatId);
        queries.push({
            chat_id: chatId,
            sender_id: session.sender_id || session.buyer_id || '',
            buyer_id: session.buyer_id || '',
            sender_name: session.sender_name || '',
            buyer_name: session.buyer_name || session.buyer_name_resolved || session.fish_nick || '',
            session_type: session.session_type || 1,
            message_id: session.message_id || '',
        });
        if (queries.length >= 24) break;
    }
    if (!queries.length) return;

    try {
        const token = getAuthToken();
        const response = await fetch(`${apiBase}/api/chat/avatars`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                ...(token ? { 'Authorization': `Bearer ${token}` } : {}),
            },
            body: JSON.stringify({ cookie_id: chatCurrentCookieId, queries }),
        });
        if (response.status === 401) {
            stopChatStream();
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }
        if (!response.ok) return;
        const result = await response.json();
        const users = result?.users || {};
        const now = Date.now();

        queries.forEach(query => {
            const chatId = String(query.chat_id || '').trim();
            const cacheKey = buildChatUserInfoCacheKey(chatCurrentCookieId, chatId);
            const info = users[chatId];
            chatUserInfoCache[cacheKey] = info && (info.avatar || info.fish_nick || info.buyer_name_resolved)
                ? { ...info, cachedAt: now }
                : { __miss: true, cachedAt: now };
        });

        applyChatUserInfosToSessions(users);
    } catch (error) {
        console.debug('批量补全客服头像失败:', error);
    }
}

function resolveSessionMessagePreview(session) {
    const messagePreview = normalizeChatSessionPreview(session?.content, session?.content_type);
    if (messagePreview && messagePreview !== '[系统/占位消息]' && messagePreview !== '[暂无文本内容]') {
        return messagePreview;
    }
    return '';
}

function resolveSessionPreview(session) {
    return resolveSessionMessagePreview(session)
        || session?.order_status_name
        || session?.item_title
        || '[暂无文本内容]';
}

function resolveSessionSubMeta(session) {
    const preview = resolveSessionPreview(session);
    const parts = [];
    [session?.item_title, session?.order_status_name, session?.item_tips].forEach(value => {
        const text = String(value || '').trim();
        if (text && text !== preview && !parts.includes(text)) {
            parts.push(text);
        }
    });
    return parts.join(' · ');
}

function getChatSessionState(session) {
    return {
        tag: '',
        preview: resolveSessionPreview(session),
        submeta: resolveSessionSubMeta(session),
        className: ''
    };
}

function updateChatHeaderMeta(session) {
    const headerItemId = document.getElementById('chatHeaderItemId');
    const headerMeta = document.getElementById('chatHeaderMeta');
    if (headerItemId) {
        headerItemId.textContent = session?.item_id ? `商品: ${session.item_id}` : '';
    }
    if (!headerMeta) return;
    const parts = [];
    if (session?.item_title) parts.push(session.item_title);
    if (session?.item_price) parts.push(`￥${session.item_price}`);
    if (session?.order_status_name) parts.push(session.order_status_name);
    if (session?.item_tips) parts.push(session.item_tips);
    headerMeta.textContent = parts.join(' · ');
}

function scoreChatSession(session) {
    const preview = normalizeChatSessionPreview(session?.content, session?.content_type);
    let score = 0;
    if (preview !== '[系统/占位消息]' && preview !== '[暂无文本内容]') score += 20;
    if (String(session?.buyer_name || '').trim()) score += 8;
    if (String(session?.item_id || '').trim()) score += 4;
    if (String(session?.created_at || '').trim()) score += 2;
    return score;
}

function sortChatSessions(sessions) {
    return [...(sessions || [])].sort((a, b) => {
        const timeDiff = String(b?.created_at || b?.lastMessageTime || '').localeCompare(String(a?.created_at || a?.lastMessageTime || ''));
        if (timeDiff !== 0) return timeDiff;
        return scoreChatSession(b) - scoreChatSession(a);
    });
}

function mergeChatSessionLists(primarySessions, secondarySessions) {
    const merged = [];
    const seen = new Set();
    [...(primarySessions || []), ...(secondarySessions || [])].forEach(session => {
        const chatId = String(session?.chat_id || '').trim();
        if (!chatId || seen.has(chatId)) return;
        seen.add(chatId);
        merged.push(session);
    });
    return sortChatSessions(merged);
}

function getChatAccountStatus(account) {
    const state = account?.connection_state || 'not_running';
    if (!account?.enabled) return { label: '已断开', className: 'offline' };
    if (account?.connected) return { label: '已连接', className: 'online' };
    if (state === 'connecting' || state === 'reconnecting') return { label: '连接中', className: 'pending' };
    if (account?.running) return { label: '运行中', className: 'pending' };
    return { label: '未连接', className: 'offline' };
}

async function refreshChatAccounts() {
    const body = document.getElementById('chatAccountsBody');
    if (!body) return;
    body.innerHTML = '<div class="text-center text-muted py-4 small"><div class="spinner-border spinner-border-sm"></div></div>';
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/accounts`);
        if (!result.success) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
            return;
        }
        const accounts = result.accounts || [];
        chatAccountsCache = accounts;
        chatCurrentAccount = accounts.find(account => account.id === chatCurrentCookieId) || null;
        if (!accounts.length) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">暂无可用账号</div>';
            return;
        }
        body.innerHTML = '';
        accounts.forEach(account => {
            const status = getChatAccountStatus(account);
            const actionLabel = account.enabled && (account.running || account.connected) ? '断开' : '连接';
            const actionIcon = actionLabel === '断开' ? 'bi-plug' : 'bi-play-circle';
            const div = document.createElement('div');
            div.className = 'chat-account-item' + (account.id === chatCurrentCookieId ? ' active' : '');
            div.innerHTML = `
                <div class="chat-account-dot ${status.className}"></div>
                <div class="chat-account-main">
                    <div class="chat-account-name" title="${escapeHtml(account.id)}">${escapeHtml(account.name || account.id)}</div>
                    <div class="chat-account-status ${status.className}" title="${escapeHtml(account.message_stream_note || '')}">${escapeHtml(status.label)}</div>
                </div>
                <button class="chat-account-action" title="${escapeHtml(actionLabel)}">
                    <i class="bi ${actionIcon}"></i>
                </button>
            `;
            div.onclick = () => selectChatAccount(account.id);
            div.querySelector('.chat-account-action')?.addEventListener('click', event => {
                event.stopPropagation();
                toggleChatAccountConnection(account.id, actionLabel === '断开');
            });
            body.appendChild(div);
        });
    } catch (error) {
        console.error('加载账号列表失败:', error);
        body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
    }
}

async function toggleChatAccountConnection(cookieId, disconnect = false) {
    try {
        const endpoint = disconnect ? 'disconnect' : 'connect';
        const result = await fetchJSON(`${apiBase}/api/chat/${endpoint}/${encodeURIComponent(cookieId)}`, { method: 'POST' });
        if (result.success) {
            showToast(result.message || (disconnect ? '已断开连接' : '连接已启动'), 'success');
        } else {
            showToast(result.detail || result.message || '操作失败', 'danger');
        }
    } catch (error) {
        console.error('切换客服连接失败:', error);
        showToast(disconnect ? '断开失败' : '连接失败', 'danger');
    }
    await refreshChatAccounts();
    if (cookieId === chatCurrentCookieId) {
        await refreshChatSessions();
    }
}

async function selectChatAccount(cookieId) {
    if (chatUserInfoHydrationTimer) {
        clearTimeout(chatUserInfoHydrationTimer);
        chatUserInfoHydrationTimer = null;
    }
    chatCurrentCookieId = cookieId;
    chatCurrentAccount = chatAccountsCache.find(account => account.id === cookieId) || null;
    chatCurrentChatId = '';
    chatCurrentToUserId = '';
    chatCurrentSenderName = '';
    chatCurrentItemId = '';
    resetChatBlacklistState();
    chatSessionsNextCursor = null;
    chatSessionsHasMore = false;
    chatMessagesNextCursor = null;
    chatMessagesHasMore = false;
    chatOldestMsgId = null;
    const placeholder = document.getElementById('chatMainPlaceholder');
    const active = document.getElementById('chatActiveArea');
    if (placeholder) placeholder.classList.remove('d-none');
    if (active) active.classList.add('d-none');
    hideReplyPanel();
    await refreshChatAccounts();
    await refreshChatSessions();
}

async function refreshChatSessions(append = false) {
    const body = document.getElementById('chatSessionsBody');
    if (!body) return;
    if (!chatCurrentCookieId) {
        body.innerHTML = '<div class="text-center text-muted py-4 small">请先选择账号</div>';
        chatSessionsCache = [];
        return;
    }
    if (!append) {
        chatSessionsNextCursor = null;
        chatSessionsHasMore = false;
        body.innerHTML = '<div class="text-center text-muted py-4 small"><div class="spinner-border spinner-border-sm"></div></div>';
    }
    try {
        let url = `${apiBase}/api/chat/sessions?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&include_order_fallback=true&remote=true&limit=60`;
        if (append && chatSessionsNextCursor) {
            url += `&cursor=${encodeURIComponent(chatSessionsNextCursor)}`;
        }
        const result = await fetchJSON(url);
        if (!result.success) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
            return;
        }
        chatSessionsNextCursor = result.next_cursor || null;
        chatSessionsHasMore = Boolean(result.has_more && chatSessionsNextCursor);
        const incomingSessions = sortChatSessions(result.sessions || []);
        chatSessionsCache = append ? mergeChatSessionLists(chatSessionsCache, incomingSessions) : incomingSessions;
        if (!append && result.remote_error) {
            console.debug('直连IM会话提示:', result.remote_error);
        }
        if (!chatSessionsCache.length) {
            const hint = result.remote_error || '暂无会话记录';
            body.innerHTML = `<div class="text-center text-muted py-4 small">${escapeHtml(hint)}</div>`;
            return;
        }
        renderChatSessions(chatSessionsCache);
    } catch (error) {
        console.error('获取会话列表失败:', error);
        body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
    }
}

function loadMoreChatSessions() {
    if (!chatSessionsHasMore || !chatSessionsNextCursor) return;
    refreshChatSessions(true);
}

function buildChatSessionsFromOrdersData(orders, cookieId) {
    const sessions = [];
    const seen = new Set();
    (orders || []).forEach(order => {
        if (String(order.cookie_id || '') !== String(cookieId || '')) return;
        const sid = String(order.sid || '').trim();
        if (!sid) return;
        const chatId = sid.split('@')[0];
        if (!chatId || seen.has(chatId)) return;
        seen.add(chatId);
        sessions.push({
            chat_id: chatId,
            sender_id: order.buyer_id || '',
            buyer_id: order.buyer_id || '',
            sender_name: order.buyer_nick || order.buyer_id || chatId,
            buyer_name: order.buyer_nick || '',
            content: '',
            content_type: 1,
            item_id: order.item_id || '',
            direction: 2,
            created_at: order.updated_at || order.platform_created_at || order.created_at || '',
        });
    });
    sessions.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    return sessions;
}

async function enrichSessionsWithOrdersFallback(existingSessions) {
    const sessions = Array.isArray(existingSessions) ? [...existingSessions] : [];
    if (!chatCurrentCookieId) return sessions;
    const hasOnlySparseLocalSessions = sessions.length <= 1;
    if (!hasOnlySparseLocalSessions) {
        return sortChatSessions(sessions);
    }
    try {
        const ordersResult = await fetchJSON(`${apiBase}/api/orders`);
        const orderSessions = buildChatSessionsFromOrdersData(ordersResult?.data || [], chatCurrentCookieId);
        return mergeChatSessionLists(sessions, orderSessions);
    } catch (error) {
        console.debug('从订单补充会话列表失败:', error);
    }
    return sortChatSessions(sessions);
}

function renderChatSessions(sessions) {
    const body = document.getElementById('chatSessionsBody');
    if (!body) return;
    if (!sessions.length) {
        body.innerHTML = '<div class="text-center text-muted py-4 small">暂无会话</div>';
        return;
    }
    body.innerHTML = '';
    sessions.forEach(session => {
        const div = document.createElement('div');
        div.className = 'chat-session-item' + (session.chat_id === chatCurrentChatId ? ' active' : '');
        const displayName = resolveSessionDisplayName(session);
        const avatar = resolveSessionAvatar(session);
        const sessionState = getChatSessionState(session);
        const preview = String(sessionState.preview || resolveSessionPreview(session)).substring(0, 42);
        const baseSubMeta = String(sessionState.submeta || '').trim();
        const priceMeta = session.item_price ? `<span class="chat-session-price">￥${escapeHtml(String(session.item_price))}</span>` : '';
        const unread = Number(session.unread_count || 0);
        const sourceTag = session.source === 'remote_im' ? '<span class="chat-session-source">IM</span>' : '';
        div.innerHTML = `
            <div class="chat-session-avatar">${avatar.type === 'image' ? `<img src="${escapeHtml(avatar.value)}" alt="avatar" class="chat-session-avatar-image">` : escapeHtml(avatar.value)}</div>
            <div class="chat-session-info">
                <div class="chat-session-title-row">
                    <div class="chat-session-name">${escapeHtml(displayName)}</div>
                    ${sourceTag}
                    ${unread > 0 ? `<span class="chat-session-unread">${unread > 99 ? '99+' : unread}</span>` : ''}
                </div>
                <div class="chat-session-preview">${escapeHtml(preview)}</div>
                <div class="chat-session-submeta">${escapeHtml(baseSubMeta)}${priceMeta}</div>
            </div>
            <div class="chat-session-time">${escapeHtml(formatChatTime(session.created_at || session.lastMessageTime))}</div>
        `;
        div.onclick = () => selectChatSession(session);
        body.appendChild(div);
    });
    if (chatSessionsHasMore) {
        const more = document.createElement('button');
        more.type = 'button';
        more.className = 'chat-load-more-btn';
        more.innerHTML = '<i class="bi bi-chevron-down"></i><span>加载更多会话</span>';
        more.onclick = loadMoreChatSessions;
        body.appendChild(more);
    }
    scheduleChatUserInfoHydration(sessions);
}

function mergeHydrationFallbackSessions() {
    if (!chatCurrentCookieId) return;
    fetchJSON(`${apiBase}/api/chat/sessions?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&include_order_fallback=true&limit=120`)
        .then(result => {
            if (!result?.success || !Array.isArray(result.sessions)) return;
            const mergedSessions = mergeChatSessionLists(chatSessionsCache, result.sessions);
            if (mergedSessions.length !== chatSessionsCache.length) {
                chatSessionsCache = mergedSessions;
                renderChatSessions(chatSessionsCache);
            }

            if (chatSessionsCache.length <= 1) {
                enrichSessionsWithOrdersFallback(chatSessionsCache)
                    .then(mergedSessions => {
                        if (Array.isArray(mergedSessions) && mergedSessions.length > chatSessionsCache.length) {
                            chatSessionsCache = sortChatSessions(mergedSessions);
                            renderChatSessions(chatSessionsCache);
                        }
                    })
                    .catch(error => {
                        console.debug('订单会话增强失败:', error);
                    });
            }
        })
        .catch(error => {
            console.debug('补充可补拉会话失败:', error);
        });
}

function filterChatSessions() {
    const keyword = (document.getElementById('chatSearchInput')?.value || '').toLowerCase();
    if (!keyword) {
        renderChatSessions(sortChatSessions(chatSessionsCache));
        return;
    }
    const hasMoreBeforeFilter = chatSessionsHasMore;
    chatSessionsHasMore = false;
    renderChatSessions(sortChatSessions(chatSessionsCache.filter(session =>
        String(session.sender_name || '').toLowerCase().includes(keyword)
        || String(session.buyer_name || '').toLowerCase().includes(keyword)
        || String(session.item_title || '').toLowerCase().includes(keyword)
        || String(session.chat_id || '').includes(keyword)
        || String(normalizeChatSessionPreview(session.content, session.content_type) || '').toLowerCase().includes(keyword)
    )));
    chatSessionsHasMore = hasMoreBeforeFilter;
}

async function selectChatSession(session) {
    session = { ...session, content: normalizeChatSessionPreview(session?.content, session?.content_type) };
    chatCurrentChatId = session.chat_id;
    chatCurrentToUserId = session.buyer_id || session.sender_id || '';
    chatCurrentSenderName = resolveSessionDisplayName(session);
    chatCurrentItemId = session.item_id || '';
    chatMessagesNextCursor = null;
    chatMessagesHasMore = false;
    chatMessagesSource = 'remote_im';
    chatOldestMsgId = null;

    const placeholder = document.getElementById('chatMainPlaceholder');
    const active = document.getElementById('chatActiveArea');
    if (placeholder) placeholder.classList.add('d-none');
    if (active) active.classList.remove('d-none');

    const headerName = document.getElementById('chatHeaderName');
    if (headerName) headerName.textContent = chatCurrentSenderName;
    updateChatHeaderMeta(session);
    resetChatBlacklistState();
    renderChatBlacklistButton();
    if (chatCurrentToUserId) {
        refreshChatBlacklistStatus();
    }

    renderChatSessions(chatSessionsCache);
    await loadChatMessages(false);
    if (chatCurrentToUserId) {
        refreshChatBlacklistStatus();
    }

    if (!document.getElementById('chatReplyPanel')?.classList.contains('d-none') && chatCurrentItemId) {
        await loadItemKeywords();
    }

    document.getElementById('chatInputBox')?.focus();
}

function shouldForceHydrateSession(session) {
    return false;
}

function shouldRebuildEmptySession(messages) {
    return false;
}

function renderChatEmptyState(session, hint = '暂无消息记录') {
    const title = session?.source === 'remote_im' ? hint : (hint || '暂无消息记录');
    return `<div class="text-center text-muted py-4"><div class="small">${escapeHtml(title)}</div></div>`;
}

function updateChatMessagePaging(result, messages) {
    chatMessagesSource = result.source || 'local_cache';
    chatMessagesNextCursor = result.next_cursor || null;
    chatMessagesHasMore = Boolean(result.has_more && chatMessagesNextCursor);
    if (chatMessagesSource === 'local_cache' && messages.length > 0) {
        chatOldestMsgId = messages[0].id;
        chatMessagesHasMore = Boolean(result.has_more && chatOldestMsgId);
    }
}

async function loadChatMessages(append = false) {
    if (!chatCurrentCookieId || !chatCurrentChatId) return;
    const area = document.getElementById('chatMessagesArea');
    if (!area) return;
    if (!append) {
        chatMessagesNextCursor = null;
        chatMessagesHasMore = false;
        chatOldestMsgId = null;
        area.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm"></div></div>';
    }

    try {
        let url = `${apiBase}/api/chat/messages?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&chat_id=${encodeURIComponent(chatCurrentChatId)}&limit=40`;
        if (chatCurrentItemId) {
            url += `&item_id=${encodeURIComponent(chatCurrentItemId)}`;
        }
        if (append) {
            if (chatMessagesSource === 'remote_im' && chatMessagesNextCursor) {
                url += `&remote=true&cursor=${encodeURIComponent(chatMessagesNextCursor)}`;
            } else if (chatMessagesSource === 'local_cache' && chatOldestMsgId) {
                url += `&remote=false&before_id=${encodeURIComponent(chatOldestMsgId)}`;
            } else {
                showToast('没有更多消息了', 'info');
                return;
            }
        } else {
            url += '&remote=true';
        }
        const result = await fetchJSON(url);
        if (!result.success) {
            if (!append) area.innerHTML = '<div class="text-center text-muted py-4">加载失败</div>';
            return;
        }
        const messages = result.messages || [];
        updateChatMessagePaging(result, messages);

        const buyerMessage = messages.find(message => message.direction === 2);
        if (buyerMessage && !chatCurrentToUserId) {
            chatCurrentToUserId = buyerMessage.sender_id;
        }
        const messageWithItem = [...messages].reverse().find(message => {
            const itemId = String(message.item_id || '');
            return itemId && itemId !== 'None' && !itemId.startsWith('auto_');
        });
        if (messageWithItem && !chatCurrentItemId) {
            chatCurrentItemId = messageWithItem.item_id;
            const currentSession = chatSessionsCache.find(item => item.chat_id === chatCurrentChatId) || {};
            updateChatHeaderMeta({ ...currentSession, item_id: chatCurrentItemId });
        }

        if (append) {
            if (!messages.length) {
                showToast('没有更多消息了', 'info');
                return;
            }
            const previousHeight = area.scrollHeight;
            area.querySelector('.chat-history-more')?.remove();
            const moreButton = chatMessagesHasMore ? '<button type="button" class="chat-history-more" onclick="loadMoreChatMessages()"><i class="bi bi-clock-history"></i><span>加载更早消息</span></button>' : '';
            area.insertAdjacentHTML('afterbegin', `${moreButton}${renderChatMessages(messages)}`);
            area.scrollTop = area.scrollHeight - previousHeight;
        } else {
            if (messages.length) {
                const moreButton = chatMessagesHasMore ? '<button type="button" class="chat-history-more" onclick="loadMoreChatMessages()"><i class="bi bi-clock-history"></i><span>加载更早消息</span></button>' : '';
                area.innerHTML = `${moreButton}${renderChatMessages(messages)}`;
            } else {
                const currentSession = chatSessionsCache.find(item => item.chat_id === chatCurrentChatId) || {};
                area.innerHTML = renderChatEmptyState(currentSession, result.remote_error || '暂无消息记录');
            }
            area.scrollTop = area.scrollHeight;
        }
    } catch (error) {
        console.error('加载消息失败:', error);
        if (!append) area.innerHTML = '<div class="text-center text-muted py-4">加载失败</div>';
    }
}

function loadMoreChatMessages() {
    loadChatMessages(true);
}

function renderChatMessages(messages) {
    let html = '';
    let lastDate = '';
    messages.forEach(message => {
        const dateStr = String(message.created_at || '').substring(0, 10);
        if (dateStr && dateStr !== lastDate) {
            lastDate = dateStr;
            html += `<div class="chat-date-divider"><span>${escapeHtml(dateStr)}</span></div>`;
        }
        const isOutgoing = message.direction === 1;
        const timeStr = String(message.created_at || '').substring(11, 16);
        let contentHtml = '';
        const extra = (() => {
            try {
                return message.extra_json ? JSON.parse(message.extra_json) : null;
            } catch (error) {
                return null;
            }
        })();
        const itemShare = extra?.item_share || null;
        if (message.content_type === 2 && message.image_url) {
            contentHtml = `<img src="${escapeHtml(message.image_url)}" class="chat-msg-image" onclick="window.open(this.src, '_blank')">`;
            if (message.content && message.content !== '[图片]') {
                contentHtml += `<div class="mt-1">${escapeHtml(message.content)}</div>`;
            }
        } else if (message.content_type === 3) {
            const poster = message.image_url ? `<img src="${escapeHtml(message.image_url)}" class="chat-msg-image mb-2" onclick="window.open('${escapeHtml(message.media_url || message.image_url)}', '_blank')">` : '';
            const link = message.media_url ? `<a href="${escapeHtml(message.media_url)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">打开视频</a>` : '';
            contentHtml = `<div class="chat-rich-card">${poster}<div class="chat-rich-title">${escapeHtml(message.content || '[视频]')}</div>${link}</div>`;
        } else if (message.content_type === 4) {
            const linkTarget = message.link_url || extra?.payload?.targetUrl || '#';
            contentHtml = `<div class="chat-rich-card"><div class="chat-rich-title">${escapeHtml(message.content || '[链接]')}</div><a href="${escapeHtml(linkTarget)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">打开链接</a></div>`;
        } else if (message.content_type === 5) {
            const linkTarget = message.link_url || '#';
            const image = itemShare?.image_url || message.image_url;
            contentHtml = `<div class="chat-rich-card chat-item-share-card">${image ? `<img src="${escapeHtml(image)}" class="chat-msg-image mb-2" onclick="window.open('${escapeHtml(linkTarget === '#' ? image : linkTarget)}', '_blank')">` : ''}<div class="chat-rich-title">${escapeHtml(itemShare?.title || message.content || '[商品分享]')}</div>${itemShare?.item_id ? `<div class="chat-rich-subtitle">商品ID: ${escapeHtml(String(itemShare.item_id))}</div>` : ''}${linkTarget && linkTarget !== '#' ? `<a href="${escapeHtml(linkTarget)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">查看商品</a>` : ''}</div>`;
        } else if (message.content_type === 6) {
            const buttonText = extra?.button_text;
            const linkTarget = message.link_url || '#';
            contentHtml = `<div class="chat-rich-card"><div class="chat-rich-title">${escapeHtml(extra?.title || message.content || '[系统卡片]')}</div>${buttonText ? `<div class="chat-rich-subtitle">${escapeHtml(buttonText)}</div>` : ''}${linkTarget && linkTarget !== '#' ? `<a href="${escapeHtml(linkTarget)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">打开卡片</a>` : ''}</div>`;
        } else {
            const normalizedContent = String(message.content || '').trim() || '[空消息]';
            contentHtml = escapeHtml(normalizedContent).replace(/\n/g, '<br>');
        }
        const sourceHtml = message.reply_source ? `<span class="chat-msg-source">${escapeHtml(message.reply_source)}</span>` : '';
        html += `<div class="chat-msg-row ${isOutgoing ? 'outgoing' : 'incoming'}"><div><div class="chat-msg-bubble">${contentHtml}</div><div class="chat-msg-meta">${escapeHtml(timeStr)}${sourceHtml}</div></div></div>`;
    });
    return html;
}

async function sendChatMessage() {
    const input = document.getElementById('chatInputBox');
    const message = String(input?.value || '').trim();
    if (!message) return;
    if (!chatCurrentCookieId || !chatCurrentChatId || !chatCurrentToUserId) {
        showToast('无法发送：缺少会话信息', 'warning');
        return;
    }
    const button = document.getElementById('chatSendBtn');
    if (button) {
        button.disabled = true;
        button.textContent = '...';
    }
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cookie_id: chatCurrentCookieId,
                chat_id: chatCurrentChatId,
                to_user_id: chatCurrentToUserId,
                message,
            })
        });
        if (result.success) {
            if (input) input.value = '';
        } else {
            showToast(result.detail || result.message || '发送失败', 'danger');
        }
    } catch (error) {
        console.error('发送消息失败:', error);
        showToast('发送消息失败', 'danger');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = '发送';
        }
    }
}

function appendChatMessage(message) {
    const area = document.getElementById('chatMessagesArea');
    if (!area) return;
    const emptyHint = area.querySelector('.text-center.text-muted');
    if (emptyHint) emptyHint.remove();
    area.insertAdjacentHTML('beforeend', renderChatMessages([message]));
    area.scrollTop = area.scrollHeight;
}

function handleChatInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
    }
}

function initChatSSE() {
    if (chatSseAbortController) {
        chatSseAbortController.abort();
        chatSseAbortController = null;
    }
    chatSseShouldRun = true;
    chatSseRetryCount = 0;
    connectChatStream();
}

async function connectChatStream() {
    if (!chatSseShouldRun) return;
    const controller = new AbortController();
    chatSseAbortController = controller;
    try {
        const token = getAuthToken();
        if (!token) {
            stopChatStream();
            return;
        }
        const response = await fetch(`${apiBase}/api/chat/stream`, {
            headers: {
                'Authorization': `Bearer ${token}`,
                'Accept': 'text/event-stream'
            },
            cache: 'no-store',
            signal: controller.signal
        });
        if (!response.ok) {
            if (response.status === 401) {
                stopChatStream();
                localStorage.removeItem('auth_token');
                showToast('登录已失效，请重新登录', 'warning');
                window.location.href = '/';
                return;
            }
            throw new Error(`HTTP ${response.status}`);
        }
        chatSseRetryCount = 0;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split('\n\n');
            buffer = parts.pop() || '';
            for (const part of parts) {
                processChatSSEEvent(part);
            }
        }
    } catch (error) {
        if (!controller.signal.aborted) {
            chatSseRetryCount += 1;
            setTimeout(() => connectChatStream(), Math.min(chatSseRetryCount * 3000, 30000));
        }
    }
}

function stopChatStream() {
    chatSseShouldRun = false;
    if (chatSseAbortController) {
        chatSseAbortController.abort();
        chatSseAbortController = null;
    }
}

function processChatSSEEvent(raw) {
    let eventType = 'message';
    let dataStr = '';
    for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) {
            eventType = line.substring(7).trim();
        } else if (line.startsWith('data: ')) {
            dataStr = line.substring(6);
        }
    }
    if (eventType === 'ping' || !dataStr) return;

    try {
        const event = JSON.parse(dataStr);
        const data = event.data || {};
        data.cookie_id = data.cookie_id || event.cookie_id;
        if (data.cookie_id !== chatCurrentCookieId) {
            return;
        }
        updateSessionFromSSE(data);
        if (data.chat_id === chatCurrentChatId) {
            appendChatMessage({
                msg_id: data.msg_id,
                chat_id: data.chat_id,
                sender_id: data.sender_id,
                sender_name: data.sender_name,
                content: data.content,
                content_type: data.content_type,
                image_url: data.image_url,
                item_id: data.item_id,
                direction: data.direction,
                reply_source: data.reply_source,
                media_url: data.media_url,
                link_url: data.link_url,
                extra_json: data.extra_json,
                created_at: data.created_at || new Date().toISOString().replace('T', ' ').substring(0, 19)
            });
        }
    } catch (error) {
        console.error('SSE解析失败:', error);
    }
}

function updateSessionFromSSE(data) {
    const preview = {
        chat_id: data.chat_id,
        sender_id: data.sender_id,
        sender_name: data.sender_name,
        buyer_id: data.direction === 2 ? data.sender_id : undefined,
        buyer_name: data.direction === 2 ? data.sender_name : undefined,
        content: data.content,
        content_type: data.content_type,
        image_url: data.image_url,
        item_id: data.item_id,
        direction: data.direction,
        created_at: data.created_at || new Date().toISOString().replace('T', ' ').substring(0, 19),
    };
    const index = chatSessionsCache.findIndex(session => session.chat_id === data.chat_id);
    if (index >= 0) {
        chatSessionsCache[index] = { ...chatSessionsCache[index], ...preview };
        chatSessionsCache.unshift(chatSessionsCache.splice(index, 1)[0]);
    } else {
        chatSessionsCache.unshift(preview);
    }
    renderChatSessions(chatSessionsCache);
}

function toggleReplyPanel() {
    const panel = document.getElementById('chatReplyPanel');
    if (!panel) return;
    panel.classList.toggle('d-none');
    if (!panel.classList.contains('d-none') && chatCurrentItemId) {
        loadItemKeywords();
    }
}

function hideReplyPanel() {
    document.getElementById('chatReplyPanel')?.classList.add('d-none');
}

async function loadItemKeywords() {
    const replyItemId = document.getElementById('replyItemId');
    const replyKeywordsList = document.getElementById('replyKeywordsList');
    const replyItemReply = document.getElementById('replyItemReply');
    if (!replyItemId || !replyKeywordsList || !replyItemReply) return;

    if (!chatCurrentCookieId || !chatCurrentItemId) {
        replyItemId.value = '未检测到商品';
        replyKeywordsList.innerHTML = '<div class="text-muted small">无商品ID</div>';
        replyItemReply.value = '';
        return;
    }

    replyItemId.value = chatCurrentItemId;
    replyKeywordsList.innerHTML = '<div class="text-muted small">加载中...</div>';

    try {
        const result = await fetchJSON(`${apiBase}/api/chat/keywords/${encodeURIComponent(chatCurrentCookieId)}/item/${encodeURIComponent(chatCurrentItemId)}`);
        if (!result.success) {
            replyKeywordsList.innerHTML = '<div class="text-danger small">加载失败</div>';
            return;
        }
        replyItemReply.value = result.item_reply || '';
        const keywords = result.keywords || [];
        replyKeywordsList.innerHTML = '';
        if (!keywords.length) {
            replyKeywordsList.innerHTML = '<div class="text-muted small">暂无关键词，点击“添加”创建</div>';
        } else {
            keywords.forEach(keyword => addKeywordRowWithData(keyword.keyword, keyword.reply || ''));
        }
        await loadCopyTargetItems();
    } catch (error) {
        console.error('加载商品关键词失败:', error);
        replyKeywordsList.innerHTML = '<div class="text-danger small">加载失败</div>';
    }
}

function addKeywordRow() {
    addKeywordRowWithData('', '');
}

function addKeywordRowWithData(keyword, reply) {
    const list = document.getElementById('replyKeywordsList');
    if (!list) return;
    const hint = list.querySelector('.text-muted');
    if (hint) hint.remove();
    const row = document.createElement('div');
    row.className = 'kw-row';
    row.innerHTML = `
        <input type="text" class="form-control form-control-sm" placeholder="关键词" value="${escapeHtml(keyword)}" style="flex:1;">
        <input type="text" class="form-control form-control-sm" placeholder="回复内容" value="${escapeHtml(reply)}" style="flex:2;">
        <button class="btn btn-outline-danger btn-sm" onclick="this.parentElement.remove()" title="删除"><i class="bi bi-trash"></i></button>
    `;
    list.appendChild(row);
}

async function saveItemKeywords() {
    if (!chatCurrentCookieId || !chatCurrentItemId) {
        showToast('缺少商品信息', 'warning');
        return;
    }
    const itemReply = document.getElementById('replyItemReply')?.value || '';
    const rows = document.querySelectorAll('#replyKeywordsList .kw-row');
    const keywords = [];
    rows.forEach(row => {
        const inputs = row.querySelectorAll('input');
        const keyword = inputs[0]?.value.trim();
        const reply = inputs[1]?.value.trim();
        if (keyword) {
            keywords.push({ keyword, reply, type: 'text' });
        }
    });

    try {
        const result = await fetchJSON(`${apiBase}/api/chat/keywords/${encodeURIComponent(chatCurrentCookieId)}/item/${encodeURIComponent(chatCurrentItemId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords, item_reply: itemReply })
        });
        if (result.success) {
            showToast(`保存成功，${result.count} 条关键词`, 'success');
        } else {
            showToast(result.detail || result.message || '保存失败', 'danger');
        }
    } catch (error) {
        console.error('保存商品关键词失败:', error);
        showToast('保存失败', 'danger');
    }
}

async function loadCopyTargetItems() {
    if (!chatCurrentCookieId) return;
    const container = document.getElementById('copyTargetItems');
    if (!container) return;
    container.innerHTML = '<div class="text-muted small">加载商品...</div>';
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/items/${encodeURIComponent(chatCurrentCookieId)}`);
        if (!result.success) {
            container.innerHTML = '<div class="text-muted small">加载失败</div>';
            return;
        }
        const items = (result.items || []).filter(item => item.item_id !== chatCurrentItemId);
        if (!items.length) {
            container.innerHTML = '<div class="text-muted small">无其他商品</div>';
            return;
        }
        container.innerHTML = '';
        items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'copy-target-item';
            const safeValue = escapeHtml(item.item_id);
            const checkboxId = buildSafeCheckboxId('ct', item.item_id);
            div.innerHTML = `<input type="checkbox" value="${safeValue}" id="${checkboxId}"><label for="${checkboxId}">${escapeHtml(item.item_title || item.item_id)}</label>`;
            container.appendChild(div);
        });
    } catch (error) {
        console.error('加载可复用商品失败:', error);
        container.innerHTML = '<div class="text-muted small">加载失败</div>';
    }
}

async function copyKeywordsToSelected() {
    if (!chatCurrentCookieId || !chatCurrentItemId) {
        showToast('缺少源商品信息', 'warning');
        return;
    }
    const checks = document.querySelectorAll('#copyTargetItems input[type=checkbox]:checked');
    const targets = [...checks].map(check => check.value);
    if (!targets.length) {
        showToast('请先选择目标商品', 'warning');
        return;
    }
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/keywords/${encodeURIComponent(chatCurrentCookieId)}/copy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_item_id: chatCurrentItemId, target_item_ids: targets })
        });
        if (result.success) {
            showToast(`已复制到 ${targets.length} 个商品，共 ${result.total} 条关键词`, 'success');
        } else {
            showToast(result.detail || result.message || '复制失败', 'danger');
        }
    } catch (error) {
        console.error('复制关键词失败:', error);
        showToast('复制失败', 'danger');
    }
}

function formatChatTime(ts) {
    if (!ts) return '';
    const d = new Date(String(ts).replace(' ', 'T'));
    if (isNaN(d.getTime())) return String(ts || '').substring(11, 16);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toTimeString().substring(0, 5);
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return '昨天';
    return `${d.getMonth() + 1}/${d.getDate()}`;
}

function loadOnlineIm() {
    refreshChatAccounts();
    initChatSSE();
}

function loadImAccountList() {
    refreshChatAccounts();
}

function onImAccountChange() {}

function refreshImIframe() {
    refreshChatSessions();
}

function openGoofishImNewWindow() {
    window.open('https://www.goofish.com/im', '_blank');
}

function openGoofishIm() {
    openGoofishImNewWindow();
}

// ==================== 定时擦亮任务管理 ====================

const POLISH_SCHEDULE_RANDOM_MINUTES = 10;

async function loadScheduledTasks() {
    try {
        const data = await fetchJSON(`${apiBase}/scheduled-tasks`);
        if (data.success) {
            return data.tasks || [];
        }
        showToast(`加载定时任务失败: ${data.message || '未知错误'}`, 'danger');
        return [];
    } catch (error) {
        console.error('加载定时任务失败:', error);
        return [];
    }
}

async function createScheduledTask(accountId, runHour, enabled = true) {
    return fetchJSON(`${apiBase}/scheduled-tasks`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            account_id: accountId,
            run_hour: runHour,
            enabled,
            random_delay_max: POLISH_SCHEDULE_RANDOM_MINUTES
        })
    });
}

async function updateScheduledTask(taskId, payload) {
    return fetchJSON(`${apiBase}/scheduled-tasks/${taskId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });
}

function getPolishScheduledTask(tasks, accountId) {
    const matchedTasks = tasks
        .filter(task => task.account_id === accountId && task.task_type === 'item_polish')
        .sort((a, b) => Number(Boolean(b.enabled)) - Number(Boolean(a.enabled)) || Number(b.id) - Number(a.id));

    return matchedTasks[0] || null;
}

function formatPolishScheduleHour(hour) {
    const safeHour = Number.isFinite(Number(hour)) ? Number(hour) : 0;
    return `${String(safeHour).padStart(2, '0')}:00`;
}

function getPolishScheduleDescription(taskOrHour, randomDelayMax = POLISH_SCHEDULE_RANDOM_MINUTES) {
    const runHour = typeof taskOrHour === 'object' && taskOrHour !== null
        ? (taskOrHour.delay_minutes ?? taskOrHour.run_hour ?? 0)
        : taskOrHour;
    const safeRandomDelay = typeof taskOrHour === 'object' && taskOrHour !== null
        ? (taskOrHour.random_delay_max ?? randomDelayMax)
        : randomDelayMax;
    return `每日 ${formatPolishScheduleHour(runHour)} 后随机 0-${safeRandomDelay} 分钟擦亮一次`;
}

function closePolishScheduleModal() {
    const modalElement = document.getElementById('polishScheduleModal');
    if (!modalElement) return;

    const modalInstance = bootstrap.Modal.getInstance(modalElement);
    if (modalInstance) {
        modalInstance.hide();
    } else {
        modalElement.remove();
    }
}

function refreshPolishScheduleModalState() {
    const enabledInput = document.getElementById('polishScheduleEnabled');
    const hourSelect = document.getElementById('polishScheduleHour');
    const hint = document.getElementById('polishScheduleHint');

    if (!enabledInput || !hourSelect || !hint) return;

    const enabled = enabledInput.checked;
    const runHour = parseInt(hourSelect.value, 10);

    hint.className = `alert ${enabled ? 'alert-info' : 'alert-secondary'} py-2 mb-3`;
    hint.textContent = enabled
        ? getPolishScheduleDescription(runHour)
        : `当前已关闭，保存后会记住 ${formatPolishScheduleHour(runHour)} 的设置，但不会自动执行`;
}

async function openPolishScheduleModal(accountId) {
    try {
        const tasks = await loadScheduledTasks();
        const task = getPolishScheduledTask(tasks, accountId);
        const runHour = Number.isFinite(Number(task?.delay_minutes)) ? Number(task.delay_minutes) : 8;
        const enabled = task ? Boolean(task.enabled) : true;
        const hourOptions = Array.from({ length: 24 }, (_, hour) => `
            <option value="${hour}" ${hour === runHour ? 'selected' : ''}>${formatPolishScheduleHour(hour)}</option>
        `).join('');
        const statusText = task ? (task.enabled ? '已开启' : '未开启') : '保存后启用';
        const nextRunText = task ? (task.enabled ? (task.next_run_at || '保存后生成') : '已关闭') : '保存后生成';
        const lastRunText = task?.last_run_at || '暂无记录';

        const existingModal = document.getElementById('polishScheduleModal');
        if (existingModal) {
            existingModal.remove();
        }

        const modalHtml = `
            <div class="modal fade" id="polishScheduleModal" tabindex="-1" aria-labelledby="polishScheduleModalLabel" aria-hidden="true">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="polishScheduleModalLabel">
                                <i class="bi bi-clock-history text-info me-2"></i>定时擦亮 - ${accountId}
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <input type="hidden" id="polishScheduleAccountId" value="${accountId}">
                            <input type="hidden" id="polishScheduleTaskId" value="${task ? task.id : ''}">

                            <div class="form-check form-switch mb-3">
                                <input class="form-check-input" type="checkbox" role="switch" id="polishScheduleEnabled" ${enabled ? 'checked' : ''}>
                                <label class="form-check-label" for="polishScheduleEnabled">启用每日定时擦亮</label>
                            </div>

                            <div class="mb-3">
                                <label class="form-label" for="polishScheduleHour">每日几点开始擦亮</label>
                                <select class="form-select" id="polishScheduleHour">
                                    ${hourOptions}
                                </select>
                            </div>

                            <div class="alert alert-info py-2 mb-3" id="polishScheduleHint">
                                ${getPolishScheduleDescription(runHour)}
                            </div>

                            <div class="small text-muted">
                                <div>当前状态：${statusText}</div>
                                <div>下次执行：${nextRunText}</div>
                                <div>上次执行：${lastRunText}</div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                            <button type="button" class="btn btn-primary" onclick="savePolishSchedule()">保存设置</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);

        const modalElement = document.getElementById('polishScheduleModal');
        const modalInstance = new bootstrap.Modal(modalElement);
        modalElement.addEventListener('hidden.bs.modal', function () {
            modalElement.remove();
        });

        document.getElementById('polishScheduleEnabled').addEventListener('change', refreshPolishScheduleModalState);
        document.getElementById('polishScheduleHour').addEventListener('change', refreshPolishScheduleModalState);
        refreshPolishScheduleModalState();

        modalInstance.show();
    } catch (error) {
        console.error('打开定时擦亮设置失败:', error);
    }
}

async function savePolishSchedule() {
    const accountId = document.getElementById('polishScheduleAccountId')?.value;
    const taskId = parseInt(document.getElementById('polishScheduleTaskId')?.value || '', 10);
    const enabled = document.getElementById('polishScheduleEnabled')?.checked;
    const runHour = parseInt(document.getElementById('polishScheduleHour')?.value || '', 10);

    if (!accountId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    if (!Number.isInteger(runHour) || runHour < 0 || runHour > 23) {
        showToast('请选择有效的擦亮时间', 'warning');
        return;
    }

    try {
        let data;

        if (taskId) {
            data = await updateScheduledTask(taskId, {
                run_hour: runHour,
                enabled,
                random_delay_max: POLISH_SCHEDULE_RANDOM_MINUTES
            });
        } else {
            data = await createScheduledTask(accountId, runHour, enabled);
        }

        if (!data.success) {
            showToast(`保存失败: ${data.message || '未知错误'}`, 'danger');
            return;
        }

        const successMessage = enabled
            ? `${accountId} 已设置为 ${getPolishScheduleDescription(runHour)}`
            : `${accountId} 已保存 ${formatPolishScheduleHour(runHour)} 的定时擦亮时间，当前为关闭状态`;
        showToast(successMessage, 'success');
        closePolishScheduleModal();
    } catch (error) {
        console.error('保存定时擦亮设置失败:', error);
    }
}
