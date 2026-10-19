#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空位案例仿真环境（S1+S2）：MuJoCo 场景 + 假感知服务 + 跟随控制器 + 网页编排 HTTP。

- 场景：椅子 + 人(mocap) + 狗(VbotBaboEDU 注入，底座 mocap 可运动学移动)
- 假感知：function_msgs/srv/SemanticNavQuery（真值几何）+ SetSpeak（日志/可选TTS）
  → 与真狗同类型同字段，empty_seat_finder.py 零改动通吃仿真/真机
- 跟随（S2）：/dog/follow/start 后狗以伴随偏移(-0.6,-0.45)运动学跟踪人 + 程序化摆腿；
  人停狗停。仿的是 ControlFollowing 的**契约**而非官方算法（黑盒）。
- 编排 HTTP :8001（CORS 全开，网页面板 + backend SimVbotAdapter 共用）：
    GET  /state | /speak_log
    POST /person/sit|stand|leave|walk|return
    POST /dog/follow/start|stop | /dog/goto {x,y}
    POST /mapping/start|stop   （人走环线+狗跟随，20s 自动结束）
    POST /speak {text} | /case/run | /reset

用法: python3 run_sim.py [--viewer] [--port 8001] [--speak-cmd 'espeak-ng -v zh "{}"']
前置: bash fetch_assets.sh；ROS 环境已 source（rclpy + function_msgs）
"""
import argparse
import json
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
WALK_LOOP = [np.array([3.5, -2.5, 0.95]), np.array([3.5, 2.5, 0.95]),
             np.array([0.5, 2.5, 0.95]), np.array([0.5, -2.5, 0.95])]
FOLLOW_OFFSET = np.array([-0.6, -0.45, 0.0])
MAPPING_SECONDS = 20.0
FINDER_CANDIDATES = [
    HERE.parent / ".claude/skills/vbot-dog/scripts/empty_seat_finder.py",
    HERE / "empty_seat_finder.py",
]
SPEAK_CMD = None


def compose_scene():
    scene = (HERE / "scene_empty_seat.xml").read_text()
    dog_xml = HERE / "assets/vbot_babo_edu_act.xml"
    if not dog_xml.exists():
        print("⚠️  缺狗资产：先跑 bash fetch_assets.sh（本帧以无狗模式运行）")
        return scene.replace("<!--DOG_INJECT-->", "").replace("<!--DOG_ACTUATORS-->", ""), False
    d = dog_xml.read_text()
    wb = re.search(r"<worldbody>(.*)</worldbody>", d, re.S).group(1)
    # URDF 固定根编译后并入 world → 自包一层 mocap 根，整狗可运动学移动
    wb = f'<body name="dog_root" mocap="true" pos="0 0 0.32">\n{wb}\n</body>'
    act = re.search(r"<actuator>(.*)</actuator>", d, re.S)
    asset = re.search(r"<asset>(.*)</asset>", d, re.S)
    scene = scene.replace("<!--DOG_INJECT-->", wb)
    scene = scene.replace("<!--DOG_ACTUATORS-->", f"<actuator>{act.group(1)}</actuator>" if act else "")
    head = f'<compiler meshdir="{HERE / "assets"}"/>\n  <asset>{asset.group(1) if asset else ""}</asset>\n  <worldbody>'
    scene = scene.replace("<worldbody>", head, 1)
    return scene, True


class Sim:
    def __init__(self, viewer):
        xml, self.dog_loaded = compose_scene()
        self.model = mujoco.MjModel.from_xml_string(xml)
        # S1 规矩：狗网格不参与碰撞（执行器撑体重会数值发散；狗是观察者/运动学演员）
        for i in range(self.model.ngeom):
            if self.model.geom(i).type == mujoco.mjtGeom.mjGEOM_MESH:
                self.model.geom_contype[i] = 0
                self.model.geom_conaffinity[i] = 0
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)
        self.person_mid = self.model.body("person").mocapid[0]
        self.dog_mid = self.model.body("dog_root").mocapid[0] if self.dog_loaded else None
        self.dog_z0 = float(self.data.mocap_pos[self.dog_mid][2]) if self.dog_loaded else 0.4
        self.person_state = "sit"
        self.target = PERSON_POS["sit"].copy()
        self.wp_idx = 0
        self.following = False
        self.mapping = False
        self.mapping_t0 = 0.0
        self.goto_target = None
        self.speaks = []
        self.viewer = viewer
        self.running = True
        self.case_proc = None

    # ---- 物理线程 ----
    def physics_loop(self):
        while self.running:
            t = self.data.time
            # 人：walking 状态沿环线走
            if self.person_state == "walking":
                wp = WALK_LOOP[self.wp_idx]
                if np.linalg.norm(wp - self.data.mocap_pos[self.person_mid]) < 0.15:
                    self.wp_idx = (self.wp_idx + 1) % len(WALK_LOOP)
                    wp = WALK_LOOP[self.wp_idx]
                self.target = wp
            # 映射超时自动收
            if self.mapping and t - self.mapping_t0 > MAPPING_SECONDS:
                self.mapping = False
                self.following = False
                self.person_state = "stand"
                self.target = PERSON_POS["stand"].copy()
            # 人运动学逼近
            p = self.data.mocap_pos[self.person_mid]
            p += np.clip(self.target - p, -0.016, 0.016)
            # 狗：goto 优先，其次跟随
            if self.dog_loaded:
                dp = self.data.mocap_pos[self.dog_mid]
                goal = None
                if self.goto_target is not None:
                    goal = np.array([self.goto_target[0], self.goto_target[1], self.dog_z0])
                    if np.linalg.norm(goal[:2] - dp[:2]) < 0.2:
                        self.goto_target = None
                        goal = None
                elif self.following:
                    goal = p + FOLLOW_OFFSET
                    goal[2] = self.dog_z0
                if goal is not None:
                    step = np.clip(goal - dp, -0.016, 0.016)  # ~0.8 m/s
                    dp[:] = dp + step
                    speed = np.linalg.norm(step) / 0.02
                    yaw = math.atan2(p[1] - dp[1], p[0] - dp[0]) if self.following else yaw_of(dp)
                    c, s = math.cos(yaw / 2), math.sin(yaw / 2)
                    self.data.mocap_quat[self.dog_mid] = [c, 0, 0, s]
                    # 程序化摆腿（演出用，非步态）
                    amp = 0.25 if speed > 0.05 else 0.0
                    for j in range(self.model.nu):
                        self.data.ctrl[j] = amp * math.sin(2 * math.pi * 1.2 * t + (j % 4) * math.pi / 2)
                else:
                    self.data.ctrl[:self.model.nu] = 0.0
            mujoco.mj_step(self.model, self.data)
            time.sleep(0.02)

    def seat_occupied(self):
        return self.person_state == "sit"

    def set_person(self, cmd):
        alias = {"leave": "away", "return": "stand"}
        cmd = alias.get(cmd, cmd)
        if cmd == "walk":
            self.person_state = "walking"
            return True
        if cmd in PERSON_POS:
            self.person_state = cmd
            self.target = PERSON_POS[cmd].copy()
            return True
        return False

    def reset(self):
        self.person_state = "sit"
        self.target = PERSON_POS["sit"].copy()
        self.following = self.mapping = False
        self.goto_target = None
        if self.dog_loaded:
            self.data.mocap_pos[self.dog_mid] = (0, 0, self.dog_z0)
            self.data.mocap_quat[self.dog_mid] = (1, 0, 0, 0)
        self.data.ctrl[:self.model.nu] = 0.0


def yaw_of(pos):
    return 0.0


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
        vec = CHAIR_SEAT - np.array([0, 0, 0.4])
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

    def _body(self):
        try:
            return json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
        except ValueError:
            return {}

    def do_OPTIONS(self):
        self._json({})

    def do_GET(self):
        if self.path == "/state":
            dp = SIM.data.mocap_pos[SIM.dog_mid].tolist() if SIM.dog_loaded else [0, 0, 0]
            self._json({"person_state": SIM.person_state, "seat_occupied": SIM.seat_occupied(),
                        "speaks": SIM.speaks[-20:], "sim_time": float(SIM.data.time),
                        "dog_loaded": SIM.dog_loaded, "dog_pos": dp,
                        "following": SIM.following, "mapping": SIM.mapping,
                        "case_running": SIM.case_proc is not None and SIM.case_proc.poll() is None})
        elif self.path == "/speak_log":
            self._json({"speaks": SIM.speaks})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path.startswith("/person/"):
            ok = SIM.set_person(self.path.split("/")[-1])
            self._json({"ok": ok, "person_state": SIM.person_state})
        elif self.path == "/dog/follow/start":
            SIM.following = True
            self._json({"ok": True})
        elif self.path == "/dog/follow/stop":
            SIM.following = False
            self._json({"ok": True})
        elif self.path == "/dog/goto":
            b = self._body()
            SIM.goto_target = (float(b.get("x", 0)), float(b.get("y", 0)))
            SIM.following = False
            self._json({"ok": True})
        elif self.path == "/mapping/start":
            SIM.mapping, SIM.following = True, True
            SIM.mapping_t0 = SIM.data.time
            SIM.person_state = "walking"
            self._json({"ok": True, "auto_stop_sec": MAPPING_SECONDS})
        elif self.path == "/mapping/stop":
            SIM.mapping = SIM.following = False
            SIM.person_state = "stand"
            SIM.target = PERSON_POS["stand"].copy()
            self._json({"ok": True})
        elif self.path == "/speak":
            text = self._body().get("text", "")
            SIM.speaks.append({"t": time.time(), "text": text})
            if SPEAK_CMD:
                subprocess.Popen(["bash", "-c", SPEAK_CMD.replace("{}", text)])
            self._json({"ok": True})
        elif self.path == "/reset":
            SIM.reset()
            self._json({"ok": True})
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


import math  # noqa: E402  (physics_loop 用到)


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
        mujoco.viewer.launch(SIM.model, SIM.data)
    else:
        try:
            while SIM.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    SIM.running = False


if __name__ == "__main__":
    main()
