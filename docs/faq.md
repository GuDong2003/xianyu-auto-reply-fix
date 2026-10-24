# 常见问题

> 返回：[README](../README.md) ｜ 相关：[部署与运行指南](deployment.md) ｜ [配置说明](configuration.md) ｜ [使用指南](usage.md)

## 端口被占用

- Docker Compose：修改 `docker-compose.yml` 或 `docker-compose-cn.yml` 中的端口映射。
- 本地运行：修改 `API_PORT` 环境变量，或调整 `global_config.yml` 中的 `AUTO_REPLY.api.port`。

## 数据库连接失败

检查 `data/` 目录和数据库文件权限，确保应用有读写权限；如使用自定义路径，确认 `DB_PATH` 配置正确。

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
## 历史订单同步提示 Session 过期或无权限

历史订单同步使用闲鱼卖家端请求格式。更新程序后，先重新执行同步；如果仍返回 Session 过期，需对该账号完成登录或人工刷新 Cookie。平台真实未开放订单权限时，请先在卖家网页确认是否能访问订单管理。

单个账号失败会显示原因并继续其他账号，不会无限重试。同一响应更新的 Cookie 会用于后续分页签名。此问题与实时订单消息触发的自动发货是不同链路，历史同步失败不等于所有自动发货均不可用。
