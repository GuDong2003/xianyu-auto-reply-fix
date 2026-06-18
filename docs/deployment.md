# 部署与运行指南

> 返回：[README](../README.md) ｜ 相关：[配置说明](configuration.md) ｜ [常见问题](faq.md) ｜ [维护工作流](maintenance.md)

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

## 方式一：使用部署脚本（推荐）

### Linux / macOS

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin
cp .env.example .env
# 编辑 .env，至少修改 ADMIN_PASSWORD 和 JWT_SECRET_KEY
chmod +x docker-deploy.sh
./docker-deploy.sh
```

脚本会自动检查依赖、创建目录、构建镜像并启动服务。

常用管理命令：

```bash
./docker-deploy.sh status
./docker-deploy.sh logs xianyu-admin-app
./docker-deploy.sh health
./docker-deploy.sh stop
```

如果需要在未启动容器时指定 Compose 文件，可使用：

```bash
XIAN_ADMIN_COMPOSE_FILE=docker-compose-cn.yml ./docker-deploy.sh health
```

默认访问地址：

- `docker-compose.yml`：`http://localhost:9000`
- `docker-compose-cn.yml`：`http://localhost:8000`

### Windows

```cmd
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin
copy .env.example .env
REM 编辑 .env，至少修改 ADMIN_PASSWORD 和 JWT_SECRET_KEY
docker-deploy.bat
```

默认访问地址：

- `docker-compose.yml`：`http://localhost:9000`
- `docker-compose-cn.yml`：`http://localhost:8000`

## 方式二：手动使用 Docker Compose

### 默认配置

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin
cp .env.example .env
# 编辑 .env，至少修改 ADMIN_PASSWORD 和 JWT_SECRET_KEY
docker compose up -d
```

访问：`http://localhost:9000`

### 国内构建配置

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin
cp .env.example .env
# 编辑 .env，至少修改 ADMIN_PASSWORD 和 JWT_SECRET_KEY
docker compose -f docker-compose-cn.yml build --no-cache xianyu-admin-app
docker compose -f docker-compose-cn.yml up -d
```

访问：`http://localhost:8000`

国内构建配置默认使用：

- `BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm`
- `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright`

如果需要临时覆盖：

```bash
BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm \
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
docker compose -f docker-compose-cn.yml build --no-cache xianyu-admin-app
```

## 方式三：本地运行

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin

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

- 当前仓库未包含 GitHub Actions 自动构建配置
- 镜像仓库地址请以实际发布情况为准

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

如果部署前复制并修改了 `.env` 中的 `ADMIN_PASSWORD`，首次初始化会使用该密码。已有数据库不会被环境变量覆盖，请登录 Web 管理界面修改密码。

> ⚠️ 对外暴露服务前，请修改默认密码和 `JWT_SECRET_KEY`，并不要提交 `.env`、数据库、日志、Cookie、Token 或 API Key。
