from abc import ABC, abstractmethod
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class RobotError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


class SafeRobotAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["move_to_known_zone", "accompany_simulated", "stop", "wake_reply", "shake_hands", "start_mapping"]
    destination: Literal["living_room", "bedroom", "entrance"] | None = None


class ActionResult(BaseModel):
    completed: bool = True
    detail: str = ""


class RobotStatus(BaseModel):
    backend: str
    model_label: str
    available: bool = False
    stopped: bool = True
    locomotion: bool = False
    position: list[float] = Field(default_factory=lambda: [0., 0., 0.])
    quaternion_wxyz: list[float] = Field(default_factory=lambda: [1., 0., 0., 0.])
    joints: list[float] = Field(default_factory=list)
    actuator_controls: list[float] = Field(default_factory=list)
    simulation_time: float = 0
    error_code: str | None = None
    scene: str = "living_room"
    worker_pid: int | None = None


class RobotAdapter(ABC):
    async def start(self):
        pass

    async def close(self):
        pass

    @abstractmethod
    def get_status(self) -> RobotStatus: ...

    @abstractmethod
    async def execute(self, action: SafeRobotAction, task_id: str) -> ActionResult: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def cancel(self, task_id: str) -> None: ...
