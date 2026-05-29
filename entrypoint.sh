#!/bin/bash
set -e

echo "========================================"
echo "  闲鱼自动回复系统 - 启动中..."
echo "========================================"

# 显示环境信息
echo "环境信息："
echo "  - Python版本: $(python --version)"
echo "  - 工作目录: $(pwd)"
echo "  - 时区: ${TZ:-未设置}"
echo "  - 数据库路径: ${DB_PATH:-/app/data/xianyu_data.db}"
echo "  - 日志级别: ${LOG_LEVEL:-INFO}"

# 禁用 core dumps 防止生成 core 文件
ulimit -c 0
echo "✓ 已禁用 core dumps"

# 创建必要的目录
echo "创建必要的目录..."
mkdir -p /app/data /app/logs /app/backups /app/static/uploads/images
mkdir -p /app/trajectory_history
echo "✓ 目录创建完成"

# 设置目录权限
echo "设置目录权限..."
chmod 777 /app/data /app/logs /app/backups /app/static/uploads /app/static/uploads/images
chmod 777 /app/trajectory_history 2>/dev/null || true
echo "✓ 权限设置完成"

# 检查关键文件
echo "检查关键文件..."
if [ ! -f "/app/global_config.yml" ]; then
    echo "⚠ 警告: 全局配置文件不存在，将使用默认配置"
fi

if [ ! -f "/app/Start.py" ]; then
    echo "✗ 错误: Start.py 文件不存在！"
    exit 1
fi
echo "✓ 关键文件检查完成"

# 检查 Python 依赖
echo "检查 Python 依赖..."
python -c "import fastapi, uvicorn, loguru, websockets" 2>/dev/null || {
    echo "⚠ 警告: 部分 Python 依赖可能未正确安装"
}
echo "✓ Python 依赖检查完成"

# 迁移数据库文件到data目录（如果需要）
echo "检查数据库文件位置..."
if [ -f "/app/xianyu_data.db" ] && [ ! -f "/app/data/xianyu_data.db" ]; then
    echo "发现旧数据库文件，迁移到data目录..."
    mv /app/xianyu_data.db /app/data/xianyu_data.db
    echo "✓ 主数据库已迁移"
elif [ -f "/app/xianyu_data.db" ] && [ -f "/app/data/xianyu_data.db" ]; then
    echo "⚠ 检测到新旧数据库都存在，使用data目录中的数据库"
    echo "  旧文件: /app/xianyu_data.db"
    echo "  新文件: /app/data/xianyu_data.db"
fi

if [ -f "/app/user_stats.db" ] && [ ! -f "/app/data/user_stats.db" ]; then
    echo "迁移统计数据库到data目录..."
    mv /app/user_stats.db /app/data/user_stats.db
    echo "✓ 统计数据库已迁移"
fi

# 迁移备份文件
backup_count=$(ls /app/xianyu_data_backup_*.db 2>/dev/null | wc -l)
if [ "$backup_count" -gt 0 ]; then
    echo "发现 $backup_count 个备份文件，迁移到data目录..."
    mv /app/xianyu_data_backup_*.db /app/data/ 2>/dev/null || true
    echo "✓ 备份文件已迁移"
fi

echo "✓ 数据库文件位置检查完成"

