# 部署与运行指南

> 返回：[README](../README.md) ｜ 相关：[配置说明](configuration.md) ｜ [常见问题](faq.md)

本页保存 README 中不适合展开的部署细节，README 只保留最短启动路径。

## 环境要求

- **Python**: 3.11+
- **Node.js**: 16+（用于 PyExecJS 执行 JavaScript）
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
playwright install chromium
# Linux 可能还需要：playwright install-deps chromium

python Start.py
```

访问：`http://localhost:8090`

> 本地运行请确保已安装 Node.js，否则 `PyExecJS` 相关功能无法正常使用。

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
