import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from .robot.base import RobotError
from .robot.mock import MockRobot
from .robot.mujoco_adapter import DEFAULT_MODEL, MujocoRobotAdapter
from .service import Service
from .store import Store


class Command(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class Recovery(BaseModel):
    scene: Literal["living_room", "help", "obstacle"] | None = None


def create_app(robot=None, database_url=None, timeout=None, resolver=None):
    @asynccontextmanager
    async def lifespan(app):
        selected = robot
        if selected is None:
            mode = os.getenv("ROBOT_BACKEND", "mock")
            if mode == "mock":
                selected = MockRobot(scenario=os.getenv("MOCK_SCENARIO", "normal"))
            elif mode == "mujoco":
                selected = MujocoRobotAdapter(model_path=os.getenv("MUJOCO_MODEL", str(DEFAULT_MODEL)),
                    scene=os.getenv("SIM_SCENARIO", "living_room"), viewer=os.getenv("MUJOCO_VIEWER", "0") == "1",
                    start_timeout=float(os.getenv("SIM_START_TIMEOUT_SECONDS", "15")),
                    heartbeat_timeout=float(os.getenv("SIM_HEARTBEAT_TIMEOUT_SECONDS", "2")))
            else:
                raise RuntimeError("Only mock and mujoco are enabled; Vbot hardware is unavailable")
        store = Store(database_url or os.getenv("DATABASE_URL", "sqlite:///./guardian.db"))
        kwargs = {"resolver": resolver} if resolver else {}
        service = Service(selected, store, timeout if timeout is not None else float(os.getenv("TASK_TIMEOUT_SECONDS", "30")), **kwargs)
        app.state.service = service
        await service.start()
        try:
            yield
        finally:
            await service.close()

    app = FastAPI(title="最后一米守护者", lifespan=lifespan)

    @app.exception_handler(RobotError)
    async def robot_error(request, exc):
        status = 404 if exc.code == "NOT_FOUND" else 503 if exc.code in ("HARDWARE_UNAVAILABLE", "ROBOT_DISCONNECTED", "SIMULATION_ERROR") else 409
        return JSONResponse(status_code=status, content={"code": exc.code, "message": str(exc), "request_id": str(uuid4())})

    @app.get("/api/health")
    async def health():
        state = app.state.service.robot.get_status()
        return {"ready": state.available, "backend": state.backend, "model": state.model_label, "locomotion": state.locomotion}

    @app.get("/api/robot/status")
    async def robot_status():
        return app.state.service.robot.get_status()

    @app.post("/api/commands")
    async def commands(command: Command):
        return await app.state.service.command(command.text)

    @app.get("/api/tasks")
    async def tasks():
        return app.state.service.store.tasks()

    @app.get("/api/tasks/{task_id}")
    async def task(task_id: str):
        return app.state.service.store.get(task_id)

    @app.post("/api/tasks/{task_id}/confirm")
    async def confirm(task_id: str):
        return app.state.service.confirm(task_id)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel(task_id: str):
        return await app.state.service.cancel(task_id)

    @app.post("/api/emergency-stop")
    async def emergency_stop():
        await app.state.service.emergency_stop()
        return app.state.service.robot.get_status()

    @app.post("/api/simulation/recover")
    async def recover(body: Recovery):
        return await app.state.service.recover(body.scene)

    @app.post("/api/demo/reset")
    async def reset():
        if app.state.service.robot.get_status().backend != "mock":
            raise RobotError("MOCK_ONLY")
        state = await app.state.service.recover("living_room")
        app.state.service.store.emit("demo.reset", {})
        return state

    @app.get("/api/events")
    async def events(after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
        return app.state.service.store.events(after, limit)

    @app.websocket("/ws/events")
    async def stream(ws: WebSocket, after: int = Query(0, ge=0)):
        await ws.accept()
        try:
            while True:
                batch = app.state.service.store.events(after)
                for event in batch:
                    await asyncio.wait_for(ws.send_json(event), 2.)
                    after = event["seq"]
                # Receive disconnect frames, while continuously polling durable events.
                try:
                    message = await asyncio.wait_for(ws.receive(), .1)
                    if message["type"] == "websocket.disconnect":
                        break
                except TimeoutError:
                    pass
        except (WebSocketDisconnect, RuntimeError, TimeoutError):
            pass

    frontend = Path(__file__).resolve().parents[2] / "frontend/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="dashboard")
    return app


app = create_app()