# 启动虚拟显示（如果启用有头模式）
if [ "${USE_XVFB}" = "true" ] || [ "${ENABLE_HEADFUL}" = "true" ]; then
    echo "========================================"
    echo "  启动虚拟显示服务器（Xvfb）"
    echo "========================================"
    
    # 清理可能存在的旧锁文件和进程
    echo "检查并清理旧的 Xvfb 进程..."
    
    # 查找并杀死旧的 Xvfb 进程
    pkill -9 Xvfb 2>/dev/null || true
    pkill -9 x11vnc 2>/dev/null || true
    
    # 清理锁文件
    rm -f /tmp/.X*-lock 2>/dev/null || true
    rm -f /tmp/.X11-unix/X* 2>/dev/null || true
    
    # 等待进程完全退出
    sleep 1
    
    # 尝试多个显示编号（从 99 开始）
    DISPLAY_NUM=99
    MAX_ATTEMPTS=10
    XVFB_STARTED=false
    
    for i in $(seq 0 $MAX_ATTEMPTS); do
        DISPLAY_NUM=$((99 + i))
        echo "尝试启动 Xvfb :$DISPLAY_NUM ..."
        
        # 尝试启动 Xvfb
        Xvfb :$DISPLAY_NUM -screen 0 1920x1080x24 -ac +extension GLX +render -noreset > /tmp/xvfb.log 2>&1 &
        XVFB_PID=$!
        
        # 等待启动
        sleep 2
        
        # 检查是否启动成功
        if ps -p $XVFB_PID > /dev/null 2>&1; then
            export DISPLAY=:$DISPLAY_NUM
            XVFB_STARTED=true
            echo "✓ Xvfb 启动成功 (PID: $XVFB_PID, DISPLAY: $DISPLAY)"
            break
        else
            echo "  显示 :$DISPLAY_NUM 启动失败，尝试下一个..."
            # 清理这次尝试的锁文件
            rm -f /tmp/.X${DISPLAY_NUM}-lock 2>/dev/null || true
        fi
    done
    
    if [ "$XVFB_STARTED" = "true" ]; then
        if command -v fluxbox >/dev/null 2>&1; then
            echo "启动窗口管理器 fluxbox..."
            fluxbox > /tmp/fluxbox.log 2>&1 &
            FLUXBOX_PID=$!
            sleep 1

            if ps -p $FLUXBOX_PID > /dev/null 2>&1; then
                echo "✓ fluxbox 启动成功 (PID: $FLUXBOX_PID)"
            else
                echo "⚠ fluxbox 启动失败，查看日志: /tmp/fluxbox.log"
            fi
        else
            echo "⚠ fluxbox 未安装，跳过窗口管理器"
        fi

        # 可选：启动 VNC 服务器用于远程查看（如果需要）
        if [ "${ENABLE_VNC}" = "true" ]; then
            echo "启动 VNC 服务器..."
            x11vnc -display $DISPLAY -forever -shared -rfbport 5900 -nopw > /tmp/x11vnc.log 2>&1 &
            VNC_PID=$!
            sleep 1
            
            if ps -p $VNC_PID > /dev/null 2>&1; then
                echo "✓ VNC 服务器启动成功 (PID: $VNC_PID, 端口: 5900)"
                echo "  可以通过 VNC 客户端连接到 <容器IP>:5900 查看浏览器界面"
            else
                echo "⚠ VNC 服务器启动失败，查看日志: /tmp/x11vnc.log"
            fi

            if command -v websockify >/dev/null 2>&1 && [ -d "/usr/share/novnc" ]; then
                echo "启动 noVNC Web 代理..."
                websockify --web=/usr/share/novnc 6080 localhost:5900 > /tmp/novnc.log 2>&1 &
                NOVNC_PID=$!
                sleep 1

                if ps -p $NOVNC_PID > /dev/null 2>&1; then
                    echo "✓ noVNC 启动成功 (PID: $NOVNC_PID, 端口: 6080)"
                    echo "  可通过浏览器访问 http://localhost:6080/vnc.html?autoconnect=1&resize=scale"
                else
                    echo "⚠ noVNC 启动失败，查看日志: /tmp/novnc.log"
                fi
            else
                echo "⚠ noVNC 未安装，跳过 Web VNC 代理"
            fi
        fi
    else
        echo "⚠ Xvfb 启动失败（尝试了 $MAX_ATTEMPTS 次），将使用无头模式"
        echo "  查看详细日志: /tmp/xvfb.log"
        unset DISPLAY
    fi
    
    echo "========================================"
fi

# 显示启动信息
echo "========================================"
echo "  系统启动参数："
echo "  - API端口: ${API_PORT:-8090}"
echo "  - API主机: ${API_HOST:-0.0.0.0}"
echo "  - Debug模式: ${DEBUG:-false}"
echo "  - 自动重载: ${RELOAD:-false}"
echo "  - 虚拟显示: ${USE_XVFB:-false}"
echo "  - VNC服务: ${ENABLE_VNC:-false}"
echo "  - DISPLAY: ${DISPLAY:-未设置}"
echo "========================================"

# 启动应用
echo "正在启动应用..."
echo ""

# 使用 exec 替换当前 shell，这样 Python 进程可以接收信号
exec python Start.py
