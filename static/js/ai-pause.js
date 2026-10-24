async function refreshCurrentAiPause() {
    if (!chatCurrentCookieId || !chatCurrentChatId) return;
    const account = chatCurrentCookieId, chat = chatCurrentChatId, buyer = chatCurrentToUserId, item = chatCurrentItemId;
    const query = new URLSearchParams({cookie_id:account,chat_id:chat,buyer_id:chatCurrentToUserId || '',item_id:chatCurrentItemId || ''});
    try {
        const response = await fetch(`${apiBase}/api/chat/ai-pause?${query}`,{headers:{'Authorization':`Bearer ${authToken}`}});
        if (!response.ok) return;
        const state = await response.json();
        if (account !== chatCurrentCookieId || chat !== chatCurrentChatId || buyer !== chatCurrentToUserId || item !== chatCurrentItemId) return;
        const descriptions = [];
        if (state.remaining_seconds > 0) descriptions.push(`当前商品AI暂停剩余${Math.ceil(state.remaining_seconds/60)}分钟`);
        if (state.all_reply_pause_remaining > 0) descriptions.push(`会话整体暂停剩余${Math.ceil(state.all_reply_pause_remaining/60)}分钟`);
        const label = document.getElementById('chatAiPauseState');
        if (label) label.textContent = descriptions.join('；');
    } catch (error) { /* 暂停状态查询失败不影响聊天记录和手动发送。 */ }
}

async function setCurrentChatAiPause(resume) {
    if (!chatCurrentCookieId || !chatCurrentChatId) {
        showToast('请先选择会话','warning');
        return;
    }
    try {
        const response = await fetch(`${apiBase}/api/chat/ai-pause`,{
            method:'POST',headers:{'Content-Type':'application/json','Authorization':`Bearer ${authToken}`},
            body:JSON.stringify({cookie_id:chatCurrentCookieId,chat_id:chatCurrentChatId,buyer_id:chatCurrentToUserId || '',item_id:chatCurrentItemId || '',minutes:10,resume})
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.detail || '更新AI暂停失败');
        showToast(resume ? '当前商品AI回复已恢复' : '当前商品AI回复已暂停10分钟','success');
        await refreshCurrentAiPause();
    } catch (error) { showToast(error.message,'danger'); }
}
