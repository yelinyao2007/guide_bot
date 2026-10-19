#!/bin/bash
# 虚拟机内一键起/重启全套：backend(看板+任务) + 空位仿真( MuJoCo + 假感知 + 编排HTTP )
# 用法: bash ~/guide_bot/scripts/start_all_vm.sh [--viewer]
set -e
VIEWER=""
[ "$1" = "--viewer" ] && VIEWER="--viewer"

pkill -f "uvicorn [b]ackend" 2>/dev/null || true
fuser -k 8001/tcp 2>/dev/null || true
pkill -f "[r]un_sim" 2>/dev/null || true
sleep 2

cd ~/guide_bot
nohup env ROBOT_BACKEND=simvbot ~/gbvenv/bin/python -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port 8000 > /tmp/gb_backend.log 2>&1 < /dev/null &
nohup bash -c "source /opt/ros/humble/setup.bash; source ~/ws/install/setup.bash; \
  cd ~/guide_bot/simulation && python3 run_sim.py $VIEWER" > /tmp/gb_sim.log 2>&1 < /dev/null &
sleep 10

echo "== backend :8000 =="; curl -s -m 5 localhost:8000/api/health || echo 未就绪
echo; echo "== sim :8001 =="; curl -s -m 5 localhost:8001/state | head -c 200 || echo 未就绪
echo; echo "看板地址: http://localhost:8000 （Mac 浏览器: http://192.168.64.2:8000）"
