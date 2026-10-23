import time
import pytest
from fastapi.testclient import TestClient
from backend.app.agent import resolve
from backend.app.domain import Intent, Task
from backend.app.main import create_app
from backend.app.robot.base import RobotError
from backend.app.robot.vbot_adapter import VbotEduAdapter
from backend.app.store import Store
from conftest import CFG, scripted_dog


def wait_task(client, task_id, status):
    until = time.monotonic() + 3
    while time.monotonic() < until:
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] == status:
            return task
        time.sleep(.02)
    pytest.fail(str(task))


@pytest.fixture
def client(tmp_path):
    runner, _ = scripted_dog()
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.05)
    with TestClient(create_app(robot, f"sqlite:///{tmp_path}/test.db")) as client:
        yield client


def test_intents_and_unknown_destinations():
    assert resolve("去客厅").destination == "living_room"
    assert resolve("陪我去客厅").intent == "accompany"
    assert resolve("去医院").intent == "unknown"
    assert resolve("去客厅再去医院").intent == "unknown"
    assert resolve("帮我联系家人").requires_confirmation
    birthday = resolve("土豆丝生日快乐")
    assert birthday.intent == "birthday_song"
    assert "土豆丝" in birthday.reply
    assert resolve("保存地图").intent == "record_field_save"


def test_help_confirmation_once(client):
    task = client.post("/api/commands", json={"text": "帮我联系家人"}).json()["task"]
    assert task["status"] == "awaiting_confirmation"
    assert not any(e["type"] == "notification.simulated" for e in client.get("/api/events").json())
    assert client.post(f'/api/tasks/{task["id"]}/confirm').status_code == 200
    done = wait_task(client, task["id"], "succeeded")
    assert done["result"]["delivery_status"] == "simulated"
    assert client.post(f'/api/tasks/{task["id"]}/confirm').status_code == 409


def test_serialized_motion_stop_and_recovery(client):
    first = client.post("/api/commands", json={"text": "去客厅"}).json()["task"]
    second = client.post("/api/commands", json={"text": "去卧室"}).json()["task"]
    assert second["status"] == "queued"
    assert sum(t["status"] == "running" for t in client.get("/api/tasks").json()) <= 1
    client.post("/api/emergency-stop")
    wait_task(client, first["id"], "cancelled")
    wait_task(client, second["id"], "cancelled")
    assert client.post("/api/commands", json={"text": "去客厅"}).status_code == 409
    assert client.post("/api/simulation/recover", json={}).status_code == 200
    third = client.post("/api/commands", json={"text": "在客厅陪我"}).json()["task"]
    wait_task(client, third["id"], "succeeded")


def test_stop_bypasses_resolver(tmp_path):
    def forbidden(text):
        raise AssertionError("Resolver must never receive emergency stop")
    runner, _ = scripted_dog()
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.05)
    with TestClient(create_app(robot, f"sqlite:///{tmp_path}/stop.db", resolver=forbidden)) as client:
        for command in ("立即停止", "停下", "stop", "紧急停止！"):
            assert client.post("/api/commands", json={"text": command}).status_code == 200


@pytest.mark.parametrize("fault,expected,code", [("drop_midway", "failed", "SERVICE_REJECTED"), ("busy", "failed", "ROBOT_BUSY")])
def test_faults(tmp_path, fault, expected, code):
    runner, _ = scripted_dog(fault=fault, drop_after=2 if fault == "drop_midway" else None)
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.05)
    with TestClient(create_app(robot, f"sqlite:///{tmp_path}/fault.db")) as client:
        task = client.post("/api/commands", json={"text": "去客厅"}).json()["task"]
        assert wait_task(client, task["id"], expected)["error_code"] == code


def test_disconnect_at_boot_latches(tmp_path):
    runner, _ = scripted_dog(fault="disconnect")
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.02)
    with TestClient(create_app(robot, f"sqlite:///{tmp_path}/boot.db")) as client:
        until = time.monotonic() + 3
        while time.monotonic() < until:
            if client.post("/api/commands", json={"text": "去客厅"}).status_code == 409:
                assert not client.get("/api/health").json()["ready"]
                return
            time.sleep(.05)
        pytest.fail("boot disconnect did not latch")


def test_websocket_order_and_replay(client):
    with client.websocket_connect("/ws/events") as ws:
        first = ws.receive_json()
        client.post("/api/commands", json={"text": "帮我联系家人"})
        second = ws.receive_json()
        assert second["seq"] > first["seq"]
    events = client.get(f'/api/events?after={first["seq"]}').json()
    assert events[0]["seq"] == second["seq"]
    with client.websocket_connect(f'/ws/events?after={first["seq"]}') as ws:
        assert ws.receive_json()["seq"] == second["seq"]


def test_persistence_restart_and_terminal_immutability(tmp_path):
    url = f"sqlite:///{tmp_path}/restart.db"
    store = Store(url)
    task = store.add(Task(intent=Intent(intent="accompany")))
    store.transition(task.id, "queued")
    store.transition(task.id, "running")
    store.engine.dispose()
    runner, _ = scripted_dog()
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.05)
    with TestClient(create_app(robot, url)) as client:
        assert client.get(f"/api/tasks/{task.id}").json()["error_code"] == "SERVER_RESTART"
        with pytest.raises(RobotError, match="INVALID_TRANSITION"):
            client.app.state.service.store.transition(task.id, "running")


def test_confirmation_expiry_and_unknown(client):
    assert client.post("/api/commands", json={"text": "去医院"}).json()["task"] is None
    assert client.get("/api/tasks/absent").status_code == 404


def test_confirmation_timeout(tmp_path):
    runner, _ = scripted_dog()
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.05)
    with TestClient(create_app(robot, f"sqlite:///{tmp_path}/expiry.db", timeout=.05)) as client:
        task = client.post("/api/commands", json={"text": "求助"}).json()["task"]
        wait_task(client, task["id"], "timed_out")
        assert client.post(f'/api/tasks/{task["id"]}/confirm').status_code == 409
