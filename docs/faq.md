# 常见问题

> 返回：[README](../README.md) ｜ 相关：[部署与运行指南](deployment.md) ｜ [配置说明](configuration.md) ｜ [使用指南](usage.md)

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
