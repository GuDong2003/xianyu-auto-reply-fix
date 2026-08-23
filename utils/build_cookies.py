# utils/build_cookies.py
# 纯 Python (requests) + node 补环境，无 playwright/无浏览器。
#
# 用法：
#   python -m utils.build_cookies                # 输出 JSON
#   from utils.build_cookies import build_initial_cookies

import argparse
import json
import subprocess
import time
from contextlib import suppress
from pathlib import Path

import requests

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36')

_HERE = Path(__file__).resolve().parent

# ── 真浏览器抓出来的完整 mtop XHR header（缺一不可） ────────────────
_MTOP_HEADERS = {
    'User-Agent':         UA,
    'Accept':             'application/json',
    'Accept-Language':    'en,zh-CN;q=0.9,zh;q=0.8,zh-TW;q=0.7,ja;q=0.6',
    'Accept-Encoding':    'gzip, deflate, br, zstd',
    'sec-ch-ua':          '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    'sec-ch-ua-mobile':   '?0',
    'sec-ch-ua-platform': '"Windows"',
    'Origin':             'https://www.goofish.com',
    'Referer':            'https://www.goofish.com/',
    'sec-fetch-dest':     'empty',
    'sec-fetch-mode':     'cors',
    'sec-fetch-site':     'same-site',
    'priority':           'u=1, i',
    'Content-Type':       'application/x-www-form-urlencoded',
}


def _gen_tfstk(timeout: int = 15) -> str:
    """node 补环境生成 tfstk；失败返回空串"""
    script = _HERE / 'gen_tfstk.js'
    if not script.exists():
        return ''
    try:
        out = subprocess.check_output(['node', str(script)], timeout=timeout, stderr=subprocess.PIPE)
        return out.decode().strip()
    except Exception:
        return ''


def build_initial_session(*, with_tfstk: bool = True, proxies=None) -> requests.Session:
    """跑完闲鱼网关初始 cookie 链，返回带 cna / _m_h5_tk / cookie2 / tfstk 的 requests.Session。

    qrcode_login_lite 等需要继续在同一 Session 上发后续请求的流程必须用这个；
    只想拿一份扁平 cookie dict 的命令行入口请用 build_initial_cookies。
    """
    s = requests.Session()
    if proxies:
        s.proxies.update(proxies)
    s.headers.update({'User-Agent': UA, 'Accept-Language': 'zh-CN,zh;q=0.9'})

    # 1) cna —— mmstat 链路
    with suppress(requests.RequestException):
        s.get('https://log.mmstat.com/eg.js', timeout=10)
    cna = s.cookies.get('cna', domain='.mmstat.com')
    if cna:
        s.cookies.set('cna', cna, domain='.goofish.com', path='/')

    # 2) 调两次 mtop：第一次拿 _m_h5_tk，第二次拿 cookie2
    for api in ['mtop.taobao.idlehome.home.webpc.feed',
                'mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get']:
        s.post(
            f'https://h5api.m.goofish.com/h5/{api}/1.0/',
            params={
                'jsv': '2.7.2', 'appKey': '34839810',
                't': str(int(time.time() * 1000)),
                'sign': '', 'v': '1.0',
                'type': 'originaljson', 'dataType': 'json', 'timeout': '20000',
                'api': api, 'sessionOption': 'AutoLoginOnly',
                'spm_cnt': 'a21ybx.home.0.0',
            },
            data='data=%7B%7D',
            headers=_MTOP_HEADERS,
            timeout=10,
        )

    # 3) tfstk —— node 补环境
    tfstk = _gen_tfstk() if with_tfstk else ''
    if tfstk:
        s.cookies.set('tfstk', tfstk, domain='.goofish.com', path='/')

    return s


def build_initial_cookies(*, with_tfstk: bool = True) -> dict:
    """扁平 dict 版本：返回闲鱼网关需要的核心 cookie 键值，便于 CLI 输出 / 直接灌入第三方 client。"""
    s = build_initial_session(with_tfstk=with_tfstk)
    tfstk_value = s.cookies.get('tfstk', domain='.goofish.com')
    return {
        'cna':                     s.cookies.get('cna', domain='.goofish.com')
                                   or s.cookies.get('cna', domain='.mmstat.com'),
        'xlly_s':                  s.cookies.get('xlly_s', '1'),
        'mtop_partitioned_detect': s.cookies.get('mtop_partitioned_detect', '1'),
        '_m_h5_tk':                s.cookies.get('_m_h5_tk'),
        '_m_h5_tk_enc':            s.cookies.get('_m_h5_tk_enc'),
        'cookie2':                 s.cookies.get('cookie2'),
        'tfstk':                   tfstk_value,
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-tfstk', action='store_true', help='skip tfstk generation')
    args = ap.parse_args()
    print(json.dumps(build_initial_cookies(with_tfstk=not args.no_tfstk),
                     ensure_ascii=False, indent=2))
