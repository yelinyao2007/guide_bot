#!/bin/bash
# 虚拟机内一键起/重启：backend(看板+任务)。
# 默认 = 真机模式(ROBOT_BACKEND=vbot)，并清理任何残留 MuJoCo 进程（用户裁决：MuJoCo 默认关）。
# 用法:
#   bash start_all_vm.sh                     → 真机模式（狗没连时看板显示未连接，属真实状态）
#   bash start_all_vm.sh --with-sim          → 空位仿真 + simvbot 模式（MuJoCo，:8001；按需，用前知会）
#   bash start_all_vm.sh --with-sim --viewer → 同上 + 虚拟机桌面 3D 窗口（需 Xorg 会话）
set -e
WITH_SIM=0
VIEWER=""
for a in "$@"; do
  [ "$a" = "--with-sim" ] && WITH_SIM=1
  [ "$a" = "--viewer" ] && { VIEWER="--viewer"; WITH_SIM=1; }
done

pkill -f "uvicorn [b]ackend" 2>/dev/null || true
# 无论何种模式，先清干净 MuJoCo 残留（裁决：默认不启仿真；v4 修复：默认模式也清孤儿）
fuser -k 8001/tcp 2>/dev/null || true
pkill -f "[r]un_sim" 2>/dev/null || true
pkill -f "[m]jdog2" 2>/dev/null || true
sleep 2

cd ~/guide_bot
BACKEND_MODE=vbot
[ "$WITH_SIM" = "1" ] && BACKEND_MODE=simvbot
nohup env ROBOT_BACKEND=$BACKEND_MODE ~/gbvenv/bin/python -m uvicorn backend.app.main:app \
  --host 0.0.0.0 --port 8000 > /tmp/gb_backend.log 2>&1 < /dev/null &
if [ "$WITH_SIM" = "1" ]; then
  nohup bash -c "export DISPLAY=:0; [ -n \"$VIEWER\" ] || export MUJOCO_GL=osmesa; \
    source /opt/ros/humble/setup.bash; source ~/ws/install/setup.bash; \
    cd ~/guide_bot/simulation && python3 run_sim.py $VIEWER" > /tmp/gb_sim.log 2>&1 < /dev/null &
fi
sleep 10

echo "== backend :8000 (模式: $BACKEND_MODE) =="; curl -s -m 5 localhost:8000/api/health || echo 未就绪
echo
if [ "$WITH_SIM" = "1" ]; then
  echo "== sim :8001 =="; curl -s -m 5 localhost:8001/state | head -c 200 || echo 未就绪
  echo
fi
echo "看板: http://localhost:8000 （Mac: http://192.168.64.2:8000）"
