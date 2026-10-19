import json
import os
import urllib.error
import urllib.request

from .base import ActionResult, RobotAdapter, RobotError, RobotStatus, SafeRobotAction

ZONES = {"living_room": (2.2, 1.8), "bedroom": (0.6, 2.6), "entrance": (3.4, -2.4)}


class SimVbotAdapter(RobotAdapter):
    """把任务框架接到 simulation/run_sim.py 的 Vbot 空位仿真（HTTP :8001）。

    与真机 VbotBackend 同构：这里调仿真 HTTP，真机版调 Zenoh/ROS；
    上层 Service/状态机/看板零改动。
    """

    def __init__(self, url=None):
        self.url = (url or os.getenv("SIM_URL", "http://127.0.0.1:8001")).rstrip("/")
        self.status = RobotStatus(backend="simvbot", model_label="VbotBaboEDU 空位仿真",
                                  scene="empty_seat", locomotion=True)

    def _get(self, path):
        with urllib.request.urlopen(self.url + path, timeout=2) as resp:
            return json.load(resp)

    def _post(self, path, payload=None):
        req = urllib.request.Request(self.url + path, data=json.dumps(payload or {}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return json.load(resp)

    async def start(self):
        # 启动宽限：仿真进程可能比 backend 慢几秒，避免开机即锁急停
        import asyncio
        for _ in range(30):
            if self.get_status().available:
                return
            await asyncio.sleep(1)
        raise RobotError("ROBOT_DISCONNECTED", "仿真 :8001 启动超时")

    def get_status(self):
        s = self.status.model_copy(deep=True)
        try:
            st = self._get("/state")
            s.available, s.error_code = True, None
            s.stopped = not st.get("following", False)
            s.position = list(st.get("dog_pos", [0., 0., 0.]))
            s.simulation_time = float(st.get("sim_time", 0.))
        except (urllib.error.URLError, OSError, ValueError):
            s.available, s.error_code = False, "ROBOT_DISCONNECTED"
        return s

    async def execute(self, action: SafeRobotAction, task_id: str):
        if action.kind == "stop":
            await self.stop()
            return ActionResult()
        state = self.get_status()
        if not state.available:
            raise RobotError("ROBOT_DISCONNECTED", "仿真 :8001 未响应")
        if action.kind == "wake_reply":
            self._post("/speak", {"text": "我在，请说指令。"})
        elif action.kind == "shake_hands":
            self._post("/speak", {"text": "握手！握握。"})
        elif action.kind == "accompany_simulated":
            self._post("/person/walk")
            self._post("/dog/follow/start")
        elif action.kind == "start_mapping":
            self._post("/mapping/start")
        elif action.kind == "move_to_known_zone":
            x, y = ZONES[action.destination]
            self._post("/dog/goto", {"x": x, "y": y})
        return ActionResult(detail=f"simvbot:{action.kind}")

    async def stop(self):
        for path in ("/dog/follow/stop", "/mapping/stop"):
            try:
                self._post(path)
            except (urllib.error.URLError, OSError):
                pass
        self.status.stopped = True

    async def cancel(self, task_id):
        await self.stop()

    async def recover(self):
        try:
            self._post("/reset")
        except (urllib.error.URLError, OSError):
            raise RobotError("ROBOT_DISCONNECTED", "仿真 :8001 未响应")
