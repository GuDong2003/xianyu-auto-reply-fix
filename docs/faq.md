# 常见问题

> 返回：[README](../README.md) ｜ 相关：[部署与运行指南](deployment.md) ｜ [配置说明](configuration.md) ｜ [使用指南](usage.md) ｜ [维护工作流](maintenance.md)

## 端口被占用

- Docker Compose：修改 `docker-compose.yml` 或 `docker-compose-cn.yml` 中的端口映射。
- 本地运行：修改 `API_PORT` 环境变量，或调整 `global_config.yml` 中的 `AUTO_REPLY.api.port`。

## 数据库连接失败

检查 `data/` 目录和数据库文件权限，确保应用有读写权限；如使用自定义路径，确认 `DB_PATH` 配置正确。

## 修改了 ADMIN_PASSWORD 但登录密码没变

`ADMIN_PASSWORD` 只在首次创建数据库和默认管理员用户时生效。已有 `data/xianyu_data.db` 不会因为修改 `.env` 自动重置密码。

处理方式：

- 能登录时：进入 Web 管理界面修改管理员密码。
- 不能登录且是测试环境：先备份需要的数据，再删除或重建 `data/` 目录重新初始化。

不要把数据库文件、`.env`、Cookie、Token 或 API Key 提交到版本库。

## WebSocket 连接失败

检查网络和防火墙设置，并确认闲鱼账号 Cookie 仍然有效。

## Playwright 浏览器缺失或安装卡住

本地运行需要安装 Chromium：

```bash
source venv/bin/activate
playwright install chromium
```

如网络较慢，可尝试配置可用的下载镜像后再安装。

## 国内 Docker 构建卡在基础镜像或 Playwright 下载

如果 Docker Hub 拉取失败，例如出现 `auth.docker.io/token`、`EOF`、`TLS handshake timeout`，优先使用国内 compose：

```bash
docker compose -f docker-compose-cn.yml build --no-cache xianyu-admin-app
docker compose -f docker-compose-cn.yml up -d
```

国内 compose 默认使用：

- `BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm`
- `PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright`

如果下载源不可用，可以在 `.env` 中替换为当前可访问的镜像源后重新构建。

## pip 或 apt 下载很慢

国内 Dockerfile 已尽量使用国内镜像源。如果仍然很慢，先确认当前网络可以访问对应镜像站，再重新构建：

```bash
docker compose -f docker-compose-cn.yml build --no-cache xianyu-admin-app
```

如只改了代码，通常不需要 `--no-cache`，可以复用缓存提升速度。

## Shell 脚本执行错误（Linux/macOS）

如果遇到 `bad interpreter` 错误，说明脚本行结束符格式不正确：

```bash
sed -i 's/\r$//' docker-deploy.sh
chmod +x docker-deploy.sh
./docker-deploy.sh
```

或直接使用：

```bash
bash docker-deploy.sh
```

## Docker 容器启动失败

如果遇到 `exec /app/entrypoint.sh: no such file or directory` 错误：

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

## Windows 系统部署

Windows 用户建议直接使用批处理脚本：

```cmd
docker-deploy.bat
```
