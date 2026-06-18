# 更新日志

> 返回：[README](../README.md) ｜ 相关：[项目路线图](roadmap.md) ｜ [发版与热更新说明](release.md)

本文件记录 Xianyu Admin 维护版本相对上游项目的主要变化。格式参考 Keep a Changelog，但不强制要求每次都对应正式 Release。

## Unreleased

### Added

- 增加首次部署安全基线说明，覆盖 `.env`、默认管理员密码、`JWT_SECRET_KEY`、AI API Key、Cookie、Token、数据库和日志文件。
- 新增维护工作流文档，覆盖分支约定、上游同步、提交前检查、发布前检查和敏感信息检查。
- FAQ 补充国内 Docker 构建常见失败场景。
- 新增 `/version` 和 `/api/system/info` 公开系统信息接口，便于确认版本、运行模式、Python 环境、数据库版本和服务状态。

### Changed

- 默认管理员首次初始化支持通过 `ADMIN_PASSWORD` 环境变量覆盖。
- Docker Compose 中 `JWT_SECRET_KEY` 的弱默认值改为显眼的 `change-me` 占位。
- `.env.example` 明确为示例配置，并补充敏感字段加密说明。
- `.gitignore` 增加本地敏感加密密钥文件忽略规则。

### Planned

- 继续优化国内 Docker 部署文档和失败排查说明。
- 增加基础 smoke test 和发布前检查命令。
- 逐步统一后台页面文案和版本标识。

## 2026-06-18

### Added

- 新增 `.env.example`，集中说明常用环境变量、管理员账号、数据库、日志、AI、WebSocket、镜像源、代理和资源限制配置。
- 新增项目路线图 `docs/roadmap.md`。
- 新增维护版本更新日志 `docs/changelog.md`。

### Changed

- README 改为 Xianyu Admin 维护版本说明，并保留上游项目归属和 AGPL-3.0 协议声明。
- Docker Compose 项目名统一为 `xianyu-admin`。
- Docker 服务名和容器名统一为 `xianyu-admin-app`。
- Docker 镜像名统一为 `xianyu-admin:latest`。
- Nginx 容器名统一为 `xianyu-admin-nginx`。
- Docker 网络名统一为 `xianyu-admin-network`。
- `Dockerfile` 和 `Dockerfile-cn` 的镜像标签更新为当前维护仓库信息。
- 国内 compose 默认使用 DaoCloud Python 基础镜像和 npmmirror Playwright 下载源。
- `docker-deploy.sh` 和 `docker-deploy.bat` 的项目名、服务名、构建目标和帮助示例更新为 Xianyu Admin。
- `docker-deploy.sh` 支持根据当前运行容器自动识别 compose 文件，国内部署后可直接执行 `./docker-deploy.sh health`。
- `start.sh` 和 `stop.sh` 的展示文案更新为 Xianyu Admin。
- 部署文档补充常用脚本命令和 `XIAN_ADMIN_COMPOSE_FILE` 用法。

### Verified

- `docker compose -f docker-compose.yml --profile with-nginx config`
- `docker compose -f docker-compose-cn.yml --profile with-nginx config`
- `docker compose -f docker-compose-cn.yml build xianyu-admin-app`
- `docker compose -f docker-compose-cn.yml up -d`
- `curl http://localhost:8000/health`
- `./docker-deploy.sh health`
- `./docker-deploy.sh status`
- `bash -n docker-deploy.sh`
- `bash -n start.sh`
- `bash -n stop.sh`
- `git diff --check`
