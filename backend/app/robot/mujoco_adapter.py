import asyncio
import multiprocessing as mp
import queue
import time
from pathlib import Path
from .base import ActionResult, RobotAdapter, RobotError, RobotStatus
from .worker import ProcessFlag, run_simulation

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "assets/menagerie/unitree_go2/scene.xml"


class MujocoRobotAdapter(RobotAdapter):
    def __init__(self, model_path=DEFAULT_MODEL, scene="living_room", viewer=False,
                 start_timeout=15., heartbeat_timeout=2., duration=2.):
        if scene not in ("living_room", "help", "obstacle"):
            raise ValueError("Unknown simulation scene")
        self.model_path, self.scene, self.viewer = str(model_path), scene, viewer
        self.start_timeout, self.heartbeat_timeout, self.duration = start_timeout, heartbeat_timeout, duration
        self.ctx = mp.get_context("spawn")
        self.process = None
        self.output = None
        self.active = None
        self.heartbeat = 0.
        self.fault = None
        self.status = RobotStatus(backend="mujoco", model_label="Go2 / Vbot 仿真替代模型", scene=scene)

    async def start(self):
        if self.process is not None:
            raise RobotError("ALREADY_STARTED")
        self.output = self.ctx.Queue(maxsize=4)
        self.stop_event, self.shutdown = ProcessFlag(self.ctx), ProcessFlag(self.ctx)
        self.status.available = False
        self.status.error_code = None
        self.heartbeat = 0
        self.fault = None
        self.process = self.ctx.Process(target=run_simulation,
            args=(self.model_path, self.scene, self.output, self.stop_event, self.shutdown, self.viewer), daemon=True)
        self.process.start()
        deadline = time.monotonic() + self.start_timeout
        try:
            while time.monotonic() < deadline:
                status = self.get_status()
                if status.available:
                    return
                if status.error_code:
                    raise RobotError(status.error_code)
                await asyncio.sleep(.02)
            raise RobotError("SIM_START_TIMEOUT")
        except BaseException:
            await self.close()
            raise

    def get_status(self):
        if self.output:
            while True:
                try:
                    message = self.output.get_nowait()
                except queue.Empty:
                    break
                if message.pop("kind") == "error":
                    self.fault = message["error_code"]
                    self.status.error_code = self.fault
                    self.status.available = False
                else:
                    self.heartbeat = message.pop("heartbeat")
                    self.status = RobotStatus(**message)
        if self.process and not self.process.is_alive():
            self.fault = self.fault or "ROBOT_DISCONNECTED"
        elif self.heartbeat and time.monotonic() - self.heartbeat > self.heartbeat_timeout:
            self.fault = "SIM_HEARTBEAT_TIMEOUT"
            self.stop_event.set()
        if self.fault:
            self.status.available = False
            self.status.error_code = self.fault
        if self.process and self.stop_event.is_set():
            self.status.stopped = True
        return self.status.model_copy(deep=True)

    async def execute(self, action, task_id):
        if action.kind == "stop":
            await self.stop()
            return ActionResult()
        state = self.get_status()
        if not state.available or state.error_code:
            raise RobotError(state.error_code or "ROBOT_DISCONNECTED")
        if self.stop_event.is_set():
            raise RobotError("STOP_LATCHED")
        if action.kind == "move_to_known_zone":
            raise RobotError("GAIT_UNAVAILABLE", "未验证步态；仅支持原地陪伴")
        if action.kind == "start_mapping":
            raise RobotError("GAIT_UNAVAILABLE", "建图需行走；Go2 替身无验证步态")
        if self.active:
            raise RobotError("ROBOT_BUSY")
        self.active = task_id
        try:
            deadline = time.monotonic() + self.duration
            while time.monotonic() < deadline:
                state = self.get_status()
                if state.error_code or not state.available:
                    raise RobotError(state.error_code or "ROBOT_DISCONNECTED")
                if self.stop_event.is_set():
                    raise RobotError("STOP_LATCHED")
                await asyncio.sleep(.02)
            return ActionResult(detail="原地陪伴完成；未执行行走")
        finally:
            self.active = None

    async def stop(self):
        if self.process:
            self.stop_event.set()  # Out of band; no command queue/LLM dependency.
        self.status.stopped = True

    async def cancel(self, task_id):
        if self.active == task_id:
            await self.stop()

    async def close(self):
        if self.process:
            self.stop_event.set()
            self.shutdown.set()
            await asyncio.to_thread(self.process.join, 2.)
            if self.process.is_alive():
                self.process.terminate()
                await asyncio.to_thread(self.process.join, 2.)
            self.process.close()
            self.process = None
        if self.output:
            self.output.close()
            self.output = None
        self.status.available = False

    async def recover(self):
        await self.close()
        await self.start()
