"""在发布镜像前验证运行时和真实入口；使用无账号、无网络的临时容器。"""

import argparse
import base64
import os
import secrets
import subprocess
import time
import uuid


RUNTIME_PROBE = '''
from utils.node_runtime import configure_playwright_node
configure_playwright_node()
import execjs
assert execjs.eval('1 + 1') == 2
from playwright.sync_api import sync_playwright
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=['--no-sandbox'])
    try:
        page = browser.new_page()
        page.goto('data:text/html,<title>runtime-smoke</title>')
        assert page.title() == 'runtime-smoke'
    finally:
        browser.close()
'''

HEALTH_PROBE = '''
import json
import urllib.request
with urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3) as response:
    assert json.load(response)['status'] == 'healthy'
'''


class SmokeFailure(RuntimeError):
    pass


def run_docker(*args, env=None, timeout=60, check=True):
    try:
        result = subprocess.run(
            ['docker', *args], env=env, capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SmokeFailure(f'Docker {args[0]} could not finish') from exc
    if check and result.returncode:
        # 容器可能输出生成的初始化凭据，CI 不转印原始输出。
        raise SmokeFailure(f'Docker {args[0]} failed (exit {result.returncode})')
    return result


def wait_for_health(name, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        running = run_docker('inspect', '--format', '{{.State.Running}}', name)
        if running.stdout.strip() != 'true':
            raise SmokeFailure('Application exited before /health became healthy')
        probe = run_docker('exec', name, 'python', '-c', HEALTH_PROBE, check=False, timeout=10)
        if probe.returncode == 0:
            return
        time.sleep(1)
    raise SmokeFailure('Application did not become healthy before the startup deadline')


def smoke(image, timeout=120):
    if timeout <= 0:
        raise SmokeFailure('Startup timeout must be positive')
    run_docker(
        'run', '--rm', '--network', 'none', '--entrypoint', 'python', image,
        '-c', RUNTIME_PROBE, timeout=120,
    )
    print('Node security check, PyExecJS and Playwright Chromium passed', flush=True)
    name = f'xianyu-smoke-{uuid.uuid4().hex[:12]}'
    environment = dict(os.environ)
    environment.update({
        'ADMIN_PASSWORD': secrets.token_urlsafe(32),
        'JWT_SECRET_KEY': secrets.token_urlsafe(48),
        'SECRET_ENCRYPTION_KEY': base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
    })
    try:
        run_docker(
            'run', '--detach', '--name', name, '--network', 'none',
            '--env', 'ADMIN_PASSWORD', '--env', 'JWT_SECRET_KEY', '--env', 'SECRET_ENCRYPTION_KEY',
            '--env', 'API_PORT=8090', '--env', 'USE_XVFB=false', '--env', 'ENABLE_HEADFUL=false',
            '--env', 'ENABLE_VNC=false', '--env', 'SQL_LOG_ENABLED=false', '--env', 'LOG_LEVEL=ERROR',
            image, env=environment,
        )
        wait_for_health(name, timeout)
        print('Default container entrypoint and /health passed', flush=True)
    finally:
        run_docker('rm', '--force', name, check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image')
    parser.add_argument('--timeout', type=float, default=120)
    args = parser.parse_args()
    try:
        smoke(args.image, args.timeout)
    except SmokeFailure as exc:
        print(f'Container smoke test failed: {exc}', flush=True)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
