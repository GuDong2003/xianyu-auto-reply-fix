# 闲鱼运营管理系统（Xianyu Admin）

[![GitHub](https://img.shields.io/badge/GitHub-qianmokano%2FXianyu__admin-blue?logo=github)](https://github.com/qianmokano/Xianyu_admin)
[![Upstream](https://img.shields.io/badge/Upstream-GuDong2003%2Fxianyu--auto--reply--fix-lightgrey?logo=github)](https://github.com/GuDong2003/xianyu-auto-reply-fix)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-源码构建-blue?logo=docker)](#快速开始)
[![Python](https://img.shields.io/badge/Python-3.11+-green?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Usage](https://img.shields.io/badge/Usage-仅供学习-red.svg)](#版权声明与使用条款)

## 项目概述

这是一个基于 **FastAPI + SQLite + Playwright** 的闲鱼运营自动化管理系统，支持多用户、多闲鱼账号管理、关键词/AI 自动回复、自动发货、商品管理、订单状态跟踪、自动擦亮、自动评价、日志监控和 Docker 部署。

本仓库是基于 [GuDong2003/xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix) 的二次开发版本，目标是改善部署体验、配置说明、运行稳定性和后续维护结构。原项目版权声明、AGPL-3.0 协议和上游项目归属均保留。

> 重要提示：本项目采用 AGPL-3.0 开源协议，仅供学习研究使用，请勿用于违法违规场景。使用前请仔细阅读[版权声明](#版权声明与使用条款)。

## 核心特性

- **多用户系统**：支持注册登录、邮箱验证、图形验证码、权限控制和用户数据隔离。
- **多账号管理**：每个用户可管理多个闲鱼账号，支持独立启停、状态查看和 Cookie 维护。
- **智能回复**：支持关键词回复、默认回复、指定商品回复、图片关键词和 AI 自动回复。
- **自动发货**：支持文本、批量数据、API、图片等发货方式，并提供防重复处理能力。
- **商品管理**：自动收集商品信息，支持商品详情、规格配置和数据去重。
- **运营协同**：提供订单管理、通知渠道、消息通知、在线客服等后台运营能力。
- **监控维护**：支持实时日志、健康检查、安全统计、系统统计和日志文件轮转。
- **容器化部署**：支持本地运行、Docker Compose、国内网络构建和多架构构建。

## 快速开始

### 国内 Docker Compose（推荐）

国内环境建议优先使用 `docker-compose-cn.yml`。该配置默认使用 DaoCloud 的 Python 基础镜像源，并为 Playwright 浏览器下载配置 npm mirror。

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin

docker compose -f docker-compose-cn.yml build --no-cache xianyu-admin-app
docker compose -f docker-compose-cn.yml up -d
curl http://localhost:8000/health
```

默认访问：

- Web 管理界面：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/health`
- 默认账号：`admin / admin123`

如果基础镜像或 Playwright 下载源需要临时覆盖：

```bash
BASE_IMAGE=docker.m.daocloud.io/library/python:3.11-slim-bookworm \
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
docker compose -f docker-compose-cn.yml build --no-cache xianyu-admin-app
```

### Docker Compose（默认）

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin
docker compose up -d
```

默认访问：

- Web 管理界面：`http://localhost:9000`
- API 文档：`http://localhost:9000/docs`
- 健康检查：`http://localhost:9000/health`
- 默认账号：`admin / admin123`

### 本地运行

```bash
git clone https://github.com/qianmokano/Xianyu_admin.git
cd Xianyu_admin

python -m venv venv
source venv/bin/activate  # Windows 使用 venv\Scripts\activate

pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium

python Start.py
```

默认访问：`http://localhost:8090`

> 更多部署方式、Windows 脚本、国内 Docker Compose 配置、多架构构建和访问地址见 [部署与运行指南](docs/deployment.md)。

## AI 回复配置

AI 回复使用统一的 `model_name` / `api_key` / `base_url` / `api_type` 配置模式，支持 OpenAI-compatible、OpenAI Responses、DashScope、Gemini、Anthropic、Azure OpenAI 等接口。

第三方 OpenAI-compatible 服务可通过自定义 `base_url` 接入；详细配置和新增 Provider 说明见 [配置说明](docs/configuration.md)。

## 文档导航

| 文档 | 内容 |
| --- | --- |
| [部署与运行指南](docs/deployment.md) | Docker、本地运行、环境要求、多架构、访问地址 |
| [配置说明](docs/configuration.md) | 环境变量、`global_config.yml`、AI 回复配置、运行期目录 |
| [使用指南](docs/usage.md) | 用户注册、添加账号、自动回复、自动发货 |
| [常见问题](docs/faq.md) | 端口、数据库、WebSocket、Playwright、Docker、Windows 问题 |
| [项目路线图](docs/roadmap.md) | 维护方向、阶段目标、优先任务和验收标准 |
| [更新日志](docs/changelog.md) | Xianyu Admin 维护版本的主要变化 |
| [发版与热更新说明](docs/release.md) | 热更新清单、版本号、`release_precheck.py`、Release 流程 |
| [安全政策](SECURITY.md) | 安全问题反馈和处理方式 |

## 技术架构

### 核心技术栈

- **后端框架**：FastAPI + Uvicorn + Python 3.11+ 异步编程
- **数据库**：SQLite 3 + 多用户数据隔离 + 自动迁移
- **前端**：Bootstrap 5 + Vanilla JavaScript + Chart.js
- **通信协议**：REST API + WebSocket + SSE
- **自动化能力**：Playwright + DrissionPage
- **部署方式**：Docker + Docker Compose + Nginx（可选）
- **日志系统**：Loguru + 文件轮转 + 实时收集

```text
┌─────────────────────────────────────────┐
│       Web 界面 (FastAPI + Static)        │
│          用户管理 + 功能界面               │
└───────────────────┬─────────────────────┘
                    │
┌───────────────────▼─────────────────────┐
│             CookieManager               │
│           多账号任务与状态管理             │
└───────────────────┬─────────────────────┘
                    │
┌───────────────────▼─────────────────────┐
│          XianyuLive (多实例)             │
│        WebSocket 连接 + 消息处理          │
└──────────────┬──────────────┬───────────┘
               │              │
┌──────────────▼───────┐ ┌────▼──────────────┐
│    AIReplyEngine     │ │ FileLogCollector  │
│     AI 回复与上下文    │ │   实时日志与统计    │
└──────────────┬───────┘ └────┬──────────────┘
               │              │
┌──────────────▼──────────────▼───────────┐
│              SQLite 数据库               │
│      用户数据 + 商品信息 + 配置数据         │
└─────────────────────────────────────────┘
```

## 监控和维护

- **实时日志**：Web 界面查看实时系统日志。
- **日志文件**：`logs/` 目录下按日期分割。
- **日志级别**：支持 DEBUG、INFO、WARNING、ERROR。
- **健康检查**：访问 `/health` 检查服务状态。

## 与上游同步

本仓库建议保留两个 remote：

- `origin`：自己的仓库 `https://github.com/qianmokano/Xianyu_admin.git`
- `upstream`：原项目 `https://github.com/GuDong2003/xianyu-auto-reply-fix.git`

同步上游更新：

```bash
git fetch upstream
git merge upstream/main
```

为避免误推原项目，建议保持：

```bash
git remote set-url --push upstream DISABLED
```

## 贡献指南

欢迎提交 Issue 和 Pull Request：

1. Fork 项目到自己的 GitHub 账号。
2. 创建功能分支：`git checkout -b feature/your-feature`。
3. 提交更改：`git commit -am 'add some feature'`。
4. 推送分支：`git push origin feature/your-feature`。
5. 提交 Pull Request。

贡献前建议先查看 [Issues](https://github.com/qianmokano/Xianyu_admin/issues)，并确保变更不引入真实 Cookie、Token、数据库文件或其他敏感信息。

## 常见问题

### 端口被占用怎么办？

Docker Compose 修改 `docker-compose.yml` / `docker-compose-cn.yml` 的端口映射；本地运行可修改 `API_PORT` 或 `global_config.yml`。

### Playwright 浏览器缺失怎么办？

```bash
source venv/bin/activate
playwright install chromium
```

### Docker 容器启动失败怎么办？

```bash
docker compose logs -f
docker compose down
docker compose build --no-cache
docker compose up -d
```

更多问题见 [常见问题](docs/faq.md)。

## 特别鸣谢

### 上游项目

- **[GuDong2003/xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix)** - 本项目的原始上游仓库。

### 开源项目参考（排名不分先后）

- **[myfish](https://github.com/Kaguya233qwq/myfish)** - 提供了扫码登录的实现思路
- **[XianYuApis](https://github.com/cv-cat/XianYuApis)** - 提供了闲鱼 API 接口的技术参考
- **[XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent)** - 提供了自动化处理的实现思路
- **[xianyu-auto-reply](https://github.com/zhinianboke/xianyu-auto-reply)** - 提供了基础框架与初始实现参考

### 开发者支持（贡献不分先后）

- **[syunnrai123](https://github.com/syunnrai123)**、**[1205747671](https://github.com/1205747671)** - 为当前项目的滑块处理方案提供思路与参考
- **[Mangor2021](https://github.com/Mangor2021)**、**[3281341052](https://github.com/3281341052)**、**[GDWhisper](https://github.com/GDWhisper)**、**[82762294](https://github.com/82762294)**、**[iidamie](https://github.com/iidamie)**、**[Roverlo](https://github.com/Roverlo)** - 为项目开发与改进提供实际贡献

## 版权声明与使用条款

本项目基于 [GuDong2003/xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix) 二次开发，采用 **GNU Affero General Public License v3.0（AGPL-3.0）** 开源协议。项目定位为学习与研究使用，请勿用于任何违法违规场景。

使用、修改、分发或通过网络提供服务时，应遵守 AGPL-3.0 的源码提供、版权声明保留等要求。使用者需自行承担部署、配置和运行风险，并确保实际用途符合当地法律法规和平台规则。

本项目按“现状”提供，不提供任何明示或暗示的保证；因使用本项目产生的风险、损失或责任，由使用者自行承担。
