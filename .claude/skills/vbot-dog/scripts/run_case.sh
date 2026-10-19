#!/bin/bash
# 一键部署并运行"空位播报"案例：推脚本上狗 → ssh 源环境 → 执行
# 用法：bash run_case.sh [狗IP，默认 192.168.126.2]
set -e
HOST="${1:-192.168.126.2}"
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "== 推送脚本到狗 =="
scp "$HERE/empty_seat_finder.py" "vbot@$HOST:~/"

echo "== 在狗上运行（先确认狗离地/有人扶稳，急停在手边）=="
ssh "vbot@$HOST" 'bash -lc "
export PATH=/opt/vita/aorta/bin:\$PATH
export ZENOH_SESSION_CONFIG_URI=/opt/vita/aorta/edu/edu_session.json5
source /opt/vita/ros/humble/setup.bash
source /opt/vita/aorta/ros/local_setup.bash
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp ROS_DOMAIN_ID=178 ROS_LOCALHOST_ONLY=1
python3 ~/empty_seat_finder.py
"'
