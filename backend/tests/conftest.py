"""脚本化假狗：接收与真机适配器完全相同的 ssh 命令串，回真格式 YAML/路由表。

不是 mock 机器人：VbotEduAdapter 的解析、路由发现、请求载荷、终态观察全部真跑，
只有传输层被替换（runner 注入），断言落在"发给狗的命令长什么样"。
"""
import time

CFG = {"dog_ip": "10.0.0.9", "ssh_user": "vbot", "zenoh_endpoint": "tcp/10.0.0.9:7447",
       "ros_domain_id": 178, "map_name": "", "notes": ""}

ROUTES = "\n".join([
    "/locomotion/set_run_mode: function_msgs/srv/SetRunMode",
    "/sm/action/lowlevel: software_msgs/srv/LowlevelAction",
    "/navigate_to_target: function_msgs/srv/NavigateToTarget",
    "/locomotion/control_following: function_msgs/srv/ControlFollowing",
    "/locomotion/set_follow_speed: function_msgs/srv/SetFollowSpeed",
    "/set_speak: function_msgs/srv/SetSpeak",
    "/slam/set_slam_mode: slam_msgs/srv/SetSlamMode",
    "/locomotion/status [software_msgs/msg/LocomotionStatus]",
    "/locomotion/body_action_status [function_msgs/msg/BodyActionStatus]",
    "/bms_state [lowlevel_msg/msg/BmsState]",
    "/odometry [nav_msgs/msg/Odometry]",
])

# ros2 topic echo 的 block-style YAML；===BODY/===BMS/===ODOM 是适配器自己的分段标记
STATUS_IDLE = """posture: 1
motion: 0
current_action: 'RL_TROT'
source: 'fsm'
stamp_ns: 1000
===BODY
status: 0
mode: 6
===BMS
soc_percent: 87.5
voltage_mv: 48000
===ODOM
pose:
  pose:
    position:
      x: 0.1
      y: 0.2
      z: 0.3
    orientation:
      w: 1.0
      x: 0.0
      y: 0.0
      z: 0.0
"""

SERVICE_OK = ("requester: making one request:\nresponse:\n  success: true\n"
              "  message: 'ok'\n  error_code: 0\n")


def scripted_dog(fault=None, nav_delay=0., drop_after=None):
    """fault: 'disconnect'(全程ssh失败) / 'busy'(状态恒为运动中) /
    'moving_late'(前两轮静止、之后运动中，用于终态观察超时)；
    drop_after: 前 N 条命令健康、之后 ssh 断连。返回 (runner, calls)。"""
    calls = []
    n = {"status": 0, "all": 0}

    def runner(cmd, timeout=20):
        calls.append(cmd)
        n["all"] += 1
        if fault == "disconnect":
            return False, "ssh: connect to host 10.0.0.9 port 22: No route to host"
        if drop_after and n["all"] > drop_after:
            return False, "ssh: connection reset by peer"
        if "list -t" in cmd:
            return True, ROUTES
        if "topic echo /locomotion/status" in cmd:
            n["status"] += 1
            if fault == "busy" or (fault == "moving_late" and n["status"] > 2):
                return True, STATUS_IDLE.replace("motion: 0", "motion: 1")
            return True, STATUS_IDLE
        if "service call" in cmd:
            if nav_delay and "NavigateToTarget" in cmd:
                time.sleep(nav_delay)
            return True, SERVICE_OK
        return True, ""
    return runner, calls
