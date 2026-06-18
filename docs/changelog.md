# 更新日志

> 返回：[README](../README.md) ｜ 相关：[项目路线图](roadmap.md) ｜ [发版与热更新说明](release.md)

本文件记录 Xianyu Admin 维护版本相对上游项目的主要变化。格式参考 Keep a Changelog，但不强制要求每次都对应正式 Release。

## Unreleased

### Planned

- 完善配置与安全基线，检查 `.gitignore`、`.env.example`、默认密码和默认密钥提示。
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
