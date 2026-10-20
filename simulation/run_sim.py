#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最后十米空位案例仿真（S2.5）：MuJoCo 场景 + 假感知 + 狗状态机 + 编排 HTTP。

场景：3 座位（B 有占座包）+ 主人 + 2 路人（mocap 运动学）+ VbotBaboEDU（mocap 底座+14 执行器）
假感知：function_msgs SemanticNavQuery（真值：最近空座位，message=座位名）+ SetSpeak
狗状态机：idle 待命 / following 跟随 / guiding 引导 / crouch_watch 蹲守空位
ROS 桥接话题（真机侧由 VbotBackend 翻译为 ControlFollowing/预置动作）：
  sub /sim/nav_goal (geometry_msgs/PointStamped) → 狗运动学引导
  sub /sim/pose     (std_msgs/String: stand|crouch) → 姿势（crouch≈官方 lie_down 语义）
  pub /sim/dog_pose (geometry_msgs/PoseStamped) 5Hz
编排 HTTP :8001：/state /speak_log /person/* /people/<i>/sit|stand|leave
                 /dog/follow/start|stop /dog/goto /dog/pose/<name> /mapping/* /speak /case/run /reset
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
from geometry_msgs.msg import PointStamped, PoseStamped
from std_msgs.msg import String

HERE = Path(__file__).resolve().parent
CHAIRS = {
    "A": {"pos": np.array([1.6, 0.9, 0.45]), "object": False},
    "B": {"pos": np.array([2.7, 1.8, 0.45]), "object": True},   # 占座包
    "C": {"pos": np.array([0.7, 2.1, 0.45]), "object": False},
}
PERSON_POS = {
    "sit_A": np.array([1.6, 0.9, 0.85]), "sit_B": np.array([2.7, 1.8, 0.85]), "sit_C": np.array([0.7, 2.1, 0.85]),
    "stand": np.array([1.95, 1.25, 0.95]), "away": np.array([3.8, -2.8, 0.95]),
}
WALK_LOOP = [np.array([3.8, -2.8, 0.95]), np.array([3.8, 2.8, 0.95]),
             np.array([0.4, 2.8, 0.95]), np.array([0.4, -2.8, 0.95])]
FOLLOW_OFFSET = np.array([-0.6, -0.45, 0.0])
MAPPING_SECONDS = 20.0
CROUCH_DELTA = {"hip": 0.0, "thigh": 0.6, "calf": -0.7, "head_pitch": 0.5, "head_yaw": 0.0}
FINDER_CANDIDATES = [
    HERE.parent / ".claude/skills/vbot-dog/scripts/empty_seat_finder.py",
    HERE / "empty_seat_finder.py",
]
SPEAK_CMD = None


def png_encode(rgb):
    import struct
    import zlib
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


from scene_tools import compose_scene


class Person:
    def __init__(self, body, mid, home):
        self.body, self.mid, self.home = body, mid, home
        self.state = "stand"
        self.target = home.copy()
        self.wp_idx = 0


class Sim:
    def __init__(self, viewer):
        xml, self.dog_loaded = compose_scene()
        self.model = mujoco.MjModel.from_xml_string(xml)
        # 狗自带 URDF 碰撞盒自撞爆炸(NaN)→ 全场景关碰撞：狗是观察者/运动学演员，不需要接触
        for i in range(self.model.ngeom):
            self.model.geom_contype[i] = 0
            self.model.geom_conaffinity[i] = 0
        # URDF 头部关节阻尼为 0 → 每步 NaN → MuJoCo 清 ctrl → 执行器全死。补阻尼救活
        self.model.dof_damping[-2:] = 2.0
        self.model.dof_damping[:-2] += 0.3
        self.data = mujoco.MjData(self.model)
        mujoco.mj_forward(self.model, self.data)
        self.owner = Person("person", self.model.body("person").mocapid[0], PERSON_POS["stand"].copy())
        self.others = [Person(n, self.model.body(n).mocapid[0], np.array(p)) for n, p in
                       (("person2", [2.9, 2.2, 0.95]), ("person3", [3.4, 0.4, 0.95]))]
        self.dog_mid = self.model.body("dog_root").mocapid[0] if self.dog_loaded else None
        self.dog_z0 = 0.32
        self.joint_ctrl = {}
        self.qpos0 = {}
        if self.dog_loaded:
            for i in range(self.model.nu):
                jname = self.model.joint(self.model.actuator_trnid[i][0]).name
                self.joint_ctrl[jname] = i
            # URDF 自然站姿 = 姿势基准（ctrl 目标 = qpos0 + 增量）
            for jname in self.joint_ctrl:
                self.qpos0[jname] = float(self.data.qpos[self.model.joint(jname).qposadr[0]])
        self.ppos = {}   # 自有位置状态（mj_step 会重置 data.mocap_pos，累积放这里）
        self.dog_quat = np.array([1.0, 0.0, 0.0, 0.0])
        self.pose = "stand"
        self.following = False
        self.mapping = False
        self.mapping_t0 = 0.0
        self.goto_target = None
        self.dog_state = "idle"
        self.speaks = []
        self.viewer = viewer
        self.running = True
        self.case_proc = None
        self.viewer_proc = None
        # 画面交给 MuJoCo viewer 窗口（--viewer）；关节/参数即 URDF 真值

    # ---- 座位占用 ----
    def seat_by(self, chair):
        if CHAIRS[chair]["object"]:
            return "占座物"
        if self.owner.state == f"sit_{chair}":
            return "主人"
        for idx, p in enumerate(self.others, 1):
            if p.state == f"sit_{chair}":
                return f"路人{idx}"
        return ""

    def seats(self):
        return [{"id": c, "occupied": bool(self.seat_by(c)), "by": self.seat_by(c)} for c in CHAIRS]

    def nearest_free(self, dog_pos):
        best, bd = None, 1e9
        for c, info in CHAIRS.items():
            if self.seat_by(c):
                continue
            d = float(np.linalg.norm(info["pos"] - dog_pos))
            if d < bd:
                best, bd = c, d
        return best, bd

    # ---- 物理线程：位置逻辑墙钟驱动（dt 缩放速度），mj_step 尽力跑 + NaN 守护 ----
    def physics_loop(self):
        last = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            dt = min(0.1, max(1e-4, now - last))
            last = now
            # 先 step：此版本 mj_step 会以 C 侧默认值重置 data.mocap_pos，
            # 之后的 mocap 写入才能存活到本帧渲染/查询并被下一 step 消费
            t0 = time.perf_counter()
            try:
                mujoco.mj_step(self.model, self.data)
                if not (np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all()):
                    self.data.qpos[:] = self.model.qpos0
                    self.data.qvel[:] = 0.0
                    self.data.ctrl[:self.model.nu] = 0.0
                    mujoco.mj_forward(self.model, self.data)
            except Exception:
                pass
            self.step_ms = 0.9 * getattr(self, "step_ms", 0.0) + 0.1 * (time.perf_counter() - t0) * 1000
            self.loop_hz = 0.9 * getattr(self, "loop_hz", 50.0) + 0.1 * (1.0 / dt)
            if self.mapping and now - self.mapping_t0 > MAPPING_SECONDS:
                self.mapping = self.following = False
                self.owner.state = "stand"
                self.owner.target = PERSON_POS["stand"].copy()
            vmax = 0.8 * dt
            for p in [self.owner] + self.others:
                if p.state == "walking":
                    wp = WALK_LOOP[p.wp_idx]
                    if np.linalg.norm(wp - self.ppos[p.mid]) < 0.15:
                        p.wp_idx = (p.wp_idx + 1) % len(WALK_LOOP)
                        wp = WALK_LOOP[p.wp_idx]
                    p.target = wp
                if p.state == "leaving" and np.linalg.norm(PERSON_POS["away"] - self.ppos[p.mid]) < 0.2:
                    p.state = "away"
                self.ppos[p.mid] += np.clip(p.target - self.ppos[p.mid], -vmax, vmax)
            if self.dog_loaded:
                dp = self.ppos[self.dog_mid]
                op = self.ppos[self.owner.mid]
                goal = None
                if self.goto_target is not None:
                    self.dog_state = "guiding"
                    goal = np.array([self.goto_target[0], self.goto_target[1], self.dog_z0])
                    if np.linalg.norm(goal[:2] - dp[:2]) < 0.25:
                        self.goto_target = None
                        self.dog_state = "idle"
                        goal = None
                elif self.following:
                    self.dog_state = "following"
                    goal = op + FOLLOW_OFFSET
                    goal[2] = self.dog_z0
                elif self.pose == "crouch":
                    self.dog_state = "crouch_watch"
                else:
                    self.dog_state = "idle"
                if goal is not None:
                    step = np.clip(goal - dp, -vmax, vmax)
                    dp[:] = dp + step
                    speed = np.linalg.norm(step) / dt
                    ref = op if self.following else goal
                    yaw = math.atan2(ref[1] - dp[1], ref[0] - dp[0])
                    c, s = math.cos(yaw / 2), math.sin(yaw / 2)
                    self.dog_quat = np.array([c, 0.0, 0.0, s])
                    amp = 0.25 if speed > 0.05 else 0.0
                    for j in range(self.model.nu):
                        self.data.ctrl[j] = amp * math.sin(2 * math.pi * 1.2 * now + (j % 4) * math.pi / 2)
                else:
                    ramp = 1.5 * dt
                    for jname, idx in self.joint_ctrl.items():
                        target = self.qpos0.get(jname, 0.0)
                        if self.pose == "crouch":
                            target += next((v for k, v in CROUCH_DELTA.items() if k in jname), 0.0)
                        self.data.ctrl[idx] += float(np.clip(target - self.data.ctrl[idx], -ramp, ramp))
            # 自有状态 → data（step 之后写，本帧生效）
            for p in [self.owner] + self.others:
                self.data.mocap_pos[p.mid][:] = self.ppos[p.mid]
            if self.dog_loaded:
                self.data.mocap_pos[self.dog_mid][:] = self.ppos[self.dog_mid]
                self.data.mocap_quat[self.dog_mid][:] = self.dog_quat
            t0 = time.perf_counter()
            try:
                mujoco.mj_step(self.model, self.data)
                if not (np.isfinite(self.data.qpos).all() and np.isfinite(self.data.qvel).all()):
                    # NaN 会腐蚀 mocap 写入 → 整状态复位，不只是清 ctrl
                    self.data.qpos[:] = self.model.qpos0
                    self.data.qvel[:] = 0.0
                    self.data.ctrl[:self.model.nu] = 0.0
                    mujoco.mj_forward(self.model, self.data)
            except Exception:
                pass
            step_ms = (time.perf_counter() - t0) * 1000
            self.step_ms = 0.9 * getattr(self, "step_ms", 0.0) + 0.1 * step_ms
            self.loop_hz = 0.9 * getattr(self, "loop_hz", 50.0) + 0.1 * (1.0 / dt)
            time.sleep(max(0.0, 0.02 - (time.perf_counter() - now)))

    def set_owner(self, cmd):
        alias = {"leave": "away", "return": "stand", "walk": "walking"}
        cmd = alias.get(cmd, cmd)
        if cmd == "sit":
            free = [c for c in CHAIRS if not self.seat_by(c)]
            if not free:
                return False
            cmd = f"sit_{free[0]}"
        if cmd.startswith("sit_") and cmd[4:] in CHAIRS:
            self.owner.state = cmd
            self.owner.target = PERSON_POS[cmd].copy()
            return True
        if cmd in ("stand", "walking", "away"):
            self.owner.state = cmd
            self.owner.target = PERSON_POS["stand" if cmd != "away" else "away"].copy()
            return True
        return False

    def set_other(self, idx, cmd):
        p = self.others[idx - 1]
        if cmd == "sit":
            free = [c for c in CHAIRS if not self.seat_by(c)]
            if not free:
                return False
            p.state = f"sit_{free[0]}"
            p.target = PERSON_POS[p.state].copy()
            return True
        if cmd == "stand":
            p.state = "stand"
            p.target = p.home.copy()
            return True
        if cmd in ("leave", "leaving"):
            p.state = "leaving"
            return True
        return False

    def reset(self):
        self.owner.state, self.owner.target = "stand", PERSON_POS["stand"].copy()
        homes = (np.array([2.9, 2.2, 0.95]), np.array([3.4, 0.4, 0.95]))
        for p, home in zip(self.others, homes):
            p.state, p.target, p.home = "stand", home.copy(), home.copy()
        self.others[0].state, self.others[0].target = "sit_C", PERSON_POS["sit_C"].copy()  # 路人1 初始坐 C
        self.following = self.mapping = False
        self.goto_target = None
        self.pose = "stand"
        self.ppos = {self.owner.mid: PERSON_POS["stand"].copy()}
        for p in self.others:
            self.ppos[p.mid] = p.target.copy()
        if self.dog_loaded:
            self.ppos[self.dog_mid] = np.array([0.0, 0.0, self.dog_z0])
            self.dog_quat = np.array([1.0, 0.0, 0.0, 0.0])
            for mid, pos in self.ppos.items():
                self.data.mocap_pos[mid][:] = pos
            self.data.mocap_quat[self.dog_mid][:] = self.dog_quat
        self.data.ctrl[:self.model.nu] = 0.0


class SimPerception(Node):
    def __init__(self, sim: Sim):
        super().__init__("sim_perception")
        self.sim = sim
        qn, qt = self._find("SemanticNavQuery")
        sn, st = self._find("SetSpeak")
        if not qn or not sn:
            raise SystemExit("服务类型未找到（source ROS 环境+vbot_ros2_msgs）")
        self.create_service(self._cls(qt), qn, self.on_query)
        self.create_service(self._cls(st), sn, self.on_speak)
        self.create_subscription(PointStamped, "/sim/nav_goal", self.on_nav_goal, 10)
        self.create_subscription(String, "/sim/pose", self.on_pose, 10)
        self.pose_pub = self.create_publisher(PoseStamped, "/sim/dog_pose", 10)
        self.create_timer(0.2, self.pub_dog_pose)
        self.get_logger().info(f"假感知上线: {qn}, {sn}")

    def _find(self, suffix):
        for name, types in self.get_service_names_and_types():
            for t in types:
                if t.split("/")[-1] == suffix:
                    return name, t
        return {"SemanticNavQuery": ("/sim/semantic_nav_query", "function_msgs/srv/SemanticNavQuery"),
                "SetSpeak": ("/sim/set_speak", "function_msgs/srv/SetSpeak")}[suffix]

    def _cls(self, type_str):
        pkg, _, cls = type_str.split("/")
        return getattr(__import__(f"{pkg}.srv", fromlist=[cls]), cls)

    def on_nav_goal(self, msg):
        self.sim.goto_target = (msg.point.x, msg.point.y)
        self.sim.following = False

    def on_pose(self, msg):
        if msg.data in ("stand", "crouch"):
            self.sim.pose = msg.data

    def pub_dog_pose(self):
        if not self.sim.dog_loaded:
            return
        msg = PoseStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = "sim_world"
        dp = self.sim.ppos[self.sim.dog_mid]
        msg.pose.position.x, msg.pose.position.y, msg.pose.position.z = float(dp[0]), float(dp[1]), float(dp[2])
        q = self.sim.dog_quat
        msg.pose.orientation.w, msg.pose.orientation.x = float(q[0]), float(q[1])
        msg.pose.orientation.y, msg.pose.orientation.z = float(q[2]), float(q[3])
        self.pose_pub.publish(msg)

    def on_query(self, req, resp):
        dog = self.sim.ppos[self.sim.dog_mid] if self.sim.dog_loaded else np.zeros(3)
        chair, _ = self.sim.nearest_free(dog)
        if not chair:
            resp.success = False
            resp.message = "no free seat"
            return resp
        vec = CHAIRS[chair]["pos"] - dog
        dist = float(np.linalg.norm(vec))
        resp.success = True
        resp.message = chair
        resp.direction.x, resp.direction.y, resp.direction.z = (float(v / dist) for v in vec)
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
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n > 0 else b"{}"
            return json.loads(raw or b"{}")
        except (ValueError, TypeError):
            return {}

    def do_OPTIONS(self):
        self._json({})

    def do_GET(self):
        if self.path == "/state":
            dp = SIM.ppos[SIM.dog_mid].tolist() if SIM.dog_loaded else [0, 0, 0]
            self._json({"person_state": SIM.owner.state, "seat_occupied": bool(SIM.seat_by("A")),
                        "seats": SIM.seats(), "dog_state": SIM.dog_state, "dog_pos": dp,
                        "people": [{"i": i, "state": p.state} for i, p in enumerate(SIM.others, 1)],
                        "speaks": SIM.speaks[-20:], "sim_time": float(SIM.data.time),
                        "dog_loaded": SIM.dog_loaded, "following": SIM.following, "mapping": SIM.mapping,
                        "goto_target": list(SIM.goto_target) if SIM.goto_target else None,
                        "joints": [round(float(v), 3) for v in SIM.data.qpos[:6]] if SIM.dog_loaded else [],
                        "qpos": [float(v) for v in SIM.data.qpos[:SIM.model.nq]] if SIM.dog_loaded else [],
                        "dog_quat": [float(v) for v in SIM.dog_quat],
                        "people_pos": {p.body: [float(v) for v in SIM.ppos[p.mid]] for p in [SIM.owner] + SIM.others},
                        "viewer_running": SIM.viewer_proc is not None and SIM.viewer_proc.poll() is None,
                        "loop_hz": round(getattr(SIM, "loop_hz", 0), 1),
                        "step_ms": round(getattr(SIM, "step_ms", 0), 2),
                        "case_running": SIM.case_proc is not None and SIM.case_proc.poll() is None})
        elif self.path == "/speak_log":
            self._json({"speaks": SIM.speaks})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        if parts[0] == "person" and len(parts) == 2:
            self._json({"ok": SIM.set_owner(parts[1])})
        elif parts[0] == "people" and len(parts) == 3:
            self._json({"ok": SIM.set_other(int(parts[1]), parts[2])})
        elif self.path == "/dog/follow/start":
            SIM.following, SIM.pose = True, "stand"
            self._json({"ok": True})
        elif self.path == "/dog/follow/stop":
            SIM.following = False
            self._json({"ok": True})
        elif self.path == "/dog/goto":
            b = self._body()
            SIM.goto_target = (float(b.get("x", 0)), float(b.get("y", 0)))
            SIM.following = False
            self._json({"ok": True})
        elif len(parts) == 3 and parts[0] == "dog" and parts[1] == "pose":
            SIM.pose = parts[2]
            self._json({"ok": True, "pose": SIM.pose})
        elif self.path == "/mapping/start":
            SIM.mapping, SIM.following = True, True
            SIM.mapping_t0 = time.perf_counter()
            SIM.owner.state = "walking"
            self._json({"ok": True, "auto_stop_sec": MAPPING_SECONDS})
        elif self.path == "/mapping/stop":
            SIM.mapping = SIM.following = False
            SIM.owner.state = "stand"
            SIM.owner.target = PERSON_POS["stand"].copy()
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
        elif self.path == "/viewer/open":
            if SIM.viewer_proc is not None and SIM.viewer_proc.poll() is None:
                self._json({"ok": True, "note": "already running"})
                return
            env = dict(__import__("os").environ, DISPLAY=":0")
            SIM.viewer_proc = subprocess.Popen(
                ["python3", str(HERE / "dog_viewer.py")], cwd=str(HERE), env=env,
                stdout=open("/tmp/gb_viewer.log", "a"), stderr=subprocess.STDOUT)
            self._json({"ok": True, "pid": SIM.viewer_proc.pid})
        elif self.path == "/viewer/stop":
            if SIM.viewer_proc is not None and SIM.viewer_proc.poll() is None:
                SIM.viewer_proc.terminate()
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
    SIM.reset()
    node = SimPerception(SIM)

    threading.Thread(target=SIM.physics_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"🕹  编排 HTTP: http://<host>:{args.port}/state")

    threading.Thread(target=rclpy.spin, args=(node,), daemon=True).start()
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
