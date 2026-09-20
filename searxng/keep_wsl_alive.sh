#!/bin/bash
# WSL 保活 + SearXNG 自愈脚本
#
# 背景：本机 WSL2 在最后一个 wsl.exe 会话退出后会关停整个 distro（.wslconfig 的
# vmIdleTimeout=-1 未能阻止 per-distro 空闲关停），导致 dockerd 与 searxng 容器
# 每次都冷启动（10-20s），期间检索全部失败。
#
# 本脚本设计为一个常驻前台进程（由 Windows 端隐藏窗口 / 任务计划在登录后启动）：
# 只要它活着，WSL 就不会空闲关停；同时每 30s 自愈一次 docker 服务与 searxng 容器。
#
# Windows 端启动（隐藏后台窗口，关机/注销前常驻）：
#   wsl -d Ubuntu-20.04 -- bash -lc "nohup bash <项目根目录>/searxng/keep_wsl_alive.sh >/tmp/keepalive.log 2>&1 &"
# 或注册到任务计划“登录时启动”（见 README 部署章节）。
set -u
COMPOSE_DIR="<项目根目录>/searxng"

echo "[keepalive] started at $(date '+%F %T'), keeping WSL awake + searxng healthy"
while true; do
  # 1) docker 服务不在线则拉起
  if ! systemctl is-active --quiet docker; then
    echo "[keepalive] $(date '+%T') docker down -> starting"
    sudo systemctl start docker 2>/dev/null || systemctl start docker 2>/dev/null
    sleep 5
  fi

  # 2) searxng 容器不在线则 compose 拉起
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx searxng; then
    :
  else
    echo "[keepalive] $(date '+%T') searxng container missing -> docker compose up -d"
    (cd "$COMPOSE_DIR" && docker compose up -d) 2>/dev/null
  fi
  sleep 30
done
