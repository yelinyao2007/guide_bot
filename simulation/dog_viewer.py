#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""活狗 3D 镜像窗口：独立进程轮询 run_sim 的 /state，把真实关节 qpos + 底座/人物位姿
同步进本地 MuJoCo 场景渲染。与仿真进程隔离——本窗口崩了不连累仿真。

用法（一般不用手动跑：看板「开 3D 窗口」按钮 / POST :8001/viewer/open 会拉起）：
    DISPLAY=:0 python3 dog_viewer.py [sim_url]
"""
import json
import sys
import time
import urllib.request

import mujoco
import mujoco.viewer
from scene_tools import compose_scene

SIM_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001").rstrip("/")
PEOPLE_BODIES = ("person", "person2", "person3")


def fetch_state():
    with urllib.request.urlopen(SIM_URL + "/state", timeout=2) as resp:
        return json.load(resp)


def main():
    xml, dog_loaded = compose_scene()
    model = mujoco.MjModel.from_xml_string(xml)
    for i in range(model.ngeom):
        model.geom_contype[i] = 0
        model.geom_conaffinity[i] = 0
    model.dof_damping[-2:] = 2.0
    model.dof_damping[:-2] += 0.3
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    mids = {name: model.body(name).mocapid[0] for name in PEOPLE_BODIES}
    dog_mid = model.body("dog_root").mocapid[0] if dog_loaded else None

    handle = mujoco.viewer.launch_passive(model, data)
    print("🪟 3D 镜像窗口已开（关掉窗口即退出；仿真不受影响）")
    while handle.is_running():
        try:
            st = fetch_state()
        except Exception:
            time.sleep(0.5)
            continue
        if dog_loaded and st.get("dog_loaded"):
            dp = st["dog_pos"]
            data.mocap_pos[dog_mid][:] = dp
            q = st.get("dog_quat", [1, 0, 0, 0])
            data.mocap_quat[dog_mid][:] = q
            qpos = st.get("qpos", [])
            if len(qpos) == model.nq:
                data.qpos[:] = qpos
        for person, mid in mids.items():
            pos = (st.get("people_pos") or {}).get(person)
            if pos:
                data.mocap_pos[mid][:] = pos
        mujoco.mj_forward(model, data)
        handle.sync()
        time.sleep(1.0 / 30.0)


if __name__ == "__main__":
    main()
