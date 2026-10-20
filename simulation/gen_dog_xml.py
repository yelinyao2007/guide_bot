#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VbotBaboEDU URDF → 带 position 执行器的 MJCF（网格改相对路径）。

用法: python3 gen_dog_xml.py <dog_abs.urdf> <out.xml>
out.xml 旁的 meshes/ 目录需已放好 STL（fetch_assets.sh 会办）。
"""
import re
import sys

import mujoco


def main():
    urdf, out = sys.argv[1], sys.argv[2]
    model = mujoco.MjModel.from_xml_path(urdf)
    tmp = out + ".base.xml"
    mujoco.mj_saveLastXML(tmp, model)
    s = open(tmp).read()

    # 网格绝对路径 → 相对 meshes/；同时清空原 compiler meshdir 防路径叠套
    s = re.sub(r'file="[^"]*/([^"/]+)"', r'file="meshes/\1"', s)
    s = re.sub(r'meshdir="[^"]*"', 'meshdir=""', s)

    # 注入 position 执行器（viewer/控关节用；仿真里狗保持站姿）
    joints = re.findall(r'<joint[^>]*name="([^"]+)"[^>]*type="hinge"', s) or \
        re.findall(r'<joint[^>]*name="([^"]+)"', s)
    actuators = "\n  <actuator>\n" + "".join(
        f'    <position joint="{j}" kp="150" kv="8" ctrllimited="true" ctrlrange="-3.14 3.14"/>\n'
        for j in joints) + "  </actuator>\n"
    s = s.replace("</mujoco>", actuators + "</mujoco>")
    open(out, "w").write(s)
    print(f"OK: {out} joints={len(joints)}")


if __name__ == "__main__":
    main()
