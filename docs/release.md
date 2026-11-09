# 发版与热更新说明

> 返回：[README](../README.md) ｜ 相关：[部署与运行指南](deployment.md) ｜ [配置说明](configuration.md)

当前仓库在 `push` 到 `main` 后先构建镜像，并用临时容器验证 Node、PyExecJS、Playwright Chromium 和默认入口的 `/health`。检查通过后才推送多架构镜像；镜像工作流成功后，再读取该次受测提交的 `static/version.txt`，生成 `update_files.json` 并创建同名 Release。构建或启动验证失败时不创建正式 Release。

## 自动纳入热更新清单的文件

- 任意目录下的 `.py` 文件
- 任意目录下的 `.html` 文件
- `static/` 目录下的静态资源，例如 `.js`、`.css`、`.txt`、`.json`、图片和字体文件
- `static/` 目录下的前端源码文件，例如 `.ts`、`.tsx`、`.jsx`、`.vue`

## 默认排除的内容

- 用户配置和运行时目录，例如 `global_config.yml`、`data/`、`logs/`、`browser_data/`、`update_backup/`、`venv/`
- 发布和部署文件，例如 `.github/`、`Dockerfile*`、`docker-compose*.yml`、`nginx/`
- 文档、脚本、数据库和缓存文件，例如 `.md`、`.sh`、`.sql`

## 建议发版步骤

1. 修改代码或新增需要热更新的文件。
2. 更新 `static/version.txt` 为新的版本号。
3. 在 `static/update_log.txt` 顶部追加新版本更新内容；首行使用
   `发布 vX.Y.Z（YYYY-MM-DD）：更新摘要` 格式，主页会自动读取版本、日期和后续编号条目。
4. 执行发布前检查：

   ```bash
   python3 release_precheck.py
   ```

5. 提交并 `push` 到 `main`。
6. 等待容器启动验证、多架构镜像构建和推送成功，再确认自动生成的 Release 与 `update_files.json`。

热更新在覆盖和新增文件之外，还支持通过 manifest 的 `deleted_files` 清理旧文件。删除前会先备份原文件，再执行清理。
