"""为 Playwright 选择包含已知安全修复的外部 Node.js。"""

import os
from pathlib import Path
import re
import shutil
import subprocess


# https://nodejs.org/en/blog/vulnerability/october-2026-security-releases
MINIMUM_NODE_VERSIONS = {
    22: (22, 23, 4),
    24: (24, 22, 1),
    26: (26, 12, 1),
}


def configure_playwright_node() -> str:
    """校验外部 Node，并防止 Playwright 回退到包内的旧运行时。"""
    configured = os.environ.get("PLAYWRIGHT_NODEJS_PATH")
    candidate = shutil.which(configured or "node")
    if not candidate:
        raise RuntimeError(
            "未找到 Node.js；请安装 Node.js 24.22.1+（24.x LTS），"
            "并将其加入 PATH 或设置 PLAYWRIGHT_NODEJS_PATH。"
        )

    node_path = str(Path(candidate).resolve())
    try:
        result = subprocess.run(
            [node_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("无法校验 Node.js 版本，请检查 Node.js 安装。") from exc

    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    if not match:
        raise RuntimeError("无法识别 Node.js 稳定版版本号，请安装 Node.js 24.22.1+（24.x LTS）。")
    version = tuple(int(part) for part in match.groups())
    minimum = MINIMUM_NODE_VERSIONS.get(version[0])
    if minimum is None or version < minimum:
        raise RuntimeError(
            f"Node.js {'.'.join(map(str, version))} 未满足安全版本要求；"
            "请使用 22.23.4+（22.x）、24.22.1+（24.x）或 26.12.1+（26.x）。"
        )

    os.environ["PLAYWRIGHT_NODEJS_PATH"] = node_path
    return node_path
