import RFB from '/static/vendor/novnc/core/rfb.js?v=1.7.0';

const viewport = document.getElementById('remoteVerificationViewport');
const status = document.getElementById('remoteVerificationStatus');
const remotePath = window.location.pathname.replace(/\/viewer$/, '/remote');
const remoteUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${remotePath}`;

const block = event => {
    event.preventDefault();
    event.stopImmediatePropagation();
};

for (const eventName of ['keydown', 'keyup', 'keypress', 'copy', 'cut', 'paste']) {
    window.addEventListener(eventName, block, true);
}
for (const eventName of ['dragover', 'drop']) {
    viewport.addEventListener(eventName, block, true);
}

const rfb = new RFB(viewport, remoteUrl, { shared: false });
rfb.scaleViewport = true;
rfb.resizeSession = false;
rfb.clipViewport = true;
rfb.focusOnClick = false;
rfb.addEventListener('connect', () => {
    // Keep pointer input enabled while explicitly releasing noVNC's keyboard grab.
    rfb._keyboard?.ungrab();
    status.textContent = '验证浏览器已连接，可用鼠标完成验证。';
    window.parent.postMessage({ type: 'remote-verification-viewer', state: 'connected' }, window.location.origin);
});
rfb.addEventListener('disconnect', event => {
    const clean = Boolean(event.detail?.clean);
    status.textContent = clean ? '验证浏览器已关闭。' : '验证浏览器连接中断。';
    window.parent.postMessage({
        type: 'remote-verification-viewer',
        state: clean ? 'closed' : 'disconnected',
    }, window.location.origin);
});
