#!/bin/bash
# 闲鱼运营管理系统（Xianyu Admin）停止脚本

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "正在停止闲鱼运营管理系统（Xianyu Admin）..."

# 递归获取某个 PID 的所有子孙进程。
get_descendants() {
    local parent="$1"
    local children child
    children=$(pgrep -P "$parent" 2>/dev/null || true)
    for child in $children; do
        echo "$child"
        get_descendants "$child"
    done
}

is_alive() {
    kill -0 "$1" 2>/dev/null
}

wait_pids_exit() {
    local timeout_seconds="$1"
    shift
    local pids="$*"
    local loops=$((timeout_seconds * 10))
    local i pid alive

    [ -z "$pids" ] && return 0

    for ((i = 0; i < loops; i++)); do
        alive=0
        for pid in $pids; do
            if is_alive "$pid"; then
                alive=1
                break
            fi
        done
        [ "$alive" -eq 0 ] && return 0
        sleep 0.1
    done

    return 1
}

unique_pids() {
    printf '%s\n' "$@" | awk 'NF && !seen[$1]++'
}

# 只匹配当前项目的 Start.py，避免误杀其它同名项目。
start_pids=$(pgrep -f "${PROJECT_DIR}/Start.py" 2>/dev/null || true)
if [ -z "$start_pids" ]; then
    # 兼容从项目目录内以相对路径启动的情况。
    start_pids=$(pgrep -f "python.*Start.py" 2>/dev/null || true)
fi

if [ -n "$start_pids" ]; then
    # 先停止 Python 主进程派生出来的 node 子进程。
    # ExecJS/扫码登录链路的 node 命令行不一定包含固定 js 文件名；如果先停 Python，
    # node 可能继续向已关闭的 stdout/stderr pipe 写入并打印 write EPIPE。
    node_pids=""
    for start_pid in $start_pids; do
        descendants=$(get_descendants "$start_pid")
        for pid in $descendants; do
            comm=$(ps -p "$pid" -o comm= 2>/dev/null | awk '{print $1}')
            args=$(ps -p "$pid" -o command= 2>/dev/null || true)
            if [ "$comm" = "node" ] || echo "$args" | grep -qE '(^|/)node( |$)'; then
                node_pids="$node_pids $pid"
            fi
        done
    done

    # 兜底：项目内已知的 node 脚本名。
    known_node_pids=$(pgrep -f "${PROJECT_DIR}/utils/gen_tfstk.js|${PROJECT_DIR}/utils/et_f.js|utils/gen_tfstk.js|utils/et_f.js" 2>/dev/null || true)
    node_pids=$(unique_pids $node_pids $known_node_pids)

    if [ -n "$node_pids" ]; then
        kill -TERM $node_pids 2>/dev/null || true
        if ! wait_pids_exit 2 $node_pids; then
            kill -KILL $node_pids 2>/dev/null || true
            wait_pids_exit 1 $node_pids >/dev/null 2>&1 || true
        fi
    fi

    # 再停止 Python 主进程。
    kill -TERM $start_pids 2>/dev/null || true
    if ! wait_pids_exit 5 $start_pids; then
        echo "正在强制停止..."
        kill -KILL $start_pids 2>/dev/null || true
        wait_pids_exit 2 $start_pids >/dev/null 2>&1 || true
    fi

    # 最后兜底清理启动过程中可能残留的已知 node 子进程。
    pkill -KILL -f "${PROJECT_DIR}/utils/gen_tfstk.js|${PROJECT_DIR}/utils/et_f.js|utils/gen_tfstk.js|utils/et_f.js" 2>/dev/null || true

    echo "已停止"
else
    echo "程序未在运行"
fi
