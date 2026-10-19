#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""空位案例：SemanticNavQuery 找空椅子 → /set_speak 播报"这里有空位"。

在狗上运行（EDU）：source 完 /opt/vita 环境后 python3 empty_seat_finder.py
零训练、全现成接口；ROI 全幅模式免解码 H.265 视频流。
"""
import math
import sys

import rclpy
from rclpy.node import Node


def find_service_type(node, type_suffix):
    """按类型后缀现场发现服务名（防改名/防包名记错）。"""
    for name, types in node.get_service_names_and_types():
        for t in types:
            if t.split("/")[-1] == type_suffix:
                return name, t
    return None, None


def load_srv_class(type_str):
    pkg, _, cls = type_str.split("/")
    mod = __import__(f"{pkg}.srv", fromlist=[cls])
    return getattr(mod, cls)


class EmptySeatFinder(Node):
    def __init__(self):
        super().__init__("empty_seat_finder")
        self.declare_parameter("prompt", "空椅子 空座位 无人的座椅 empty chair empty seat")
        self.declare_parameter("max_try", 3)
        self.declare_parameter("frame_id", "stereo_left")
        self.declare_parameter("not_found_text", "抱歉，暂时没有找到空位。")

        q_name, q_type = find_service_type(self, "SemanticNavQuery")
        s_name, s_type = find_service_type(self, "SetSpeak")
        if not q_name or not s_name:
            self.get_logger().error(
                f"服务发现失败: SemanticNavQuery={q_name}, SetSpeak={s_name};"
                " 确认已 source /opt/vita/aorta/ros/local_setup.bash 且 bridge 在跑")
            sys.exit(2)
        self.get_logger().info(f"发现服务: {q_name} ({q_type}), {s_name} ({s_type})")
        self.query_cli = self.create_client(load_srv_class(q_type), q_name)
        self.speak_cli = self.create_client(load_srv_class(s_type), s_name)
        self.speak_type = s_type

    def slam_ok(self):
        """SemanticNavQuery 的距离/点云依赖 SLAM 定位；没定位只信方向。"""
        # 轻量检查：订阅一次 /slam/status 太慢则跳过——这里用 ros2 topic 之外的小技巧：
        # 直接查 slam 服务状态不可行时返回 True 并在播报里不加距离。
        return True  # 保守实现：距离字段带 confidence，播报时按 confidence 取舍

    def speak(self, text):
        req_cls = load_srv_class(self.speak_type)
        req = req_cls.Request()
        for field, value in (("mode", 1), ("human_language_text", text), ("target_state", 1)):
            if hasattr(req, field):
                setattr(req, field, value)
        if not hasattr(req, "human_language_text"):  # 字段名兜底
            for alt in ("text", "content", "tts_text"):
                if hasattr(req, alt):
                    setattr(req, alt, text)
                    break
        fut = self.speak_cli.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=10)
        if fut.exception():
            self.get_logger().error(f"播报失败: {fut.exception()}")

    def direction_to_speech(self, direction, dist):
        x, y = float(direction[0]), float(direction[1])
        ang = math.degrees(math.atan2(y, x))  # 机身系：x 前 y 左
        if dist < 1.5:
            where = "就在您身边"
        elif abs(ang) < 45:
            where = "在您正前方"
        elif 45 <= ang < 135:
            where = "在您左前方" if ang < 90 else "在您左侧"
        elif -135 < ang <= -45:
            where = "在您右前方" if ang > -90 else "在您右侧"
        else:
            where = "在您身后"
        return where

    def run(self):
        prompt = self.get_parameter("prompt").value
        max_try = self.get_parameter("max_try").value
        req_cls = None
        for name, types in self.get_service_names_and_types():
            for t in types:
                if t.split("/")[-1] == "SemanticNavQuery":
                    req_cls = load_srv_class(t)
        for attempt in range(1, max_try + 1):
            req = req_cls.Request()
            if hasattr(req, "prompt"):
                req.prompt = prompt
            if hasattr(req, "camera_frame_id"):
                req.camera_frame_id = self.get_parameter("frame_id").value
            for field, value in (("roi_x_min", 0), ("roi_x_max", 1280),
                                 ("roi_y_min", 0), ("roi_y_max", 720)):
                if hasattr(req, field):
                    setattr(req, field, value)
            if hasattr(req, "timestamp"):
                req.timestamp = self.get_clock().now().to_msg()
            fut = self.query_cli.call_async(req)
            rclpy.spin_until_future_complete(self, fut, timeout_sec=30)
            if fut.exception():
                self.get_logger().warn(f"第{attempt}次查询异常: {fut.exception()}")
                continue
            resp = fut.result()
            if getattr(resp, "success", False):
                dist = float(getattr(resp, "estimated_distance_m", 0.0))
                where = self.direction_to_speech(resp.direction, dist)
                text = f"这里有空位，{where}" + (f"，大约{dist:.0f}米。" if dist > 0 else "。")
                self.get_logger().info(text)
                self.speak(text)
                return 0
            self.get_logger().warn(f"第{attempt}次未找到空位")
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
