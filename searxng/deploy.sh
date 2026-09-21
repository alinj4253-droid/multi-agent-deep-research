#!/bin/bash
# SearXNG 一键部署 + 验证（WSL2 Docker）
# 用法（在 WSL2 Ubuntu 内，进入本项目 searxng/ 目录）：
#   bash deploy.sh
set -e
cd "$(cd "$(dirname "$0")" && pwd)"

# 消除 Windows CRLF 影响
sed -i 's/\r$//' settings.yml

echo "=== 启动/更新 SearXNG 容器 ==="
docker compose up -d

echo "等待服务初始化（12 秒）..."
sleep 12

echo ""
echo "=== 稳定性检查：granian 启动次数（稳定应为 1）==="
N=$(docker logs searxng 2>&1 | grep -c "Starting granian" || true)
echo "granian starts = $N"
docker ps --filter name=searxng --format '{{.Names}} | {{.Status}}'

echo ""
echo "=== JSON API 测试（WSL 内 localhost:8888）==="
curl -s "http://localhost:8888/search?q=gaussian+splatting&format=json" \
  -o /tmp/sx.json -w "http_code=%{http_code}  time=%{time_total}s\n"

python3 - <<'EOF'
import json
try:
    d = json.load(open('/tmp/sx.json'))
except Exception as e:
    print("JSON 解析失败:", e)
    print(open('/tmp/sx.json', encoding='utf-8', errors='replace').read()[:400])
    raise SystemExit(1)
print("results =", len(d.get("results", [])))
print("unresponsive =", [e[0] for e in d.get("unresponsive_engines", [])])
for r in d.get("results", [])[:6]:
    print("-", (r.get("title") or "")[:70], "|", r.get("engines"))
EOF
