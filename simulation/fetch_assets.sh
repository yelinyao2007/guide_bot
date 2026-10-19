#!/bin/bash
# 拉取狗模型资产（网格+URDF）并生成带执行器的 MJCF
# 已有 vbot-lab 克隆时可设 VBOT_LAB_DIR 跳过下载
set -e
cd "$(dirname "$0")"
R="$(pwd)/assets/"
mkdir -p assets

if [ ! -d assets/meshes ]; then
  if [ -n "$VBOT_LAB_DIR" ] && [ -d "$VBOT_LAB_DIR/assets/robots/foot_quadruped/meshes" ]; then
    SRC="$VBOT_LAB_DIR/assets/robots/foot_quadruped"
  else
    TMP=/tmp/vbot-lab-$$
    git clone -q --depth 1 https://ghfast.top/https://github.com/VitaDynamics/vbot-lab.git "$TMP"
    SRC="$TMP/assets/robots/foot_quadruped"
  fi
  cp -r "$SRC/meshes" assets/
  cp "$SRC/urdf/VbotBaboEDU.urdf" assets/
  [ -d /tmp/vbot-lab-$$ ] && rm -rf /tmp/vbot-lab-$$
fi

# package:// → 绝对路径（MuJoCo 不认 ROS 包协议）
sed "s|package:/VbotBaboEDU/|$R|g; s|package://VbotBaboEDU/|$R|g" \
  assets/VbotBaboEDU.urdf > assets/dog_abs.urdf
python3 gen_dog_xml.py assets/dog_abs.urdf assets/vbot_babo_edu_act.xml
echo "✅ 资产就绪: assets/vbot_babo_edu_act.xml"
