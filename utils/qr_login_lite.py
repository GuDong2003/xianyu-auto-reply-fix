"""utils/qr_login_lite.py — 纯 HTTP 闲鱼扫码登录（无 Playwright / 无浏览器）

参考实现: cv-cat/XianYuApis@1dc4db0 `goofish_apis.qrcode_login`。
适配点:
  - 复用 `utils.build_cookies.build_initial_cookies` 拿 cna / _m_h5_tk / cookie2 / tfstk
  - 改用 loguru 替换 print
  - 返回 (cookies_dict, account_info) 而不是 XianyuApis 实例，便于直接灌入项目的 cookie_manager
  - 暴露 `on_qr_url` 回调，前端可拿 URL 自渲染（不依赖终端字符二维码）
  - 可选注入 `proxies`，让登录走账号绑定的出口

依赖:
  - requests >= 2.31         (requirements.txt 已有)
  - qrcode[pil] >= 7.4.2     (requirements.txt 已有；终端渲染才用得到)
  - node 运行时 + utils/gen_tfstk.js + utils/et_f.js  (build_cookies 内部依赖)

CLI 用法:
  python -m utils.qr_login_lite                # 终端渲染二维码并登录，结束打印 cookie JSON

库用法:
  from utils.qr_login_lite import qrcode_login_lite
  cookies, acct = qrcode_login_lite(on_qr_url=lambda u: push_to_frontend(u))
"""
from __future__ import annotations

import json
import sys
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

import requests
from loguru import logger

from utils.build_cookies import UA, _MTOP_HEADERS, build_initial_session
from utils.xianyu_utils import generate_device_id


_PASSPORT_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en,zh-CN;q=0.9,zh;q=0.8,zh-TW;q=0.7,ja;q=0.6",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "priority": "u=1, i",
}

_STATUS_DESC = {
    "NEW": "等待扫码",
    "SCANNED": "已扫码，待手机确认",
    "CONFIRMED": "已确认",
    "EXPIRED": "二维码已过期",
}


