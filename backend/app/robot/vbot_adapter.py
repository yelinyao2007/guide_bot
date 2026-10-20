"""真机适配器：维他动力 VBOT EDU foot_quadruped（应用 V1.6.x）。

接口一律按 EDU 文档（现场资料 03_维他动力大头狗/docs_interfaces_*.zh-CN.md）：
- 状态订阅 /locomotion/status、/locomotion/body_action_status、/bms_state、/odometry
  （locomotion.zh-CN.md / aorta-ros2.zh-CN.md 表；区分 unknown/transitioning/recovering，不都当空闲）
- 站起 /locomotion/set_run_mode：MODE_MANUAL=0 且 is_traction_user_param=false（locomotion.zh-CN.md）
- 趴下/安全停 /sm/action/lowlevel：SAFE_LAYDOWN_SEQUENCE=4 / SAFE_STOP_SEQUENCE=3
  （不用 FIXED_STAND/FIXED_LAYDOWN 顶替，文档明确禁止）
- 到点/伴随跟随 function_msgs NavigateToTarget / ControlFollowing(+SetFollowSpeed 导盲限速)
- 建图 /slam/set_slam_mode(mode=2 建图)；播报 /set_speak(mode=1 人话)
路由名启动时用 ros2 service/action/topic list -t 现场发现（文档要求发现确切路由，不硬背）；
service 响应只代表接纳，接纳后观察 /locomotion/status 回到静止作为完成证据
（文档：接纳≠完成；无匹配终态报结果未知，不盲重发）。
传输 = ssh 到狗跑 ros2 CLI（ROS 在狗上 LOCALHOST_ONLY=1，跨机只能 ssh/zenoh）。
状态由后台线程按 poll_interval 刷新缓存；get_status 只读缓存，monitor 10Hz 不会刷爆 ssh。
runner 可注入（测试用脚本化假狗，跑真 YAML 解析与请求载荷断言）。
"""
import asyncio
import re
import threading
from uuid import uuid4

from .base import ActionResult, RobotAdapter, RobotError, RobotStatus, SafeRobotAction

POSTURE = {0: "unknown", 1: "standing", 2: "lying", 3: "transitioning"}
MOTION = {0: "idle", 1: "recovering", 2: "emergency"}
BODY_MODE = {0: "PASSIVE", 1: "FIXED_STAND", 2: "FIXED_LAYDOWN", 3: "SAFE_STOP_SEQUENCE",
             4: "SAFE_LAYDOWN_SEQUENCE", 5: "RL", 6: "RL_TROT", 7: "MPC", 8: "STREAM",
             9: "RL_PILOT", 10: "RL_RECOVERY", 11: "LAYDOWN", 12: "EXTERNAL", 13: "OPENTRACK",
             14: "RECOVERY_ACTION", 15: "DAMPER", 100: "RL_ACTION_POLICY1", 101: "RL_ACTION_POLICY2",
             102: "RL_ACTION_POLICY3", 103: "HIMLOCO_ACTION"}

# 用途 -> ROS 类型（路由名现场发现；类型来自 vbot_ros2_msgs 与 EDU 接口表）
SVC = {
    "set_run_mode": "function_msgs/srv/SetRunMode",
    "lowlevel": "software_msgs/srv/LowlevelAction",
    "nav": "function_msgs/srv/NavigateToTarget",
    "follow": "function_msgs/srv/ControlFollowing",
    "speed": "function_msgs/srv/SetFollowSpeed",
    "speak": "function_msgs/srv/SetSpeak",
    "slam": "slam_msgs/srv/SetSlamMode",
}

MODE_MANUAL = 0
SAFE_STOP_SEQUENCE = 3
SAFE_LAYDOWN_SEQUENCE = 4
CF_ACCOMPANY = 8
GUIDE_SPEED = 0.5  # 导盲限速写死，不用网页/指令覆盖


def _field(block, key):
    m = re.search(rf"^[ \t]*{key}:\s*(.+)$", block or "", re.M)
    return m.group(1).strip().strip("'\"") if m else None


