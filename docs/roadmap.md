# 项目路线图

> 返回：[README](../README.md) ｜ 相关：[更新日志](changelog.md) ｜ [部署与运行指南](deployment.md) ｜ [配置说明](configuration.md)

本路线图用于记录 Xianyu Admin 作为二次维护版本的演进方向。目标不是一次性重写原项目，而是在保留上游能力的基础上，逐步改善部署体验、配置安全、运行稳定性、文档可维护性和后台使用体验。

## 维护原则

- **先稳定，再扩展**：优先保证 Docker、本地运行、健康检查、日志和数据持久化可靠。
- **小步提交**：每一批改动保持可验证、可回滚、可说明。
- **保留上游归属**：继续保留 AGPL-3.0 协议、LICENSE、原作者声明和上游链接。
- **适合公开维护**：避免提交 Cookie、Token、数据库、日志和真实配置。
- **兼容国内部署**：国内 Docker、pip、apt、Playwright 下载链路需要有清晰说明和可替换配置。

## 阶段 0：Fork 初始化与项目独立化

目标：让仓库从原项目 fork 变成一个清晰的二次维护版本。

当前状态：

- [x] 项目名统一为 `Xianyu Admin`
- [x] GitHub 仓库地址切换为 `qianmokano/Xianyu_admin`
- [x] 保留上游项目声明、AGPL-3.0 协议和 LICENSE
- [x] Docker 镜像名统一为 `xianyu-admin:latest`
- [x] Docker 服务名和容器名统一为 `xianyu-admin-app`
- [x] 国内 Docker Compose 构建链路验证通过
- [x] 部署脚本统一新服务名，并支持当前运行 compose 文件识别

验收标准：

- 国内 compose 可以构建、启动并通过 `/health`
- README 能清楚说明本仓库与上游项目的关系
- `origin` 指向自己的仓库，`upstream` 指向原作者仓库

## 阶段 1：部署体验优化

目标：让新用户可以更容易在 macOS、Linux、Windows 和国内网络环境中部署成功。

优先任务：

- [ ] 完善 `docker-deploy.sh` 的交互流程和错误提示
- [ ] 完善 `docker-deploy.bat` 的 Windows 部署体验
- [ ] 增加构建失败排查说明：Docker Hub、DaoCloud、Playwright、pip、apt 源
- [ ] 增加首次部署检查清单
- [ ] 补充常用运维命令：启动、停止、重启、日志、健康检查、清理
- [ ] 明确默认 compose 与国内 compose 的端口差异

可选增强：

- [ ] 增加 `Makefile` 或轻量命令入口，例如 `make up-cn`
- [ ] 增加 Docker 构建缓存和镜像清理说明
- [ ] 增加多架构构建的完整示例

验收标准：

- 用户按 README 或 `docs/deployment.md` 操作即可完成首次部署
- 国内环境无需手动猜测基础镜像和 Playwright 镜像源
- 部署失败时能从文档找到下一步排查方向

## 阶段 2：配置与安全基线

目标：让项目适合公开维护，降低敏感信息误提交和默认弱配置风险。

优先任务：

- [x] 检查 `.gitignore` 是否覆盖运行期目录和敏感文件
- [x] 整理 `.env.example`，区分示例值、必填项和生产建议
- [x] README 明确首次部署必须修改默认管理员密码
- [x] README 和配置文档说明 `JWT_SECRET_KEY`、AI API Key、Cookie 的保存方式
- [x] 检查 Docker Compose 中的默认密钥和默认账号提示
- [x] 增加生产部署安全提示

需要重点忽略的内容：

- `data/`
- `logs/`
- `backups/`
- `.env`
- SQLite 数据库
- 浏览器缓存和登录状态
- Cookie、Token、API Key

验收标准：

- 仓库中不包含真实敏感信息
- 新用户能知道哪些配置必须修改
- 示例配置可以用于本地启动，但不会暗示可直接用于生产

## 阶段 3：文档体系完善

目标：让项目不只是能跑，还能让别人看懂怎么用、怎么维护、怎么同步上游。

优先任务：

- [x] 新增项目路线图 `docs/roadmap.md`
- [x] 新增更新日志 `docs/changelog.md`
- [ ] 补充 FAQ 中的国内部署常见失败场景
- [ ] 补充上游同步流程和冲突处理建议
- [ ] 补充发布前检查命令清单
- [ ] 统一 README、部署文档、配置文档之间的交叉链接

验收标准：

- README 保持简洁，详细内容拆到 `docs/`
- 每次维护更新能在 changelog 中找到记录
- 路线图能指导后续分支和 issue 拆分

## 阶段 4：运行稳定性与可观测性

目标：方便长期运行、排错和维护。

候选任务：

- [ ] 优化 `/health` 返回信息和失败原因
- [ ] 增加 `/version` 或 `/api/system/info`
- [ ] 明确日志级别、日志目录和日志轮转策略
- [ ] Docker 启动失败时输出更友好的错误信息
- [ ] 增加数据库初始化和迁移状态检查
- [ ] 增加后台任务状态展示或接口

验收标准：

- 部署后能快速判断系统、数据库、Cookie 管理器和后台任务状态
- 常见启动失败能通过日志快速定位
- 文档中有清晰的排查路径

## 阶段 5：功能体验优化

目标：开始形成 Xianyu Admin 自己的产品体验。

候选任务：

- [ ] 后台页面文案统一为 Xianyu Admin
- [ ] 登录页显示当前版本号
- [ ] 管理后台导航结构优化
- [ ] 账号管理、自动回复、自动发货页面补充更清楚的状态提示
- [ ] 增加配置检测页面
- [ ] 增加一键查看系统状态页面

验收标准：

- 用户能明显感知这是 Xianyu Admin 维护版本
- 常用操作路径更短，错误提示更明确
- 不破坏原项目核心自动化能力

## 阶段 6：测试与质量保障

目标：降低改动部署和核心功能时的不确定性。

优先任务：

- [ ] 固化 Docker Compose config 校验命令
- [ ] 固化部署脚本 shell 语法检查
- [ ] 增加基础 smoke test：`/`、`/health`、`/docs`
- [ ] 增加数据库初始化测试
- [ ] README 或 release 文档记录发布前检查命令

后续增强：

- [ ] 单元测试
- [ ] API 测试
- [ ] GitHub Actions CI
- [ ] Docker 镜像自动构建

验收标准：

- 每次 PR 或本地提交前有一组明确检查命令
- 部署相关改动至少通过 compose config 和 health smoke test
- 核心接口变更有测试覆盖

## 阶段 7：上游同步与版本发布

目标：持续吸收原项目更新，同时保留 Xianyu Admin 自己的优化。

优先任务：

- [ ] 保持 `upstream` remote 指向原作者仓库
- [ ] 记录上游同步步骤和冲突处理方式
- [ ] 每次同步后检查 Dockerfile、compose、README、部署脚本和数据库迁移
- [ ] 使用 `docs/changelog.md` 记录维护版本改动
- [ ] 为稳定节点打 tag，例如 `v0.1.0-xianyu-admin`

建议同步命令：

```bash
git fetch upstream
git merge upstream/main
```

验收标准：

- 上游更新可以被定期同步
- 同步后的差异可追踪、可验证
- 用户能从 changelog 理解当前维护版本相对上游的变化
