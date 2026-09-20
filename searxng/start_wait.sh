#!/bin/bash
# WSL 启动后等待 docker 与 SearXNG 容器完全就绪（轮询而非固定 sleep）。
# 用法（WSL 内）：bash start_wait.sh

echo "等待 docker 服务..."
for i in $(seq 1 30); do
  systemctl is-active docker >/dev/null 2>&1 && break
  sleep 1
done
echo "docker = $(systemctl is-active docker)"

echo "等待 SearXNG 就绪（轮询 127.0.0.1:8888）..."
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    "http://127.0.0.1:8888/search?q=ping&format=json" 2>/dev/null)
  if [ "$code" = "200" ]; then
    echo "SearXNG 就绪（第 ${i} 次探测，约 $((i*2)) 秒）"
    break
  fi
  sleep 2
done

docker ps --filter name=searxng --format '{{.Names}} | {{.Status}}'
echo "=== 功能验证 ==="
curl -s "http://127.0.0.1:8888/search?q=gaussian+splatting&format=json&language=zh-CN" \
  -o /tmp/f.json -w "http=%{http_code} t=%{time_total}s\n" --max-time 25
python3 -c "import json;d=json.load(open('/tmp/f.json'));print('results=',len(d.get('results',[])))" 2>/dev/null || echo "JSON 失败"
