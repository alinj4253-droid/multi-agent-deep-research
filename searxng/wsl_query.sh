#!/bin/bash
# 供 Windows 后端经 "wsl -d Ubuntu-20.04 -- bash <本脚本> <urlencoded-query>" 调用。
# 在 WSL 内部经 127.0.0.1 访问 SearXNG（必须用 127.0.0.1，localhost 偶发解析失败），
# 绕过 Windows->WSL 被代理 TUN(Meta 网卡) 劫持的入站路由。
#
# 内置轮询重试：容器冷启动(granian 初始化约 10s)或 WSL 网络抖动时，
# 每个新 wsl 会话的前几次请求可能失败(returncode 56/000)，在此吸收，
# 直到拿到含 "results" 的 JSON 再输出，保证 Windows 侧单次调用即可稳定取结果。
Q="${1:-gaussian+splatting}"
URL="http://127.0.0.1:8888/search?q=${Q}&format=json&language=zh-CN"

OUT=""
for i in $(seq 1 15); do
  OUT=$(curl -s "$URL" --max-time 25 2>/dev/null)
  # 成功判据必须是“results 数组里至少有一个结果对象”，仅出现 "results": []
  # （引擎冷启动 / 全部超时时 SearXNG 也会返回空数组）不算成功，继续重试
  if printf '%s' "$OUT" | grep -Eq '"results"[[:space:]]*:[[:space:]]*\[[^]]*\{'; then
    printf '%s' "$OUT"
    exit 0
  fi
  sleep 3
done
# 全部重试失败：输出最后一次内容（可能为空），由 Windows provider 感知并降级
printf '%s' "$OUT"
exit 1