def _print_qr_terminal(qr_url: str) -> None:
    try:
        import qrcode as qr_lib
    except ImportError:
        logger.warning(f"qrcode 包未安装，跳过终端渲染；请手动扫码: {qr_url}")
        return
    qr = qr_lib.QRCode(border=1, box_size=1)
    qr.add_data(qr_url)
    qr.make()
    matrix = qr.get_matrix()
    rows = len(matrix)
    lines = []
    for r in range(0, rows, 2):
        line = ""
        for c in range(len(matrix[r])):
            top = matrix[r][c]
            bot = matrix[r + 1][c] if r + 1 < rows else False
            if top and bot:
                line += "█"
            elif top:
                line += "▀"
            elif bot:
                line += "▄"
            else:
                line += " "
        lines.append(line)
    payload = ("\n".join(lines) + "\n").encode("utf-8", errors="replace")
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def qrcode_login_lite(
    poll_interval: float = 3.0,
    timeout: float = 120.0,
    show_qrcode_in_terminal: bool = True,
    on_qr_url: Optional[Callable[[str], None]] = None,
    on_status: Optional[Callable[[str], None]] = None,
    proxies: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, str], Dict[str, Any]]:
    """纯 HTTP 扫码登录闲鱼。

    Args:
        poll_interval: 轮询 query.do 的间隔（秒）。
        timeout: 整个二维码会话的最大等待时长（秒）。超时或扫码超时都抛 TimeoutError。
        show_qrcode_in_terminal: 是否在 stdout 用 ▀▄█ 字符渲染二维码（CLI 调试用）。
        on_qr_url: 拿到 QR URL 时的回调，签名 `(qr_url: str) -> None`。Web UI 可在这里把 URL
            推给前端去用 JS 库画二维码，避免终端依赖。
        on_status: cv-cat 内部 `qrCodeStatus` 每次变化时回调，签名 `(status: str) -> None`。
            可能取值: NEW / SCANNED / CONFIRMED / EXPIRED 或其它阿里下发的字符串。
            Web UI 可在这里把扫码进展同步到前端模态。
        proxies: 透传给 requests 的代理字典，例如
            {'http': 'http://x', 'https': 'http://x'}。

    Returns:
        cookies_dict: 登录后 `.goofish.com` 与 `.mmstat.com` 域全部 cookie 的扁平 dict，
            可直接喂给 `utils.xianyu_utils.trans_cookies` 反向使用，或拼成 cookie 头串。
        account_info: {'unb', 'tracknick', 'device_id'}。

    Raises:
        TimeoutError: 用户未在 timeout 内完成扫码确认，或二维码被服务端标记 EXPIRED。
        RuntimeError: passport 接口返回异常（生成二维码失败 / 登录后无 unb 等）。
    """

    # ── 1. 初始 cookie（cna / _m_h5_tk / cookie2 / tfstk） ──
    s = build_initial_session(proxies=proxies)

    cna = (
        s.cookies.get("cna", domain=".goofish.com")
        or s.cookies.get("cna", domain=".mmstat.com")
        or ""
    )
    cookie2 = s.cookies.get("cookie2", domain=".goofish.com") or ""

    # ── 2. 加载 mini_login.htm 拿 XSRF-TOKEN ──
    s.get(
        "https://passport.goofish.com/mini_login.htm",
        params={
            "lang": "zh_cn",
            "appName": "xianyu",
            "appEntrance": "web",
            "styleType": "vertical",
            "bizParams": "",
            "notLoadSsoView": "false",
            "notKeepLogin": "false",
            "isMobile": "false",
            "qrCodeFirst": "false",
            "stie": "77",
            "rnd": "0.6842814084442211",
        },
        headers={
            **_PASSPORT_HEADERS,
            "Referer": "https://www.goofish.com/",
            "sec-fetch-site": "same-site",
            "sec-fetch-dest": "iframe",
            "sec-fetch-mode": "navigate",
        },
        timeout=15,
    )
    csrf_token = s.cookies.get("XSRF-TOKEN", domain="passport.goofish.com") or ""

    # ── 3. 生成二维码 ──
    biz_params = (
        f"taobaoBizLoginFrom=web&renderRefer={quote('https://www.goofish.com/')}"
    )
    gen_params = {
        "appName": "xianyu",
        "fromSite": "77",
        "appEntrance": "web",
        "_csrf_token": csrf_token,
        "umidToken": "",
        "hsiz": cookie2,
        "bizParams": biz_params,
        "mainPage": "false",
        "isMobile": "false",
        "lang": "zh_CN",
        "returnUrl": "",
        "umidTag": "SERVER",
    }
    gen_resp_raw = s.get(
        "https://passport.goofish.com/newlogin/qrcode/generate.do",
        params=gen_params,
        headers={
            **_PASSPORT_HEADERS,
            "Referer": "https://passport.goofish.com/mini_login.htm",
        },
        timeout=10,
    )
    try:
        gen_resp = gen_resp_raw.json()
    except ValueError as exc:
        raise RuntimeError(
            f"生成二维码响应非 JSON: status={gen_resp_raw.status_code}"
        ) from exc

    gen_data = (gen_resp.get("content") or {}).get("data") or {}
    qr_url = gen_data.get("codeContent")
    qr_t = gen_data.get("t")
    qr_ck = gen_data.get("ck")
    if not (qr_url and qr_t and qr_ck):
        raise RuntimeError(f"生成二维码失败: {gen_resp}")

    generated_login_token = (
        gen_data.get("lgToken")
        or parse_qs(urlparse(qr_url).query).get("lgToken", [None])[0]
    )

    logger.info(f"获取到登录二维码: {qr_url}")
    if on_qr_url is not None:
        try:
            on_qr_url(qr_url)
        except Exception:
            logger.exception("on_qr_url 回调抛异常")
    if show_qrcode_in_terminal:
        _print_qr_terminal(qr_url)

    # ── 4. 轮询 query.do 等扫码确认 ──
    query_url = "https://passport.goofish.com/newlogin/qrcode/query.do"
    query_base = {
        "appName": "xianyu",
        "fromSite": "77",
        "appEntrance": "web",
        "_csrf_token": csrf_token,
        "umidToken": "",
        "hsiz": cookie2,
        "bizParams": biz_params,
        "mainPage": "false",
        "isMobile": "false",
        "lang": "zh_CN",
        "returnUrl": "",
        "umidTag": "SERVER",
        "navlanguage": "en",
        "navUserAgent": UA,
        "navPlatform": "Win32",
        "isIframe": "true",
        "documentReferer": "https://www.goofish.com/",
        "defaultView": "sms",
        "deviceId": cna,
    }

    deadline = time.time() + timeout
    login_token: Optional[str] = generated_login_token
    last_status = ""

    while time.time() < deadline:
        body = {**query_base, "t": str(qr_t), "ck": qr_ck}
        resp = s.post(
            f"{query_url}?appName=xianyu&fromSite=77",
            data=body,
            headers={
                **_PASSPORT_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://passport.goofish.com",
                "Referer": "https://passport.goofish.com/mini_login.htm",
            },
            timeout=10,
        )
        try:
            qdata = (resp.json().get("content") or {}).get("data") or {}
        except ValueError:
            qdata = {}
        status = qdata.get("qrCodeStatus", "")

        if status != last_status:
            remaining = max(0, int(deadline - time.time()))
            desc = _STATUS_DESC.get(status, status or "未知")
            logger.info(f"二维码状态: [{status or '???'}] {desc} (剩余 {remaining}s)")
            last_status = status
            if on_status is not None and status:
                try:
                    on_status(status)
                except Exception:
                    logger.exception("on_status 回调抛异常")

        if status == "CONFIRMED":
            login_token = qdata.get("token") or qdata.get("lgToken") or generated_login_token
            break
        if status == "EXPIRED":
            raise TimeoutError("二维码已过期，请重新调用 qrcode_login_lite()")

        time.sleep(poll_interval)

    if not login_token and not s.cookies.get("unb"):
        raise TimeoutError("扫码超时，未完成登录")

    # ── 5. 用 login_token 完成登录（部分批次需要） ──
    if login_token:
        login_resp = s.post(
            "https://passport.goofish.com/login_token/login.do",
            params={
                "token": login_token,
                "subFlow": "DIALOG_CHECK_LOGIN_RPC",
                "nextCode": "0018",
                "bizScene": "qrcode",
                "confirm": "true",
            },
            data={"deviceId": cna},
            headers={
                **_PASSPORT_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": "https://passport.goofish.com",
                "Referer": "https://passport.goofish.com/mini_login.htm",
            },
            timeout=10,
        )
        logger.debug(f"login_token/login.do 完成 status={login_resp.status_code}")

    # ── 6. 刷新用户态 _m_h5_tk（登录后会换） ──
    s.post(
        "https://h5api.m.goofish.com/h5/mtop.idle.web.user.page.nav/1.0/",
        params={
            "jsv": "2.7.2",
            "appKey": "34839810",
            "t": str(int(time.time() * 1000)),
            "sign": "",
            "v": "1.0",
            "type": "originaljson",
            "dataType": "json",
            "timeout": "20000",
            "api": "mtop.idle.web.user.page.nav",
            "sessionOption": "AutoLoginOnly",
            "spm_cnt": "a21ybx.home.0.0",
        },
        data="data=%7B%7D",
        headers=_MTOP_HEADERS,
        timeout=10,
    )

    # ── 7. 收集 cookies + 账号识别 ──
    unb = s.cookies.get("unb", domain=".goofish.com") or ""
    tracknick = s.cookies.get("tracknick", domain=".goofish.com") or ""
    if not unb:
        raise RuntimeError("登录链路完成但未拿到 unb cookie，扫码登录失败")

    cookies_dict: Dict[str, str] = {}
    cookie_domains: Dict[str, str] = {}
    for c in s.cookies:
        cookies_dict[c.name] = c.value
        cookie_domains[c.name] = c.domain or ""
    logger.info(f"扫码登录最终Cookie字段: {sorted(cookies_dict.keys())}")
    if "havana_lgc2_77" in cookies_dict:
        logger.info(f"扫码登录已获取 havana_lgc2_77，domain={cookie_domains.get('havana_lgc2_77')}")

    device_id = generate_device_id(unb)
    account_info: Dict[str, Any] = {
        "unb": unb,
        "tracknick": tracknick,
        "device_id": device_id,
    }
    logger.info(f"扫码登录成功 unb={unb} tracknick={tracknick}")
    return cookies_dict, account_info


if __name__ == "__main__":
    cookies, acct = qrcode_login_lite()
    print(json.dumps({"account": acct, "cookies": cookies}, ensure_ascii=False, indent=2))
