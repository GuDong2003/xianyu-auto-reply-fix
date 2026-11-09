# 使用Python 3.11作为基础镜像
# 支持通过构建参数指定镜像源（解决多架构构建时的网络问题）
# 使用方法：docker build --build-arg BASE_IMAGE=ccr.ccs.tencentyun.com/dockerp/library/python:3.11-slim-bookworm
ARG BASE_IMAGE=python:3.11-slim-bookworm
ARG NODE_IMAGE=node:24.22.2-bookworm-slim
FROM ${NODE_IMAGE} AS node-runtime
FROM ${BASE_IMAGE}

# 设置标签信息
LABEL maintainer="GuDong2003"
LABEL version="2.1.0"
LABEL description="闲鱼管理系统 - GuDong2003 维护版本，支持多用户、多账号与自动化管理"
LABEL repository="https://github.com/GuDong2003/xianyu-auto-reply-fix"
LABEL license="仅供学习与研究使用，禁止商业用途"
LABEL author="GuDong2003"
LABEL build-date=""
LABEL vcs-ref=""

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Asia/Shanghai
ENV DOCKER_ENV=true
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
ENV PLAYWRIGHT_NODEJS_PATH=/usr/local/bin/node
ENV NODE_PATH=/usr/local/lib/node_modules

# Use HTTPS Debian mirrors with retries; HTTP and some domestic mirrors are unreliable here.
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖（包括Playwright浏览器依赖）
RUN apt-get -o Acquire::Retries=5 update && \
    apt-get -o Acquire::Retries=5 install -y --no-install-recommends \
        # 基础工具
        libstdc++6 \
        libatomic1 \
        tzdata \
        curl \
        ca-certificates \
        # 图像处理依赖
        libjpeg-dev \
        libpng-dev \
        libfreetype6-dev \
        fonts-dejavu-core \
        fonts-liberation \
        # Playwright浏览器依赖
        libnss3 \
        libnspr4 \
        libatk-bridge2.0-0 \
        libdrm2 \
        libxkbcommon0 \
        libxcomposite1 \
        libxdamage1 \
        libxrandr2 \
        libgbm1 \
        libxss1 \
        libasound2 \
        libatspi2.0-0 \
        libgtk-3-0 \
        libgdk-pixbuf2.0-0 \
        libxcursor1 \
        libxi6 \
        libxrender1 \
        libxext6 \
        libx11-6 \
        libxft2 \
        libxinerama1 \
        libxtst6 \
        libappindicator3-1 \
        libx11-xcb1 \
        libxfixes3 \
        xdg-utils \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        fluxbox \
        # OpenCV运行时依赖
        libgl1 \
        libglib2.0-0 \
        && apt-get clean \
        && rm -rf /var/lib/apt/lists/* \
        && rm -rf /tmp/* \
        && rm -rf /var/tmp/*

# 设置时区
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 从同为 Bookworm 的官方镜像复制对应架构的 Node，避免 Debian 源的 Node 18。
# 可通过 --build-arg NODE_IMAGE=... 指定兼容镜像源。
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
COPY utils/node_runtime.py /app/utils/node_runtime.py
RUN ln -sf /usr/local/bin/node /usr/local/bin/nodejs && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    python -c "from utils.node_runtime import configure_playwright_node; configure_playwright_node()" && \
    node --version && npm --version

# 复制requirements.txt并安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple&& \
    pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目文件
COPY . .

# 安装Playwright浏览器（必须在复制项目文件之后）
RUN playwright install chromium && \
    playwright install-deps chromium && \
    CHROME_BIN="$(find /ms-playwright -type f -path '*/chrome-linux*/chrome' | head -n 1)" && \
    test -n "$CHROME_BIN" && \
    ln -sf "$CHROME_BIN" /usr/bin/chromium && \
    ln -sf "$CHROME_BIN" /usr/bin/chromium-browser && \
    ln -sf "$CHROME_BIN" /usr/bin/google-chrome

# 创建必要的目录并设置权限
RUN mkdir -p /app/logs /app/data /app/backups /app/static/uploads/images && \
    chmod 777 /app/logs /app/data /app/backups /app/static/uploads /app/static/uploads/images

# 配置系统限制，防止core文件生成
RUN echo "ulimit -c 0" >> /etc/profile

# 注意: 为了简化权限问题，使用root用户运行
# 在生产环境中，建议配置适当的用户映射

# 暴露端口
EXPOSE 8090
EXPOSE 6080

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8090/health || exit 1

# 复制启动脚本
# 复制启动脚本和调试工具
COPY entrypoint.sh /app/entrypoint.sh
COPY debug-xvfb.sh /app/debug-xvfb.sh

# 设置执行权限（使用多种方式确保权限正确）
RUN chmod +x /app/entrypoint.sh /app/debug-xvfb.sh && \
    chmod 755 /app/entrypoint.sh /app/debug-xvfb.sh && \
    ls -la /app/entrypoint.sh /app/debug-xvfb.sh

# 启动命令
CMD ["/app/entrypoint.sh"]
