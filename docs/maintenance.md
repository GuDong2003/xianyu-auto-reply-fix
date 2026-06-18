# 维护工作流

> 返回：[README](../README.md) ｜ 相关：[项目路线图](roadmap.md) ｜ [更新日志](changelog.md) ｜ [发版与热更新说明](release.md)

本文记录 Xianyu Admin 维护版本的日常开发、上游同步、检查命令和发布前流程。目标是让每次改动都能追踪来源、明确验证方式，并尽量减少与上游项目同步时的冲突。

## 分支约定

建议从最新 `main` 创建短生命周期分支：

```bash
git switch main
git pull origin main
git switch -c docs/maintenance-workflow
```

常用分支前缀：

- `docs/`：文档、路线图、FAQ、维护说明。
- `chore/`：部署脚本、配置、项目结构、依赖和工程维护。
- `fix/`：明确缺陷修复。
- `feat/`：新增用户可感知功能。
- `refactor/`：不改变行为的结构调整。
- `test/`：测试和验证能力。

## 提交前检查

文档或配置改动至少执行：

```bash
git diff --check
git status --short
```

Docker 或部署脚本改动建议执行：

```bash
bash -n docker-deploy.sh
bash -n start.sh
bash -n stop.sh
docker compose -f docker-compose.yml --profile with-nginx config
docker compose -f docker-compose-cn.yml --profile with-nginx config
```

Python 代码改动建议至少执行：

```bash
python3 -m py_compile db_manager.py
python3 -m py_compile Start.py
python3 -m py_compile reply_server.py
```

运行中 smoke test：

```bash
curl -sS http://localhost:8000/health
curl -sS http://localhost:8000/version
curl -sS http://localhost:8000/api/system/info
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8000/
curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8000/docs
```

如果使用默认 compose，则把端口替换为 `9000`；本地运行则使用 `8090`。

## 提交流程

1. 确认工作区只包含当前任务文件：

   ```bash
   git status --short
   git diff --name-only
   ```

2. 只暂存本次任务相关文件：

   ```bash
   git add README.md docs/maintenance.md docs/roadmap.md docs/changelog.md
   ```

3. 使用清楚的提交信息：

   ```bash
   git commit -m "docs: add maintenance workflow"
   ```

4. 推送并创建 PR：

   ```bash
   git push --set-upstream origin docs/maintenance-workflow
   ```

## 上游同步流程

本仓库建议保留两个 remote：

- `origin`：自己的仓库 `https://github.com/qianmokano/Xianyu_admin.git`
- `upstream`：原作者仓库 `https://github.com/GuDong2003/xianyu-auto-reply-fix.git`

确认 remote：

```bash
git remote -v
```

为避免误推原项目，保持：

```bash
git remote set-url --push upstream DISABLED
```

同步上游建议开独立分支：

```bash
git switch main
git pull origin main
git switch -c chore/sync-upstream-YYYYMMDD
git fetch upstream
git merge upstream/main
```

如有冲突，优先保护本维护版本已经明确改造过的部署和文档入口。解决冲突后执行检查，再提交合并结果。

## 上游同步重点检查文件

每次同步后重点看这些文件是否被上游改动影响：

- `README.md`
- `.env.example`
- `.gitignore`
- `Dockerfile`
- `Dockerfile-cn`
- `docker-compose.yml`
- `docker-compose-cn.yml`
- `docker-deploy.sh`
- `docker-deploy.bat`
- `start.sh`
- `stop.sh`
- `docs/`
- `db_manager.py`
- `reply_server.py`
- `Start.py`
- `static/version.txt`
- `requirements.txt`

同步后至少确认：

- 项目名仍是 `Xianyu Admin`
- Docker 镜像、服务、容器和网络仍使用 `xianyu-admin` 命名
- 国内 compose 的基础镜像和 Playwright 下载源仍可覆盖
- `.env`、数据库、日志、Cookie、Token、API Key 仍不会被提交
- README 和文档仍保留上游归属、AGPL-3.0 协议说明和当前维护版本说明

## 发布前检查

正式合并或打 tag 前建议执行：

```bash
git status --short
git diff --check
docker compose -f docker-compose-cn.yml --profile with-nginx config
docker compose -f docker-compose.yml --profile with-nginx config
```

如果涉及热更新文件，继续执行：

```bash
python3 release_precheck.py
```

如果涉及容器启动链路，建议至少做一次：

```bash
docker compose -f docker-compose-cn.yml up -d
curl http://localhost:8000/health
docker compose -f docker-compose-cn.yml ps
```

## 更新日志规则

每批维护改动合并前，按影响更新 [更新日志](changelog.md)：

- `Added`：新增文档、脚本、配置项、接口或功能。
- `Changed`：行为、默认值、部署方式、说明方式发生变化。
- `Fixed`：修复明确问题。
- `Verified`：记录关键验证命令，尤其是 Docker、脚本、健康检查。

没有形成正式 Release 的改动先放在 `Unreleased`；稳定节点再整理到日期或版本小节。

## 敏感信息检查

提交前建议至少检查：

```bash
git status --short
git diff --cached --name-only
git check-ignore -v .env data/xianyu_data.db logs/app.log backups/foo.db browser_data/state.json
```

不要提交：

- `.env`
- `data/`
- `logs/`
- `backups/`
- SQLite 数据库
- Cookie、Token、API Key
- 浏览器缓存和登录状态
