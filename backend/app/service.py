import asyncio
import inspect
from contextlib import suppress
from datetime import datetime, timezone
from .agent import dify_resolve, is_stop, resolve
from .domain import Intent, Task, TERMINAL
from .robot.base import RobotError, SafeRobotAction


class Service:
    def __init__(self, robot, store, timeout=30., resolver=dify_resolve):
        self.robot, self.store, self.timeout, self.resolver = robot, store, timeout, resolver
        self.queue = asyncio.Queue()
        self.halted = False
        self.background = []
        self.current = None
        self.recovering = False

    async def start(self):
        for task in self.store.tasks():
            if task.status not in TERMINAL:
                target = "cancelled" if task.status == "awaiting_confirmation" else "failed"
                self.store.transition(task.id, target, "SERVER_RESTART")
        try:
            await self.robot.start()
        except RobotError as exc:
            self.halted = True
            self.store.emit("robot.exception", {"code": exc.code})
        self.background = [asyncio.create_task(self.run()), asyncio.create_task(self.monitor())]

    async def close(self):
        await self.emergency_stop()
        for future in self.background:
            future.cancel()
        for future in self.background:
            with suppress(asyncio.CancelledError):
                await future
        await self.robot.close()
        self.store.engine.dispose()

    async def command(self, text):
        if is_stop(text):
            await self.emergency_stop()
            return {"intent": Intent(intent="stop").model_dump(), "task": None}
        if text.strip() == "确认联系家人":
            pending = [t for t in self.store.tasks() if t.status == "awaiting_confirmation"]
            if len(pending) != 1:
                raise RobotError("CONFIRMATION_AMBIGUOUS")
            task = self.confirm(pending[0].id)
            return {"intent": task.intent.model_dump(), "task": task.model_dump()}
        intent = self.resolver(text)
        if inspect.isawaitable(intent):
            intent = await intent
        # find_seat/nearby_fun/record_field_save: 识别但暂不执行（P2 链路未实现），只透传显示
        if intent.intent in ("unknown", "status", "passthrough", "find_seat", "nearby_fun", "record_field_save"):
            return {"intent": intent.model_dump(), "task": None, "robot": self.robot.get_status().model_dump()}
        if self.halted or self.recovering:
            raise RobotError("STOP_LATCHED")
        task = self.store.add(Task(intent=intent))
        task = self.store.transition(task.id, "awaiting_confirmation" if intent.intent == "request_help" else "queued")
        if task.status == "queued":
            self.queue.put_nowait(task.id)
        return {"intent": intent.model_dump(), "task": task.model_dump()}

    def confirm(self, task_id):
        if self.halted or self.recovering:
            raise RobotError("STOP_LATCHED")
        task = self.store.get(task_id)
        if task.status != "awaiting_confirmation" or task.intent.intent != "request_help":
            raise RobotError("INVALID_TRANSITION")
        task = self.store.transition(task_id, "queued")
        self.queue.put_nowait(task_id)
        return task

    async def cancel(self, task_id):
        task = self.store.get(task_id)
        if task.status in TERMINAL:
            return task
        task = self.store.transition(task_id, "cancelled")
        await self.robot.cancel(task_id)
        return task

    async def emergency_stop(self, code=None):
        self.halted = True  # No await before the admission gate closes.
        await self.robot.stop()
        for task in self.store.tasks():
            if task.status not in TERMINAL:
                target = "failed" if code and task.status in ("queued", "running", "created") else "cancelled"
                self.store.transition(task.id, target, code or "EMERGENCY_STOP")
        self.store.emit("safety.stopped", {"code": code or "EMERGENCY_STOP"})

    async def recover(self, scene=None):
        if self.recovering:
            raise RobotError("ROBOT_BUSY")
        self.recovering = True
        try:
            await self.emergency_stop()
            if self.current:
                self.current.cancel()
                with suppress(asyncio.CancelledError):
                    await self.current
            if scene is not None:
                if scene not in ("living_room", "help", "obstacle"):
                    raise RobotError("INVALID_SCENE")
                if self.robot.get_status().backend == "mujoco":
                    self.robot.scene = scene
                else:
                    self.robot.scenario = "obstacle" if scene == "obstacle" else "normal"
            await self.robot.recover()
            state = self.robot.get_status()
            self.halted = bool(state.error_code or not state.available)
            self.store.emit("robot.recovered", state.model_dump())
            return state
        finally:
            self.recovering = False

    async def perform(self, task):
        if task.intent.intent == "check_link":
            import asyncio as _aio
            from . import vbot_config
            checks = await _aio.to_thread(vbot_config.run_checks, vbot_config.load())
            passed = sum(1 for c in checks if c["ok"])
            return {"checks": checks, "passed": passed, "total": len(checks)}
        if task.intent.intent == "request_help":
            # MockNotifier: persisted task result, no network or real delivery.
            result = {"delivery_status": "simulated", "contact_alias": "家人", "preview_id": task.id}
            self.store.emit("notification.simulated", result)
            return result
        kinds = {"accompany": "accompany", "follow_me": "accompany",
                 "wake": "wake_reply", "shake_hands": "shake_hands", "start_mapping": "start_mapping"}
        action = SafeRobotAction(kind=kinds.get(task.intent.intent, "move_to_known_zone"),
                                 destination=task.intent.destination)
        return (await self.robot.execute(action, task.id)).model_dump()

    async def run(self):
        while True:
            task_id = await self.queue.get()
            try:
                task = self.store.get(task_id)
                if task.status != "queued":
                    continue
                if self.halted:
                    self.store.transition(task_id, "cancelled", "STOP_LATCHED")
                    continue
                task = self.store.transition(task_id, "running")
                self.current = asyncio.create_task(self.perform(task))
                try:
                    result = await asyncio.wait_for(self.current, self.timeout)
                    if self.store.get(task_id).status == "running":
                        self.store.transition(task_id, "succeeded", result=result)
                except TimeoutError:
                    if self.store.get(task_id).status == "running":
                        self.store.transition(task_id, "timed_out", "TASK_TIMEOUT")
                    await self.emergency_stop("TASK_TIMEOUT")
                except RobotError as exc:
                    if self.store.get(task_id).status == "running":
                        self.store.transition(task_id, "failed", exc.code)
                    self.store.emit("robot.exception", {"code": exc.code, "task_id": task_id})
                    if exc.code != "GAIT_UNAVAILABLE":
                        await self.emergency_stop(exc.code)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except Exception as exc:
                    if self.store.get(task_id).status == "running":
                        self.store.transition(task_id, "failed", "INTERNAL_ERROR")
                    self.store.emit("robot.exception", {"code": "INTERNAL_ERROR", "message": str(exc)})
                    await self.emergency_stop("INTERNAL_ERROR")
                finally:
                    self.current = None
            finally:
                self.queue.task_done()

    async def monitor(self):
        last_error = None
        while True:
            if not self.recovering:
                state = self.robot.get_status()
                self.store.emit("robot.state", state.model_dump())
                if state.error_code and state.error_code != last_error:
                    self.store.emit("robot.exception", {"code": state.error_code})
                    await self.emergency_stop(state.error_code)
                last_error = state.error_code
            for task in self.store.tasks():
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(task.updated_at)).total_seconds()
                if task.status == "awaiting_confirmation" and age > self.timeout:
                    self.store.transition(task.id, "timed_out", "CONFIRMATION_TIMEOUT")
            await asyncio.sleep(.1)
