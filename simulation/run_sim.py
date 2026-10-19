#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空位案例仿真环境（S1）：MuJoCo 场景 + 假感知服务 + 网页编排 HTTP。

- 场景：椅子 + 人(mocap) + 狗(注入 VbotBaboEDU 执行器版)
- 假感知：function_msgs/srv/SemanticNavQuery（真值几何）+ SetSpeak（日志/可选TTS）
  → 与真狗同类型同字段，empty_seat_finder.py 零改动通吃仿真/真机
- 编排 HTTP :8001（CORS 全开，供网页端面板）：
    GET  /state            {person_state, seat_occupied, speaks, sim_time, dog_loaded}
    POST /person/sit|stand|leave|return
    POST /case/run         拉起 empty_seat_finder.py
    GET  /speak_log

用法: python3 run_sim.py [--viewer] [--port 8001] [--speak-cmd 'espeak-ng -v zh "{}"']
前置: bash fetch_assets.sh；ROS 环境已 source（rclpy + function_msgs 可导入）
"""
import argparse
import json
import math
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import mujoco
import numpy as np
import rclpy
from rclpy.node import Node

HERE = Path(__file__).resolve().parent
CHAIR_SEAT = np.array([1.6, 0.9, 0.45])
PERSON_POS = {
    "sit": np.array([1.6, 0.9, 0.85]),
    "stand": np.array([1.95, 1.25, 0.95]),
    "away": np.array([3.5, -2.5, 0.95]),
}
FINDER_CANDIDATES = [
    HERE.parent / ".claude/skills/vbot-dog/scripts/empty_seat_finder.py",
    HERE / "empty_seat_finder.py",
]


def compose_scene():
    scene = (HERE / "scene_empty_seat.xml").read_text()
    dog_xml = HERE / "assets/vbot_babo_edu_act.xml"
    if not dog_xml.exists():
        print("⚠️  缺狗资产：先跑 bash fetch_assets.sh（本帧以无狗模式运行）")
        return scene.replace("<!--DOG_INJECT-->", "").replace("<!--DOG_ACTUATORS-->", ""), False
    d = dog_xml.read_text()
    wb = re.search(r"<worldbody>(.*)</worldbody>", d, re.S).group(1)
    act = re.search(r"<actuator>(.*)</actuator>", d, re.S)
    asset = re.search(r"<asset>(.*)</asset>", d, re.S)
    scene = scene.replace("<!--DOG_INJECT-->", wb)
    scene = scene.replace("<!--DOG_ACTUATORS-->", f"<actuator>{act.group(1)}</actuator>" if act else "")
    # from_xml_string 无基目录 → 显式 meshdir 指到资产目录；网格定义在 asset 段
    head = f'<compiler meshdir="{HERE / "assets"}"/>\n  <asset>{asset.group(1) if asset else ""}</asset>\n  <worldbody>'
    scene = scene.replace("<worldbody>", head, 1)
    return scene, True


class Sim:
    def __init__(self, viewer):
        xml, self.dog_loaded = compose_scene()
        self.model = mujoco.MjModel.from_xml_string(xml)
        # S1：狗是观察者，网格 geom 关闭碰撞（kp40 执行器撑不住体重会数值发散）
        for i in range(self.model.ngeom):
            if self.model.geom(i).type == mujoco.mjtGeom.mjGEOM_MESH:
                self.model.geom_contype[i] = 0
                self.model.geom_conaffinity[i] = 0
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)
        self.mocap_id = self.model.body("person").mocapid[0]
        self.person_state = "sit"
        self.target = PERSON_POS["sit"].copy()
        self.speaks = []          # [{t, text}]
        self.viewer = viewer
        self.running = True
        self.case_proc = None

    # ---- 物理线程 ----
    def physics_loop(self):
        while self.running:
            cur = self.data.mocap_pos[self.mocap_id]
            cur += np.clip(self.target - cur, -0.016, 0.016)  # ~0.8 m/s @50Hz
            mujoco.mj_step(self.model, self.data)
            if self.viewer:
                pass  # viewer 模式由 mujoco.viewer 自带循环接管（见 main）
            time.sleep(0.02)

    def seat_occupied(self):
        return self.person_state == "sit"

    def set_person(self, cmd):
        alias = {"leave": "away", "return": "stand"}
        cmd = alias.get(cmd, cmd)
        if cmd in PERSON_POS:
            self.person_state = cmd
        else:
            return False
        self.target = PERSON_POS[self.person_state].copy()
        return True


SIM = None  # main 里赋值


# ---- ROS 假感知节点 ----
class SimPerception(Node):
    def __init__(self, sim: Sim):
        super().__init__("sim_perception")
        self.sim = sim
        qn, qt = self._find("SemanticNavQuery")
        sn, st = self._find("SetSpeak")
        if not qn or not sn:
            raise SystemExit(f"服务类型未找到: query={qt} speak={st}（source ROS 环境+vbot_ros2_msgs）")
        self.create_service(self._cls(qt), qn, self.on_query)
        self.create_service(self._cls(st), sn, self.on_speak)
        self.get_logger().info(f"假感知上线: {qn}, {sn}")

    def _find(self, suffix):
        for name, types in self.get_service_names_and_types():
            for t in types:
                if t.split("/")[-1] == suffix:
                    return name, t
        # 自己提供时列表里还没有 → 直接按约定名/类型创建
        default = {"SemanticNavQuery": ("/sim/semantic_nav_query", "function_msgs/srv/SemanticNavQuery"),
                   "SetSpeak": ("/sim/set_speak", "function_msgs/srv/SetSpeak")}[suffix]
        return default

    def _cls(self, type_str):
        pkg, _, cls = type_str.split("/")
        return getattr(__import__(f"{pkg}.srv", fromlist=[cls]), cls)

    def on_query(self, req, resp):
        if self.sim.seat_occupied():
            resp.success = False
            resp.message = "seat occupied by person"
            return resp
        vec = CHAIR_SEAT - np.array([0, 0, 0.4])   # 狗基座近似原点
        dist = float(np.linalg.norm(vec))
        resp.success = True
        resp.message = "sim ground truth"
        resp.direction.x = float(vec[0] / dist)
        resp.direction.y = float(vec[1] / dist)
        resp.direction.z = float(vec[2] / dist)
        resp.confidence = 0.9
        resp.estimated_distance_m = dist
        resp.frame_id = "sim"
        return resp

    def on_speak(self, req, resp):
        if req.target_state == 1 and req.mode == 1:
            text = req.human_language_text
            self.sim.speaks.append({"t": time.time(), "text": text})
            self.get_logger().info(f"🔊 {text}")
            if SPEAK_CMD:
                subprocess.Popen(["bash", "-c", SPEAK_CMD.replace("{}", text)])
            resp.success = True
            resp.message = "spoken(sim)"
        else:
            resp.success = True
            resp.message = "ack"
        return resp


# ---- 编排 HTTP ----
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._json({})

    def do_GET(self):
        if self.path == "/state":
            self._json({"person_state": SIM.person_state,
                        "seat_occupied": SIM.seat_occupied(),
                        "speaks": SIM.speaks[-20:],
                        "sim_time": float(SIM.data.time),
                        "dog_loaded": SIM.dog_loaded,
                        "case_running": SIM.case_proc is not None and SIM.case_proc.poll() is None})
        elif self.path == "/speak_log":
            self._json({"speaks": SIM.speaks})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.startswith("/person/"):
            ok = SIM.set_person(self.path.split("/")[-1])
            self._json({"ok": ok, "person_state": SIM.person_state})
        elif self.path == "/case/run":
            finder = next((p for p in FINDER_CANDIDATES if p.exists()), None)
            if not finder:
                self._json({"error": "finder script not found"}, 404)
                return
            env = "source /opt/ros/humble/setup.bash; source ~/ws/install/setup.bash 2>/dev/null; "
            SIM.case_proc = subprocess.Popen(["bash", "-c", env + f"python3 {finder}"])
            self._json({"ok": True, "pid": SIM.case_proc.pid})
        else:
            self._json({"error": "not found"}, 404)


SPEAK_CMD = None


def main():
    global SIM, SPEAK_CMD
    ap = argparse.ArgumentParser()
    ap.add_argument("--viewer", action="store_true")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--speak-cmd", default=None)
    args = ap.parse_args()
    SPEAK_CMD = args.speak_cmd

    rclpy.init()
    SIM = Sim(args.viewer)
    node = SimPerception(SIM)

    threading.Thread(target=SIM.physics_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"🕹  编排 HTTP: http://<host>:{args.port}/state")

    spin_t = threading.Thread(target=rclpy.spin, args=(node,), daemon=True)
    spin_t.start()
    if args.viewer:
        import mujoco.viewer
        mujoco.viewer.launch(SIM.model, SIM.data)  # 阻塞，关窗即退出
    else:
        try:
            while SIM.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    SIM.running = False


if __name__ == "__main__":
    main()
