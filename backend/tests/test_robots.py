import asyncio
import os
import signal
import time
import pytest
from backend.app.robot.base import RobotError, SafeRobotAction
from backend.app.robot.mock import MockRobot
from backend.app.robot.mujoco_adapter import DEFAULT_MODEL, MujocoRobotAdapter
from backend.app.robot.vbot_stub import VbotAdapter

ACCOMPANY = SafeRobotAction(kind="accompany_simulated")


async def test_mock_stop_is_fast_and_latched():
    robot = MockRobot(duration=10)
    task = asyncio.create_task(robot.execute(ACCOMPANY, "one"))
    await asyncio.sleep(.03)
    start = time.monotonic()
    await robot.stop()
    with pytest.raises(RobotError, match="STOP_LATCHED"):
        await task
    assert time.monotonic() - start < .25
    await robot.stop()
    assert robot.get_status().stopped
    await robot.recover()
    robot.duration = .01
    assert (await robot.execute(ACCOMPANY, "two")).completed


@pytest.mark.parametrize("scenario,code", [("obstacle", "OBSTACLE_BLOCKED"), ("disconnect", "ROBOT_DISCONNECTED")])
async def test_mock_faults(scenario, code):
    robot = MockRobot(scenario=scenario)
    with pytest.raises(RobotError, match=code):
        await robot.execute(ACCOMPANY, "one")
    assert robot.get_status().stopped


async def test_mock_timeout():
    robot = MockRobot(scenario="timeout")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(robot.execute(ACCOMPANY, "one"), .05)
    await robot.stop()
    assert robot.get_status().stopped


async def test_vbot_never_connects():
    with pytest.raises(RobotError, match="HARDWARE_UNAVAILABLE"):
        await VbotAdapter().execute(ACCOMPANY, "one")


@pytest.fixture
async def physics():
    pytest.importorskip("mujoco")
    if not DEFAULT_MODEL.exists():
        pytest.skip("Download pinned Menagerie Go2 first")
    robot = MujocoRobotAdapter(duration=.1)
    await robot.start()
    try:
        yield robot
    finally:
        await robot.close()


@pytest.mark.physics
async def test_real_model_state_step_stop(physics):
    first = physics.get_status()
    assert first.worker_pid != os.getpid()
    assert first.available and not first.locomotion
    assert len(first.joints) == 12
    assert len(first.quaternion_wxyz) == 4
    await asyncio.sleep(.2)
    after = physics.get_status()
    assert after.simulation_time > first.simulation_time
    assert after.position != first.position  # gravity, not a static pose mock
    assert (await physics.execute(ACCOMPANY, "one")).completed
    with pytest.raises(RobotError, match="GAIT_UNAVAILABLE"):
        await physics.execute(SafeRobotAction(kind="move_to_known_zone", destination="living_room"), "two")
    await physics.stop()
    await physics.stop()
    await asyncio.sleep(.1)
    state = physics.get_status()
    assert state.stopped and state.actuator_controls == [0.] * 12
    assert state.simulation_time > after.simulation_time  # stop is not frozen physics
    with pytest.raises(RobotError, match="STOP_LATCHED"):
        await physics.execute(ACCOMPANY, "three")


@pytest.mark.physics
async def test_disconnect_and_explicit_recovery(physics):
    old_pid = physics.process.pid
    physics.process.terminate()
    await asyncio.to_thread(physics.process.join, 2)
    assert physics.get_status().error_code == "ROBOT_DISCONNECTED"
    with pytest.raises(RobotError, match="ROBOT_DISCONNECTED"):
        await physics.execute(ACCOMPANY, "one")
    await physics.recover()
    assert physics.get_status().available
    assert physics.process.pid != old_pid
    assert (await physics.execute(ACCOMPANY, "two")).completed


@pytest.mark.physics
async def test_stale_worker(physics):
    if not hasattr(signal, "SIGSTOP"):
        pytest.skip("POSIX fault injection")
    os.kill(physics.process.pid, signal.SIGSTOP)
    try:
        physics.heartbeat_timeout = .1
        await asyncio.sleep(.2)
        assert physics.get_status().error_code == "SIM_HEARTBEAT_TIMEOUT"
        assert physics.stop_event.is_set()
    finally:
        os.kill(physics.process.pid, signal.SIGCONT)


@pytest.mark.physics
async def test_obstacle_scene(physics):
    physics.scene = "obstacle"
    await physics.recover()
    assert physics.get_status().error_code == "OBSTACLE_BLOCKED"
    assert physics.get_status().stopped
    with pytest.raises(RobotError, match="OBSTACLE_BLOCKED"):
        await physics.execute(ACCOMPANY, "one")


@pytest.mark.physics
async def test_invalid_model_and_start_timeout():
    pytest.importorskip("mujoco")
    robot = MujocoRobotAdapter(model_path="/missing/guardian-scene.xml")
    with pytest.raises(RobotError):
        await robot.start()
    assert robot.process is None
    robot = MujocoRobotAdapter(start_timeout=0)
    with pytest.raises(RobotError, match="SIM_START_TIMEOUT"):
        await robot.start()
    assert robot.process is None
