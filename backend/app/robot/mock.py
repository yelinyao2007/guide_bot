import asyncio
from .base import ActionResult, RobotAdapter, RobotError, RobotStatus, SafeRobotAction


class MockRobot(RobotAdapter):
    def __init__(self, scenario="normal", duration=2.):
        self.scenario, self.duration = scenario, duration
        self.status = RobotStatus(backend="mock", model_label="MOCK / 非物理仿真", available=True, locomotion=True)
        self.halted = asyncio.Event()
        self.active = None

    def get_status(self):
        return self.status.model_copy(deep=True)

    async def execute(self, action: SafeRobotAction, task_id: str):
        if action.kind == "stop":
            await self.stop()
            return ActionResult()
        if self.halted.is_set():
            raise RobotError("STOP_LATCHED")
        if self.active:
            raise RobotError("ROBOT_BUSY")
        self.active = task_id
        self.status.stopped = False
        start = asyncio.get_running_loop().time()
        try:
            while True:
                if self.halted.is_set():
                    raise RobotError("STOP_LATCHED")
                elapsed = asyncio.get_running_loop().time() - start
                if elapsed > .1 and self.scenario in ("obstacle", "disconnect"):
                    self.status.error_code = "OBSTACLE_BLOCKED" if self.scenario == "obstacle" else "ROBOT_DISCONNECTED"
                    self.status.available = self.scenario != "disconnect"
                    self.halted.set()
                    raise RobotError(self.status.error_code)
                if self.scenario != "timeout" and elapsed >= self.duration:
                    if action.kind == "move_to_known_zone":
                        self.status.position = {"living_room": [1., 0., 0.], "bedroom": [0., 1., 0.], "entrance": [-1., 0., 0.]}[action.destination]
                    return ActionResult(detail="mock simulated action")
                await asyncio.sleep(.02)
        finally:
            self.status.stopped = True
            self.active = None

    async def stop(self):
        self.halted.set()
        self.status.stopped = True

    async def cancel(self, task_id):
        if self.active == task_id:
            await self.stop()

    async def recover(self):
        self.halted.clear()
        self.status.available = True
        self.status.error_code = None
