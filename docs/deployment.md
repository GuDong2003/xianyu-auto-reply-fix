# 部署与运行指南

> 返回：[README](../README.md) ｜ 相关：[配置说明](configuration.md) ｜ [常见问题](faq.md)

本页保存 README 中不适合展开的部署细节，README 只保留最短启动路径。

## 环境要求

- **Python**: 3.11+
- **Node.js**: 推荐 24.22.1+（24.x LTS）；也支持 22.23.4+（22.x）或 26.12.1+（26.x），用于 PyExecJS 和 Playwright 驱动
- **系统**: Windows / Linux / macOS
- **架构**: x86_64 (amd64) / ARM64 (aarch64)
- **Docker**: 20.10+（Docker 部署）
- **Docker Compose**: 2.0+（Docker 部署）
- **浏览器依赖**: Playwright Chromium（本地运行需要安装）
- **资源建议**: 建议 2GB+ 内存，预留 10GB+ 存储空间

## 部署前安全配置

`docker-compose.yml` 内置的默认管理员密码和 JWT 密钥仅用于本地初始化。正式或联网部署前，请在仓库根目录创建 `.env` 并至少设置：

```dotenv
ADMIN_PASSWORD=replace-with-a-strong-password
JWT_SECRET_KEY=replace-with-a-long-random-string
```

可以使用本地密码管理器或安全随机数工具生成密钥，例如：

```bash
openssl rand -hex 32
```

同时请注意：

- 首次登录后立即修改默认管理员密码。
- 不要将 `.env`、Cookie、Token、数据库、日志或备份提交到 Git。
- 默认 Compose 会映射 `5900` 和 `6080` 供 VNC/noVNC 使用，不要把这两个端口直接开放到公网。
- 对外提供 Web 管理界面时，请使用 HTTPS 反向代理、防火墙和访问控制。

## 方式一：使用部署脚本（推荐）

### Linux / macOS

```bash
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix
chmod +x docker-deploy.sh
./docker-deploy.sh
```

脚本会自动检查依赖、创建目录、构建镜像并启动服务。

默认访问地址：

- `docker-compose.yml`：`http://localhost:9000`
- `docker-compose-cn.yml`：`http://localhost:8000`

### Windows

```cmd
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix
docker-deploy.bat
```

默认访问地址：

- `docker-compose.yml`：`http://localhost:9000`
- `docker-compose-cn.yml`：`http://localhost:8000`

## 方式二：手动使用 Docker Compose

### 默认配置

```bash
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix
docker compose up -d
```

仅在修改了 `Dockerfile` 或依赖文件、需要本地重新构建镜像时，使用 `docker compose up -d --build`。

访问：`http://localhost:9000`

### 国内构建配置

```bash
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix
docker compose -f docker-compose-cn.yml up -d --build
```

访问：`http://localhost:8000`

## 方式三：本地运行

```bash
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix

python -m venv venv
source venv/bin/activate  # Linux/macOS
# venv\Scripts\activate   # Windows

pip install --upgrade pip
pip install -r requirements.txt
export PLAYWRIGHT_NODEJS_PATH="$(command -v node)"  # Linux/macOS，使用已修复的 Node
playwright install chromium
# Linux 可能还需要：playwright install-deps chromium

python Start.py
```

访问：`http://localhost:8090`

> 启动程序会校验 Node.js 的安全版本，并让 Playwright 使用外部 Node。仅重新安装 Playwright 可能仍带入旧版内置 Node。Windows PowerShell 可使用 `$env:PLAYWRIGHT_NODEJS_PATH = (Get-Command node).Source`；显式指定的路径也会接受版本校验。

> 从旧环境更新时，升级依赖后执行 `python -m pip uninstall -y blackboxprotobuf protobuf`，移除旧版未使用的依赖链。消息解码使用项目内的 MessagePackDecoder，不依赖这两个包。

### 2026 年 10 月证书事件的更新要求

- 当前依赖固定 Playwright 1.64.0，更新 Python 包后需要重新执行 `python -m playwright install chromium`。
- Ubuntu 24.04 的 `ca-certificates` 至少更新到 `20260816ubuntu0.24.04.1`。系统 CA、Python certifi 和浏览器的信任库需要分别更新。
- 本项目浏览器上下文启用 HTTPS 证书验证。遇到证书错误时应修复对端证书链或本地信任库。
- 浏览器版本更新后，可使用 [eMudhra 专项检测页](https://emudhra.we-have-a-problem.com/#check) 核验。自动化浏览器的新配置可能没有撤销列表，仅更新浏览器二进制仍可能出现红色；应更新撤销组件或在浏览器信任库中明确禁用受影响的 emSign ECC Root CA - G3。
- Linux Chromium 的本地信任设置使用 NSS Shared DB；M146 起默认路径为 `~/.local/share/pki/nssdb`，已有 `~/.pki/nssdb` 时沿用旧路径。证书管理操作参见 [Chromium 官方说明](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/linux/cert_management.md)。本机已按指纹核对并添加 G3 根的不信任记录。

## 多架构支持

支持的架构：

- `linux/amd64` - Intel / AMD 处理器
- `linux/arm64` - ARM64 处理器

构建方式：

- 提供 `build-multi-arch.sh` 多架构构建脚本
- 支持使用 Docker Buildx 构建 amd64 / arm64 镜像
- Docker 部署和本地运行可在对应架构环境中使用

说明：

- 仓库通过 GitHub Actions 在 `main` 更新、手动触发或发布 Release 时构建多架构镜像。
- 官方 GHCR 镜像地址为 `ghcr.io/gudong2003/xianyu-auto-reply-fix`；具体可用标签以 [Packages](https://github.com/GuDong2003/xianyu-auto-reply-fix/pkgs/container/xianyu-auto-reply-fix) 和 [Releases](https://github.com/GuDong2003/xianyu-auto-reply-fix/releases) 为准。
- Docker Hub 仅在维护者配置了对应发布凭据时同步，不能作为必然可用的发布渠道。

## 访问地址

部署完成后，您可以通过以下方式访问系统：

| 场景 | Web 管理界面 | API 文档 | 健康检查 |
| --- | --- | --- | --- |
| Docker Compose 默认配置 | `http://localhost:9000` | `http://localhost:9000/docs` | `http://localhost:9000/health` |
| Docker Compose 国内配置 | `http://localhost:8000` | `http://localhost:8000/docs` | `http://localhost:8000/health` |
| 本地运行 | `http://localhost:8090` | `http://localhost:8090/docs` | `http://localhost:8090/health` |

默认管理员账号（首次初始化且未自定义密码时）：

- 用户名：`admin`
- 密码：`admin123`

> ⚠️ 首次登录后请立即修改默认密码。
