import asyncio
import os
import signal
import time
import pytest
from backend.app.robot.base import RobotError, SafeRobotAction
from backend.app.robot.mujoco_adapter import DEFAULT_MODEL, MujocoRobotAdapter
from backend.app.robot.vbot_adapter import VbotEduAdapter
from conftest import CFG, scripted_dog

ACCOMPANY = SafeRobotAction(kind="accompany")
MOVE = SafeRobotAction(kind="move_to_known_zone", destination="living_room")
BIRTHDAY = SafeRobotAction(kind="birthday_song")


def make_robot(**kw):
    runner, calls = scripted_dog(**kw)
    robot = VbotEduAdapter(cfg=CFG, runner=runner, poll_interval=.05)
    robot.idle_timeout = 1.
    return robot, calls


async def test_vbot_status_parse_and_route_discovery():
    robot, calls = make_robot()
    robot._discover()
    robot._refresh()
    st = robot.get_status()
    assert st.available and st.error_code is None
    assert st.backend == "vbot" and st.posture == "standing" and st.body_mode == "RL_TROT"
    assert st.battery_percent == 87.5 and st.current_action == "RL_TROT"
    assert st.position == [0.1, 0.2, 0.3] and st.quaternion_wxyz == [1., 0., 0., 0.]
    assert st.stopped and not st.locomotion
    assert robot._route("lowlevel") == "/sm/action/lowlevel"
    assert robot._route("nav") == "/navigate_to_target"
    assert robot._route("speak") == "/set_speak"


async def test_vbot_accompany_speed_and_stop_payloads():
    robot, calls = make_robot()
    robot._discover()
    robot._refresh()
    result = await robot.execute(ACCOMPANY, "task-abcdef12")
    assert result.completed and "gb-task-abcd" in result.detail
    joined = "\n".join(calls)
    assert "mode: 8" in joined and "req_id: 'gb-task-abcd'" in joined  # ACCOMPANY
    assert "following_speed: 0.5" in joined and "source: 'guide'" in joined  # 导盲限速写死
    await robot.stop()
    assert "mode: 3" in calls[-1] and "target_state: 1" in calls[-1]  # SAFE_STOP_SEQUENCE


async def test_vbot_birthday_sends_greeting_then_song():
    robot, calls = make_robot()
    robot._discover()
    robot._refresh()
    result = await robot.execute(BIRTHDAY, "birthday-1")
    assert result.completed
    speech_calls = [call for call in calls if "function_msgs/srv/SetSpeak" in call]
    assert len(speech_calls) == 2
    assert "祝土豆丝生日快乐。" in speech_calls[0]
    assert "祝你生日快乐" in speech_calls[1]
    assert sum("lowlevel_msg/srv/HeadAction" in call for call in calls) == 5
    assert sum("peripheral_msgs/srv/LightControl" in call for call in calls) == 5


async def test_vbot_stand_uses_manual_not_fixed_stand():
    robot, calls = make_robot()
    robot._discover()
    robot._refresh()
    result = await robot.stand()
    assert result.completed
    joined = "\n".join(calls)
    assert "function_msgs/srv/SetRunMode" in joined
    assert "mode: 0" in joined  # MODE_MANUAL，文档禁止用 FIXED_STAND 顶替
    assert "has_is_traction_user_param: true" in joined and "is_traction_user_param: false" in joined


async def test_vbot_lie_down_uses_safe_sequence():
    robot, calls = make_robot()
    robot._discover()
    robot._refresh()
    await robot.lie_down()
    assert "mode: 4" in calls[-2] or "mode: 4" in calls[-1]  # SAFE_LAYDOWN_SEQUENCE


async def test_vbot_move_to_nav_payload():
    robot, calls = make_robot()
    robot._discover()
    robot._refresh()
    result = await robot.execute(MOVE, "nav-1")
    assert result.completed
    assert "target_name: 'living_room'" in "\n".join(calls)


async def test_vbot_mapping_uses_empty_then_configured_name(tmp_path):
    robot, calls = make_robot()
    robot.cfg["map_name"] = "demo_site_1f"
    robot._discover()
    robot._refresh()
    await robot.execute(SafeRobotAction(kind="start_mapping"), "map-start")
    await robot.execute(SafeRobotAction(kind="save_mapping"), "map-save")
    slam = [call for call in calls if "slam_msgs/srv/SetSlamMode" in call]
    assert "map_name: ''" in slam[-2]
    assert "map_name: 'demo_site_1f'" in slam[-1]


async def test_vbot_busy_gate():
    robot, _ = make_robot(fault="busy")
    robot._discover()
    robot._refresh()
    assert robot.get_status().locomotion
    with pytest.raises(RobotError, match="ROBOT_BUSY"):
        await robot.execute(ACCOMPANY, "t")


async def test_vbot_disconnect_sets_error_and_rejects():
    robot, _ = make_robot(fault="disconnect")
    with pytest.raises(RobotError, match="ROBOT_DISCONNECTED"):
        robot._refresh()
    st = robot.get_status()
    assert not st.available and st.error_code == "ROBOT_DISCONNECTED"
    with pytest.raises(RobotError, match="ROBOT_DISCONNECTED"):
        await robot.execute(ACCOMPANY, "t")


async def test_vbot_no_terminal_observation_reports_unknown():
    robot, _ = make_robot(fault="moving_late")
    robot._discover()
    robot._refresh()
    with pytest.raises(RobotError, match="TASK_TIMEOUT"):
        await robot.execute(MOVE, "t")


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
        await physics.execute(MOVE, "two")
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
