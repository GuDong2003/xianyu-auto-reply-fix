# 配置说明

> 返回：[README](../README.md) ｜ 相关：[部署与运行指南](deployment.md) ｜ [AI 回复配置](../README.md#-ai-回复配置) ｜ [常见问题](faq.md)

本项目的配置分为环境变量、`global_config.yml` 和 Web 管理界面配置。敏感信息建议仅保存在本地环境或运行期数据目录中，不要提交到版本库。

## 环境变量

系统实际会读取的环境变量主要包括：

```bash
# Web 服务
API_HOST=0.0.0.0
API_PORT=8090

# 数据存储
DB_PATH=/app/data/xianyu_data.db

# SQL 日志
SQL_LOG_ENABLED=true
SQL_LOG_LEVEL=INFO

# 敏感信息加密
SECRET_ENCRYPTION_KEY=your-secret-key

# 兼容旧单账号模式（可选）
COOKIES_STR=your_cookie_string

# Docker 图形模式（可选）
USE_XVFB=true
ENABLE_HEADFUL=true
ENABLE_VNC=false
DISPLAY=:99

# 滑块人工接管（可选；默认仍启用自动滑块）
XY_ENABLE_AUTO_SLIDER=0
XY_MANUAL_SLIDER_TIMEOUT=180
```

`XY_ENABLE_AUTO_SLIDER` 未设置时保持原有自动滑块行为。将它设置为 `0`、`false`、`no` 或 `off` 后，程序会改用有头浏览器等待人工完成验证。`XY_MANUAL_SLIDER_TIMEOUT` 用于设置等待秒数，默认 `180`，最小 `30`。

## 全局配置文件

`global_config.yml` 包含详细的系统配置，支持：

- WebSocket 连接参数
- API 接口配置
- 自动回复设置
- 商品管理配置
- 日志配置

> 其他运行参数（如 WebSocket、心跳、自动回复等）主要在 `global_config.yml` 和 Web 管理界面中配置。

## AI 回复配置

AI 回复通过 Web 管理界面配置，核心字段包括：

- `model_name`：模型名称
- `api_key`：API Key
- `base_url`：API 入口地址
- `api_type`：接口类型

常见接口类型包括 OpenAI-compatible Chat Completions、OpenAI Responses、DashScope、Gemini、Anthropic、Azure OpenAI 等。若某个第三方服务兼容 OpenAI Chat Completions，优先复用 OpenAI-compatible 配置路径，不建议新增专用适配器。

## 运行期数据目录

以下目录属于运行期数据，不要提交到版本库：

- `data/`
- `logs/`
- `browser_data/`
- `update_backup/`

这些目录可能包含数据库、日志、浏览器状态、Cookie 相关状态或更新备份。
