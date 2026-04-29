# 🐟 闲鱼管理系统


[![GitHub](https://img.shields.io/badge/GitHub-GuDong2003%2Fxianyu--auto--reply--fix-blue?logo=github)](https://github.com/GuDong2003/xianyu-auto-reply-fix)
[![Docker Compose](https://img.shields.io/badge/Docker%20Compose-源码构建-blue?logo=docker)](#-快速开始)
[![Python](https://img.shields.io/badge/Python-3.11+-green?logo=python)](https://www.python.org/)
[![Usage](https://img.shields.io/badge/Usage-仅供学习-red.svg)](#️-版权声明与使用条款)

## 📋 项目概述

一个功能完整的闲鱼管理系统，采用现代化的技术架构，支持多用户、多账号管理，具备智能回复、自动发货、自动确认发货、商品管理等企业级功能。系统基于Python异步编程，使用FastAPI提供RESTful API，SQLite数据库存储，支持Docker一键部署。

> **⚠️ 重要提示：本项目仅供学习研究使用，严禁商业用途！使用前请仔细阅读[版权声明](#️-版权声明与使用条款)。**

## 🏗️ 技术架构

### 核心技术栈
- **后端框架**: FastAPI + Uvicorn + Python 3.11+ 异步编程
- **数据库**: SQLite 3 + 多用户数据隔离 + 自动迁移
- **前端**: Bootstrap 5 + Vanilla JavaScript + Chart.js + 响应式设计
- **通信协议**: WebSocket + SSE + RESTful API + 实时通信
- **自动化能力**: Playwright + DrissionPage + 浏览器自动化
- **部署方式**: Docker + Docker Compose + Nginx（可选）+ 一键部署
- **日志系统**: Loguru + 文件轮转 + 实时收集
- **安全认证**: Bearer Token + 图形验证码 + 邮箱验证 + 权限控制

### 系统架构特点
- **模块化架构**: 按账号、订单、发货、通知、日志等模块拆分，易于维护和扩展
- **异步处理**: 基于 asyncio 的高性能异步处理
- **多用户隔离**: 完整的数据隔离和权限控制
- **容器化部署**: Docker 容器化部署，支持一键启动
- **实时监控**: WebSocket + SSE 实时通信和状态监控
- **稳定性保障**: 自动重连、异常恢复、自动迁移、日志轮转

## ✨ 核心特性

### 🔐 多用户系统
- **用户注册登录** - 支持邮箱验证码注册、用户名/邮箱登录和图形验证码保护
- **数据完全隔离** - 每个用户的数据独立存储，互不干扰
- **权限管理** - 严格的用户权限控制和 Bearer Token 认证
- **安全保护** - 防暴力破解、会话管理、安全日志
- **日期校验** - 核心滑块验证模块包含日期有效性校验

### 📱 多账号管理
- **多账号支持** - 每个用户可管理多个闲鱼账号
- **独立运行** - 每个账号独立启用、停用和刷新
- **实时状态** - 账号连接状态和运行配置可实时查看
- **账号维护** - 支持 Cookie、账密、备注等信息管理
- **一键擦亮** - 支持在账号列表中一键批量擦亮当前在售商品
- **定时擦亮** - 支持按账号配置每日执行时段与随机延迟，自动完成商品擦亮

### 🤖 智能回复系统
- **关键词匹配** - 支持通用关键词和商品专属关键词回复
- **指定商品回复** - 支持为特定商品设置专门回复内容
- **批量导入导出** - 支持 Excel 格式的关键词批量导入导出
- **AI智能回复** - 支持上下文理解和多种兼容模型接口
- **图片关键词** - 支持图片关键词和图片自动发送
- **优先级策略** - 指定商品回复 > 商品专用关键词 > 通用关键词 > 默认回复 > AI回复

### 🚚 自动发货功能
- **智能匹配** - 基于商品信息自动匹配发货规则
- **多规格支持** - 支持同一商品的不同规格自动匹配
- **延时发货** - 支持设置发货延时时间
- **多种触发** - 支持付款消息、小刀卡片等触发条件
- **防重复处理** - 智能防重复发货和防重复确认
- **多种发货方式** - 支持文字、批量数据、API、图片等发货方式
- **发货统计** - 完整的发货记录和统计功能

### 🛍️ 商品管理
- **自动收集** - 消息触发时自动收集商品信息
- **商品详情** - 支持获取、查看和编辑商品详情
- **多规格配置** - 支持多规格商品配置和管理
- **智能去重** - 自动去重，避免重复存储

### 🧰 运营协同
- **后台运营** - 提供订单管理、通知渠道、消息通知和在线客服等常用功能，便于统一处理日常运营事务

### 📊 系统监控
- **实时日志** - 完整的操作日志记录、查看和导出
- **安全统计** - 支持登录封禁和锁定统计
- **健康检查** - 服务状态健康检查
- **系统统计** - 支持用户、账号、卡券等数据统计

### 📁 数据管理
- **Excel导入导出** - 支持关键词数据的 Excel 导入导出
- **模板生成** - 自动生成包含示例数据的导入模板
- **批量操作** - 支持批量添加、更新关键词数据
- **数据验证** - 导入时自动验证数据格式和重复性
- **多规格卡券管理** - 支持创建和管理多规格卡券
- **发货规则管理** - 支持多规格发货规则的创建和管理
- **数据备份恢复** - 支持用户备份恢复和管理员数据库备份
- **容器化部署** - 支持 Docker / Docker Compose 持久化部署

## 🚀 快速开始

**⚡ 推荐方式**：使用仓库内置的 Docker Compose 配置从源码构建并启动，和当前项目配置保持一致。

### 方式一：使用部署脚本（推荐）⭐

<details>
<summary>Linux / macOS</summary>

```bash
# 1. 克隆项目
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix

# 2. 执行部署脚本
chmod +x docker-deploy.sh
./docker-deploy.sh
```

脚本会自动检查依赖、创建目录、构建镜像并启动服务。  
默认访问地址：
- `docker-compose.yml`：`http://localhost:9000`
- `docker-compose-cn.yml`：`http://localhost:8000`

</details>

<details>
<summary>Windows</summary>

```cmd
:: 1. 克隆项目
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix

:: 2. 执行部署脚本
docker-deploy.bat
```

默认访问地址：
- `docker-compose.yml`：`http://localhost:9000`
- `docker-compose-cn.yml`：`http://localhost:8000`

</details>

### 方式二：手动使用 Docker Compose

#### 默认配置
```bash
# 1. 克隆项目
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix

# 2. 启动服务
docker compose up -d

# 3. 访问系统
# http://localhost:9000
```

#### 国内构建配置
```bash
# 1. 克隆项目
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix

# 2. 启动服务
docker compose -f docker-compose-cn.yml up -d --build

# 3. 访问系统
# http://localhost:8000
```

### 方式三：本地运行

```bash
# 1. 克隆项目
git clone https://github.com/GuDong2003/xianyu-auto-reply-fix.git
cd xianyu-auto-reply-fix

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install --upgrade pip
pip install -r requirements.txt

# 4. 安装 Playwright 浏览器
playwright install chromium
playwright install-deps chromium  # 仅 Linux 需要

# 5. 启动系统
python Start.py

# 6. 访问系统
# http://localhost:8090
```

> 本地运行请确保已安装 Node.js，否则 `PyExecJS` 相关功能无法正常使用。

### 📋 环境要求

- **Python**: 3.11+
- **Node.js**: 16+（用于 PyExecJS 执行 JavaScript）
- **系统**: Windows / Linux / macOS
- **架构**: x86_64 (amd64) / ARM64 (aarch64)
- **Docker**: 20.10+（Docker 部署）
- **Docker Compose**: 2.0+（Docker 部署）
- **浏览器依赖**: Playwright Chromium（本地运行需要安装）
- **资源建议**: 建议 2GB+ 内存，预留 10GB+ 存储空间

### 🖥️ 多架构支持

**支持的架构**:
- ✅ **linux/amd64** - Intel / AMD 处理器
- ✅ **linux/arm64** - ARM64 处理器

**构建方式**:
- ✅ 提供 `build-multi-arch.sh` 多架构构建脚本
- ✅ 支持使用 Docker Buildx 构建 amd64 / arm64 镜像
- ✅ Docker 部署和本地运行可在对应架构环境中使用

**说明**:
- 当前仓库未包含 GitHub Actions 自动构建配置
- 镜像仓库地址请以实际发布情况为准

### ⚙️ 环境变量配置（可选）

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
```

> 其他运行参数（如 WebSocket、心跳、自动回复等）主要在 `global_config.yml` 和 Web 管理界面中配置。

### 🔄 热更新发版

当前仓库的 GitHub Actions 会在 `push` 到 `main` 后读取 `static/version.txt`。如果该版本对应的 Release 还不存在，则会自动生成 `update_files.json` 并创建同名 Release。

自动纳入热更新清单的文件：
- 任意目录下的 `.py` 文件
- 任意目录下的 `.html` 文件
- `static/` 目录下的静态资源，例如 `.js`、`.css`、`.txt`、`.json`、图片和字体文件
- `static/` 目录下的前端源码文件，例如 `.ts`、`.tsx`、`.jsx`、`.vue`

默认排除的内容：
- 用户配置和运行时目录，例如 `global_config.yml`、`data/`、`logs/`、`browser_data/`、`update_backup/`、`venv/`
- 发布和部署文件，例如 `.github/`、`Dockerfile*`、`docker-compose*.yml`、`nginx/`
- 文档、脚本、数据库和缓存文件，例如 `.md`、`.sh`、`.sql`

建议的发版步骤：
1. 修改代码或新增需要热更新的文件
2. 更新 `static/version.txt` 为新的版本号
3. 执行 `python3 release_precheck.py`，检查是否存在删文件、改名、未跟踪文件或忘记升级版本号的情况
4. 提交并 `push` 到 `main`
5. 等待 Action 自动生成 Release 和 `update_files.json`

热更新在覆盖和新增文件之外，还支持通过 manifest 的 `deleted_files` 清理旧文件。删除前会先备份原文件，再执行清理。



### 🌐 访问系统

部署完成后，您可以通过以下方式访问系统：

- **Web管理界面**：
  - Docker Compose 默认配置: http://localhost:9000
  - Docker Compose 国内配置: http://localhost:8000
  - 本地运行: http://localhost:8090
- **默认管理员账号（首次初始化且未自定义密码时）**：
  - 用户名：`admin`
  - 密码：`admin123`
- **API文档**：
  - Docker Compose 默认配置: http://localhost:9000/docs
  - Docker Compose 国内配置: http://localhost:8000/docs
  - 本地运行: http://localhost:8090/docs
- **健康检查**：
  - Docker Compose 默认配置: http://localhost:9000/health
  - Docker Compose 国内配置: http://localhost:8000/health
  - 本地运行: http://localhost:8090/health

> ⚠️ **安全提示**：首次登录后请立即修改默认密码！


## 📋 系统使用

### 1. 用户注册
- 若系统已开启注册功能，可访问登录页或注册页（如本地运行使用 `http://localhost:8090/register.html`）
- 填写用户信息，完成邮箱验证
- 根据系统配置完成图形验证码校验

### 2. 添加闲鱼账号
- 登录系统后进入主界面
- 进入账号管理，添加新账号
- 输入账号 ID 和 Cookie 信息
- 可按需配置用户名、密码、备注和显示浏览器
- 保存后即可启用或刷新账号

### 3. 使用商品擦亮
- 在账号管理页可直接执行一键擦亮，批量处理当前账号在售商品
- 可按账号配置每日定时擦亮任务，支持执行时段和随机延迟

### 4. 配置自动回复
- **关键词回复**：设置通用关键词和商品专属关键词
- **AI回复**：配置兼容模型接口启用智能回复
- **默认回复**：设置未匹配时的默认回复
- **指定商品回复**：为特定商品设置专门回复内容

### 5. 设置自动发货
- 添加卡券和发货规则，配置商品与发货内容的匹配关系
- 支持文本、批量数据、API、图片等发货方式
- 系统检测到付款消息或小刀卡片后自动触发发货流程

### 6. 安装闲鱼聊天暗色模式脚本（可选）
本项目提供了一个油猴用户脚本，可为闲鱼官网聊天页面添加暗色模式支持。

**安装步骤**：
1. 安装油猴扩展（Tampermonkey）到浏览器
2. 打开 `static/userscripts/goofish-dark-mode.user.js`
3. 在油猴扩展中创建新脚本并粘贴保存
4. 访问闲鱼聊天页面后即可生效

**功能特性**：
- 支持开启 / 关闭 / 跟随系统三种模式
- 支持通过油猴菜单快速切换状态
- 提供适配聊天界面的暗色风格

## 🏗️ 系统架构

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

## ⚙️ 配置说明

### 全局配置文件
`global_config.yml` 包含详细的系统配置，支持：
- WebSocket连接参数
- API接口配置
- 自动回复设置
- 商品管理配置
- 日志配置等

## 📊 监控和维护

### 日志管理
- **实时日志**：Web界面查看实时系统日志
- **日志文件**：`logs/` 目录下的按日期分割的日志文件
- **日志级别**：支持DEBUG、INFO、WARNING、ERROR级别

## 🤝 贡献指南

欢迎为项目做出贡献！您可以通过以下方式参与：

### 📝 提交问题
- 在 [GitHub Issues](https://github.com/GuDong2003/xianyu-auto-reply-fix/issues) 中报告Bug
- 提出新功能建议和改进意见
- 分享使用经验和最佳实践

### 🔧 代码贡献
- Fork 项目到您的GitHub账号
- 创建功能分支：`git checkout -b feature/your-feature`
- 提交更改：`git commit -am 'Add some feature'`
- 推送分支：`git push origin feature/your-feature`
- 提交 Pull Request


## ❓ 常见问题

### 1. 端口被占用
- **Docker Compose**：修改 `docker-compose.yml` 或 `docker-compose-cn.yml` 中的端口映射
- **本地运行**：修改 `API_PORT` 环境变量，或调整 `global_config.yml` 中的 `AUTO_REPLY.api.port`

### 2. 数据库连接失败
检查 `data/` 目录和数据库文件权限，确保应用有读写权限；如使用自定义路径，确认 `DB_PATH` 配置正确。

### 3. WebSocket连接失败
检查网络和防火墙设置，并确认闲鱼账号 Cookie 仍然有效。

### 4. Shell脚本执行错误（Linux/macOS）
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

### 5. Docker容器启动失败
如果遇到 `exec /app/entrypoint.sh: no such file or directory` 错误：

```bash
docker compose down
docker compose build --no-cache
docker compose up -d
```

### 6. Windows系统部署
Windows 用户建议直接使用批处理脚本：

```cmd
docker-deploy.bat
```

## 📞 技术支持

### 📧 联系方式
- **问题反馈**：通过 [GitHub Issues](https://github.com/GuDong2003/xianyu-auto-reply-fix/issues) 提交
- **功能建议**：通过 GitHub Issues 提出改进建议
- **使用问题**：建议先查看本文档的“常见问题”部分

## 🧸 特别鸣谢

### 开源项目参考
- **[xianyu-auto-reply](https://github.com/zhinianboke-new/xianyu-auto-reply)** - 提供了原始项目基础与二次开发起点
- **[XianYuApis](https://github.com/cv-cat/XianYuApis)** - 提供了闲鱼 API 接口的技术参考
- **[XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent)** - 提供了自动化处理的实现思路
- **[myfish](https://github.com/Kaguya233qwq/myfish)** - 提供了扫码登录的实现思路

### 开发者支持
- **[syunnrai123](https://github.com/syunnrai123)** - 为当前项目的滑块处理方案提供思路与参考
- **[Mangor2021](https://github.com/Mangor2021)** - 为项目开发与改进提供了实际贡献
- **[82762294](https://github.com/82762294)** - 为项目开发与改进提供了实际贡献

## ⚖️ 版权声明与使用条款

### 📋 重要声明

本项目基于原项目整理和修复，仅供学习与研究使用，请勿用于商业用途或任何违法违规场景。

### 🚫 使用限制

- **禁止商业使用** - 不得将本项目或其衍生内容用于商业用途
- **禁止违法使用** - 不得将本项目用于任何违法违规活动
- **禁止滥用服务** - 不得利用本项目进行骚扰、欺诈或其他不当行为

### ✅ 使用说明

- **保留来源信息** - 使用、修改或分发时请保留原项目来源说明
- **标注修改内容** - 如基于本项目进行了修改，建议明确标注修改部分
- **自行承担风险** - 使用者需自行承担部署、配置和运行风险
- **遵守当地法规** - 使用者应确保实际用途符合当地法律法规和平台规则

### 👤 项目来源

- **原项目**：`zhinianboke-new/xianyu-auto-reply`
- **当前仓库**：`GuDong2003/xianyu-auto-reply-fix`

### ⚠️ 免责声明

本项目按“现状”提供，不提供任何明示或暗示的保证；因使用本项目产生的风险、损失或责任，由使用者自行承担。

## Star History
<a href="https://www.star-history.com/#GuDong2003/xianyu-auto-reply-fix&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=GuDong2003/xianyu-auto-reply-fix&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=GuDong2003/xianyu-auto-reply-fix&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=GuDong2003/xianyu-auto-reply-fix&type=Date" />
 </picture>
</a>
