#!/bin/bash
# 闲鱼运营管理系统（Xianyu Admin）启动脚本

cd "$(dirname "$0")"

echo "========================================"
echo "  闲鱼运营管理系统（Xianyu Admin）"
echo "========================================"

# 检查是否已有实例在运行
if pgrep -f "Start.py" > /dev/null; then
    echo "检测到程序已在运行"
    echo ""
    read -p "是否要重启? (y/n): " choice
    if [ "$choice" = "y" ] || [ "$choice" = "Y" ]; then
        echo "正在停止现有进程..."
        pkill -f "Start.py"
        sleep 2
    else
        echo "保持现有进程运行"
        echo "Web管理界面: http://localhost:8090"
        exit 0
    fi
fi

# 启动程序
echo "正在启动..."
./venv/bin/python -u Start.py 2>&1 &

# 等待启动
sleep 5

# 检查是否启动成功
if pgrep -f "Start.py" > /dev/null; then
    echo "========================================"
    echo "  启动成功!"
    echo "  Web管理界面: http://localhost:8090"
    echo "========================================"
else
    echo "启动失败，请检查日志"
    exit 1
fi