def _num(block, key):
    v = _field(block, key)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class VbotEduAdapter(RobotAdapter):
    def __init__(self, cfg=None, runner=None, poll_interval=2.):
        from .. import vbot_config as vc
        self.vc = vc
        self.cfg = dict(cfg or vc.load())
        self._runner = runner or (lambda cmd, timeout=20: vc.ssh_run(self.cfg, cmd, timeout))
        self.poll_interval = poll_interval
        self.idle_timeout = 25.  # 接纳后观察回到静止的上限；测试可压短
        self.routes = {}
        self._lock = threading.Lock()
        self._status = RobotStatus(backend="vbot", model_label="VBOT EDU foot_quadruped 真机",
                                   available=False, error_code="ROBOT_DISCONNECTED")
        self._stop = threading.Event()
        self._thread = None

    # ---------- 传输 ----------
    def _remote(self, cmd, timeout=20):
        pre = (f"source /opt/vita/ros/humble/setup.bash >/dev/null 2>&1; "
               f"export ROS_DOMAIN_ID={self.cfg.get('ros_domain_id', 178)} ROS_LOCALHOST_ONLY=1; ")
        return self._runner(pre + cmd, timeout)

    def _route(self, key):
        name = self.routes.get(SVC[key])
        if not name:
            raise RobotError("ROUTE_MISSING", f"狗上未发现 {SVC[key]}；先跑连接检查确认应用版本")
        return name

    # ---------- 生命周期 ----------
    async def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    async def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(3)
            self._thread = None

    def _loop(self):
        while not self._stop.is_set():
            try:
                self._refresh()
            except RobotError:
                self._set(available=False, error_code="ROBOT_DISCONNECTED")
            except Exception as exc:  # 解析/传输意外都不许杀线程
                self._set(available=False, error_code="ROBOT_DISCONNECTED", detail=str(exc))
            self._stop.wait(self.poll_interval)

    def _discover(self):
        ok, out = self._remote("ros2 service list -t 2>/dev/null; ros2 action list -t 2>/dev/null; "
                               "ros2 topic list -t 2>/dev/null", 40)
        routes = {}
        for line in out.splitlines():
            toks = line.split()
            if len(toks) >= 2 and toks[0].startswith("/"):
                routes[toks[-1].strip("[]")] = toks[0].rstrip(":")
        if not routes:
            raise RobotError("ROBOT_DISCONNECTED", "路由发现为空：狗未连上或 ROS 环境未源")
        self.routes = routes

    def _refresh(self):
        if not self.routes:
            self._discover()
        cmd = ("timeout 4 ros2 topic echo /locomotion/status --no-daemon --once 2>/dev/null; echo ===BODY; "
               "timeout 4 ros2 topic echo /locomotion/body_action_status --no-daemon --once 2>/dev/null; echo ===BMS; "
               "timeout 4 ros2 topic echo /bms_state --no-daemon --once 2>/dev/null; echo ===ODOM; "
               "timeout 4 ros2 topic echo /odometry --no-daemon --once 2>/dev/null")
        ok, out = self._remote(cmd, 45)
        parts = re.split(r"===([A-Z]+)\n?", out)
        sec = {"LOCO": parts[0]}
        sec.update(dict(zip(parts[1::2], parts[2::2])))
        posture = _num(sec["LOCO"], "posture")
        if not ok or posture is None:
            self._set(available=False, error_code="ROBOT_DISCONNECTED")
            return
        posture, motion = int(posture), int(_num(sec["LOCO"], "motion") or 0)
        odom = sec.get("ODOM", "")
        pos = [_num(odom, k) for k in ("x", "y", "z")]
        ori = odom[odom.find("orientation"):] if "orientation" in odom else ""
        quat = [_num(ori, k) for k in ("w", "x", "y", "z")]
        body_mode = _num(sec.get("BODY", ""), "mode")
        with self._lock:
            s = self._status
            s.available, s.error_code = True, None
            s.posture = POSTURE.get(posture, "unknown")
            s.current_action = _field(sec["LOCO"], "current_action") or ""
            s.body_mode = BODY_MODE.get(int(body_mode), "") if body_mode is not None else ""
            s.battery_percent = _num(sec.get("BMS", ""), "soc_percent")
            if all(v is not None for v in pos):
                s.position = pos
            if all(v is not None for v in quat):
                s.quaternion_wxyz = quat
            s.locomotion = motion != 0 or posture == 3
            s.stopped = motion == 0
            s.scene = self.cfg.get("map_name") or "edu-device"

    def get_status(self):
        with self._lock:
            return self._status.model_copy(deep=True)

    def _set(self, **kw):
        with self._lock:
            for key, value in kw.items():
                setattr(self._status, key, value)

    # ---------- 请求 ----------
    async def _call_service(self, key, yaml_req, timeout=25):
        ok, out = await asyncio.to_thread(
            self._remote, f"timeout 15 ros2 service call {self._route(key)} {SVC[key]} '{yaml_req}' 2>&1", timeout)
        return ok, out

    @staticmethod
    def _accepted(ok, out, what):
        if not ok:
            raise RobotError("SERVICE_REJECTED", f"{what}: {out[:200]}")
        m = re.search(r"(?:success|accepted):\s*(true|false)", out)
        if m and m.group(1) == "false":
            raise RobotError("SERVICE_REJECTED", f"{what} 被拒: {out[:200]}")

    async def _wait_idle(self, timeout):
        """接纳≠完成：观察状态回到静止（连续两轮）作为完成证据；超时报未知，不盲重发。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        seen = 0
        while loop.time() < deadline:
            st = self.get_status()
            if st.error_code:
                raise RobotError(st.error_code)
            seen = seen + 1 if (not st.locomotion and st.posture in ("standing", "lying")) else 0
            if seen >= 2:
                return
            await asyncio.sleep(min(self.poll_interval, .5))
        raise RobotError("TASK_TIMEOUT", "未观察到回到静止的终态；结果未知，勿自动重发")

    def _gate_idle(self, st):
        if st.error_code:
            raise RobotError(st.error_code)
        if st.locomotion:
            raise RobotError("ROBOT_BUSY", "狗正在运动/过渡，先停止再下发新动作")
        if st.posture not in ("standing", "lying"):
            raise RobotError("NOT_IDLE", f"姿态未知或过渡中({st.posture})，不发动作")

    @staticmethod
    def _quote(text):
        return text.replace("'", "")

    # ---------- 动作 ----------
    async def execute(self, action: SafeRobotAction, task_id: str):
        st = self.get_status()
        if not st.available:
            raise RobotError(st.error_code or "ROBOT_DISCONNECTED")
        if action.kind == "stop":
            await self.stop()
            return ActionResult(detail="SAFE_STOP_SEQUENCE 已接纳")
        req = f"gb-{task_id[:8]}"
        if action.kind == "move_to_known_zone":
            if not action.destination:
                raise RobotError("INVALID_DESTINATION")
            self._gate_idle(st)
            ok, out = await self._call_service(
                "nav", f"{{target_name: '{action.destination}', distance_threshold: 0.5}}")
            self._accepted(ok, out, f"NavigateToTarget({action.destination})")
            await self._wait_idle(self.idle_timeout)
            return ActionResult(detail=f"到点 {action.destination}；req={req}")
        if action.kind == "accompany":
            self._gate_idle(st)
            ok, out = await self._call_service(
                "follow", f"{{target_state: 1, mode: {CF_ACCOMPANY}, req_id: '{req}', pre_check: false}}")
            self._accepted(ok, out, "ControlFollowing ACCOMPANY")
            await self._call_service("speed", f"{{following_speed: {GUIDE_SPEED}, source: 'guide'}}")
            return ActionResult(detail=f"伴随已启动；req={req}")
        if action.kind == "wake_reply":
            ok, out = await self._call_service(
                "speak", f"{{target_state: 1, mode: 1, req_id: '{req}', pre_check: false, "
                         f"machine_language_name: '', human_language_text: '{self._quote('我在，有什么需要？')}'}}")
            self._accepted(ok, out, "SetSpeak")
            return ActionResult(detail="已播报唤醒回应")
        if action.kind == "shake_hands":
            # EDU 开放接口无握手预设（RCP 预设目录未交付）；诚实降级为播报，不假装执行了动作。
            ok, out = await self._call_service(
                "speak", f"{{target_state: 1, mode: 1, req_id: '{req}', pre_check: false, "
                         f"machine_language_name: '', human_language_text: '握手！握握。'}}")
            self._accepted(ok, out, "SetSpeak")
            return ActionResult(detail="EDU 无开放握手预设，已降级为播报")
        if action.kind == "start_mapping":
            ok, out = await self._call_service(
                "slam", "{mode: 2, request_reloc: false, map_name: '', "
                        "client_id: 'guide_bot', client_req_id: 0}")
            self._accepted(ok, out, "SetSlamMode 建图")
            return ActionResult(detail="建图模式已请求；进度看 /slam/status")
        raise RobotError("UNSUPPORTED_ACTION", action.kind)

    async def stop(self):
        req = f"gb-stop-{uuid4().hex[:6]}"
        ok, out = await self._call_service(
            "lowlevel", f"{{target_state: 1, mode: {SAFE_STOP_SEQUENCE}, req_id: '{req}', "
                        f"pre_check: false, action_path: '', action_params_json: ''}}")
        self._accepted(ok, out, "SAFE_STOP_SEQUENCE")

    async def cancel(self, task_id):
        await self.stop()

    async def recover(self):
        self.routes = {}
        await asyncio.to_thread(self._refresh)

    # 供看板/CLI 显式调用的文档动作（不在 SafeRobotAction 六_kind 内）
    async def stand(self):
        st = self.get_status()
        if st.error_code:
            raise RobotError(st.error_code)
        req = f"gb-stand-{uuid4().hex[:6]}"
        ok, out = await self._call_service(
            "set_run_mode", f"{{target_state: 1, mode: {MODE_MANUAL}, req_id: '{req}', "
                            f"pre_check: false, has_is_traction_user_param: true, is_traction_user_param: false}}")
        self._accepted(ok, out, "SetRunMode MANUAL")
        await self._wait_idle(self.idle_timeout)
        return ActionResult(detail=f"MANUAL 运控已启动；req={req}（模式激活≠物理站起，看姿态）")

    async def lie_down(self):
        req = f"gb-lay-{uuid4().hex[:6]}"
        ok, out = await self._call_service(
            "lowlevel", f"{{target_state: 1, mode: {SAFE_LAYDOWN_SEQUENCE}, req_id: '{req}', "
                        f"pre_check: false, action_path: '', action_params_json: ''}}")
        self._accepted(ok, out, "SAFE_LAYDOWN_SEQUENCE")
        await self._wait_idle(self.idle_timeout)
        return ActionResult(detail=f"安全趴下已接纳；req={req}")
