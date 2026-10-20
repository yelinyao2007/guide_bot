#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最后十米空位案例 v2：提前找空位 → 引导 → 最后十米确认 → 播报"这里有空位" → 蹲守位旁。

真机/仿真同构：
- 感知/播报 = function_msgs SemanticNavQuery / SetSpeak（真狗板载服务；仿真由 run_sim.py 提供同型假服务）
- 引导/姿势 = 话题 /sim/nav_goal(PointStamped) 与 /sim/pose(String: stand|crouch)
  仿真侧 run_sim 直接消费；真机侧由 VbotBackend 翻译成 ControlFollowing NAV_TO_POINT / 预置动作(≈lie_down)
流程：找空位 → 播报"找到空位…我带您过去" → 引导到位旁 → 再查一次确认没被抢
      → 被抢则换座位重试一次 → 确认成功播报"这里有空位" + 蹲守
"""
import math
import sys
import time

import rclpy
from rclpy.node import Node
from geometry_msgs.msg import PointStamped, PoseStamped
from std_msgs.msg import String

BESIDE_OFFSET = (0.5, -0.4)
ARRIVE_RADIUS = 0.6
GUIDE_TIMEOUT = 30.0


def discover(node, type_suffix, timeout_sec=15.0):
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        for name, types in node.get_service_names_and_types():
            for t in types:
                if t.split("/")[-1] == type_suffix:
                    return name, t
        rclpy.spin_once(node, timeout_sec=0.25)
    return None, None


def load_cls(type_str):
    pkg, _, cls = type_str.split("/")
    sub = "msg" if "/msg/" in type_str else "srv"
    return getattr(__import__(f"{pkg}.{sub}", fromlist=[cls]), cls)


class EmptySeatFinder(Node):
    def __init__(self):
        super().__init__("empty_seat_finder")
        self.declare_parameter("not_found_text", "抱歉，暂时没有找到空位。")
        q_name, q_type = discover(self, "SemanticNavQuery")
        s_name, s_type = discover(self, "SetSpeak")
        if not q_name or not s_name:
            self.get_logger().error("服务发现失败：确认真机 bridge 或仿真 run_sim.py 在跑")
            sys.exit(2)
        self.q_cls = load_cls(q_type)
        self.s_cls = load_cls(s_type)
        self.query_cli = self.create_client(self.q_cls, q_name)
        self.speak_cli = self.create_client(self.s_cls, s_name)
        self.nav_pub = self.create_publisher(PointStamped, "/sim/nav_goal", 10)
        self.pose_pub = self.create_publisher(String, "/sim/pose", 10)
        self.dog_pos = None
        self.create_subscription(PoseStamped, "/sim/dog_pose", self.on_dog_pose, 10)
        self.get_logger().info(f"finder 上线: {q_name}, {s_name}")

    def on_dog_pose(self, msg):
        self.dog_pos = (msg.pose.position.x, msg.pose.position.y)

    def speak(self, text):
        req = self.s_cls.Request()
        req.mode, req.human_language_text, req.target_state = 1, text, 1
        fut = self.speak_cli.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=10)
        self.get_logger().info(f"🔊 {text}")

    def query(self, tries=3):
        for _ in range(tries):
            req = self.q_cls.Request()
            req.prompt = "空椅子 空座位 无人的座椅 empty chair empty seat"
            if hasattr(req, "camera_frame_id"):
                req.camera_frame_id = "stereo_left"
            for f, v in (("roi_x_min", 0), ("roi_x_max", 1280), ("roi_y_min", 0), ("roi_y_max", 720)):
                if hasattr(req, f):
                    setattr(req, f, v)
            if hasattr(req, "timestamp"):
                req.timestamp = self.get_clock().now().to_msg()
            fut = self.query_cli.call_async(req)
            rclpy.spin_until_future_complete(self, fut, timeout_sec=30)
            if fut.result() is not None and fut.result().success:
                return fut.result()
            rclpy.spin_once(self, timeout_sec=0.5)
        return None

    @staticmethod
    def where_text(direction, dist):
        ang = math.degrees(math.atan2(float(direction.y), float(direction.x)))
        if dist < 1.5:
            return "就在您身边"
        if abs(ang) < 45:
            return "在您正前方"
        if 45 <= ang < 135:
            return "在您左前方" if ang < 90 else "在您左侧"
        if -135 < ang <= -45:
            return "在您右前方" if ang > -90 else "在您右侧"
        return "在您身后"

    def guide_to(self, seat_xy):
        goal = (seat_xy[0] + BESIDE_OFFSET[0], seat_xy[1] + BESIDE_OFFSET[1])
        msg = PointStamped()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.point.x, msg.point.y = goal[0], goal[1]
        deadline = time.time() + GUIDE_TIMEOUT
        while time.time() < deadline:
            self.nav_pub.publish(msg)
            rclpy.spin_once(self, timeout_sec=0.2)
            if self.dog_pos and math.dist(self.dog_pos, goal) < ARRIVE_RADIUS:
                return True
        return bool(self.dog_pos) and math.dist(self.dog_pos, goal) < ARRIVE_RADIUS

    def run(self):
        for attempt in (1, 2):
            resp = self.query()
            if resp is None:
                self.speak(self.get_parameter("not_found_text").value)
                return 1
            seat = resp.message
            dist = float(resp.estimated_distance_m)
            if self.dog_pos is None:
                for _ in range(20):
                    rclpy.spin_once(self, timeout_sec=0.2)
                    if self.dog_pos:
                        break
            base = self.dog_pos or (0.0, 0.0)
            seat_xy = (base[0] + resp.direction.x * dist, base[1] + resp.direction.y * dist)
            if attempt == 1:
                self.speak(f"找到空位，{seat} 座，{self.where_text(resp.direction, dist)}，大约{dist:.0f}米，我带您过去。")
            if not self.guide_to(seat_xy):
                self.speak("抱歉，没能带到座位旁。")
                return 1
            # 最后十米确认：座位还在吗
            check = self.query(tries=1)
            if check is not None and check.message == seat:
                self.speak("这里有空位。")
                p = String()
                p.data = "crouch"
                self.pose_pub.publish(p)
                self.get_logger().info(f"蹲守 {seat} 座旁")
                return 0
            self.speak(f"{seat} 座刚被占用，我换一个找。")
        self.speak(self.get_parameter("not_found_text").value)
        return 1


def main():
    rclpy.init()
    node = EmptySeatFinder()
    rc = node.run()
    node.destroy_node()
    rclpy.shutdown()
    sys.exit(rc)


if __name__ == "__main__":
    main()
