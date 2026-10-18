"""Only this spawned process imports MuJoCo and owns physics/rendering state."""
import os
import queue
import time


class ProcessFlag:
    """Single-byte shared latch; a crashed worker cannot leave a mutex locked."""
    def __init__(self, context):
        self.value = context.RawValue("b", 0)

    def set(self):
        self.value.value = 1

    def is_set(self):
        return bool(self.value.value)

    def wait(self, seconds):
        time.sleep(min(seconds, .02))
        return self.is_set()


def publish(output, payload):
    try:
        output.put_nowait(payload)
    except queue.Full:
        # Bounded telemetry: the next frame will retry, never block safety.
        pass


def run_simulation(model_path, scene, output, stop_event, shutdown, viewer_enabled):
    viewer = None
    try:
        import mujoco
        import numpy as np
        spec = mujoco.MjSpec.from_file(model_path)
        spec.worldbody.add_geom(name="sofa", type=mujoco.mjtGeom.mjGEOM_BOX,
                                size=[.7, .3, .35], pos=[0, 1.5, .35], rgba=[.2, .4, .6, 1])
        if scene == "obstacle":
            spec.worldbody.add_geom(name="safety_obstacle", type=mujoco.mjtGeom.mjGEOM_BOX,
                                    size=[.1, .6, .3], pos=[.6, 0, .3], rgba=[.9, .3, .1, 1])
        model = spec.compile()
        data = mujoco.MjData(model)
        if model.nu != 12 or model.nq != 19 or model.body("base").id < 0:
            raise ValueError("Expected Menagerie Go2 free base and 12 motors")
        home = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_KEY, "home")
        if home >= 0:
            mujoco.mj_resetDataKeyframe(model, data, home)  # Initialization only.
        mujoco.mj_forward(model, data)
        base_id = model.body("base").id
        if viewer_enabled:
            import mujoco.viewer
            viewer = mujoco.viewer.launch_passive(model, data)
        next_publish = 0.
        error = None
        while not shutdown.is_set():
            tick = time.monotonic()
            if scene == "obstacle":
                obstacle = model.geom("safety_obstacle").id
                # Conservative planar clearance using actual simulated base pose
                # and physical box geometry, including the robot footprint.
                delta = np.abs(data.xpos[base_id, :2] - data.geom_xpos[obstacle, :2])
                clearance = np.linalg.norm(np.maximum(delta - model.geom_size[obstacle, :2], 0)) - .3
                if clearance < .25:
                    error = "OBSTACLE_BLOCKED"
                    stop_event.set()
            # No validated gait: passive zero-torque physics, never teleport.
            data.ctrl[:] = 0
            mujoco.mj_step(model, data)
            if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all():
                raise RuntimeError("Non-finite physics state")
            if tick >= next_publish:
                publish(output, {"kind": "state", "heartbeat": tick, "backend": "mujoco",
                    "model_label": "Unitree Go2 / Vbot 仿真替代模型（非 Vbot 动力学验证）",
                    "available": True, "stopped": stop_event.is_set(), "locomotion": False,
                    "position": data.xpos[base_id].tolist(), "quaternion_wxyz": data.xquat[base_id].tolist(),
                    "joints": data.qpos[7:].tolist(), "actuator_controls": data.ctrl.tolist(),
                    "simulation_time": float(data.time), "error_code": error,
                    "scene": scene, "worker_pid": os.getpid()})
                next_publish = tick + .05
                if viewer:
                    if not viewer.is_running():
                        raise RuntimeError("Viewer closed")
                    viewer.sync()
            shutdown.wait(max(0, model.opt.timestep - (time.monotonic() - tick)))
    except Exception as exc:
        stop_event.set()
        publish(output, {"kind": "error", "error_code": "SIMULATION_ERROR", "message": str(exc)})
    finally:
        if viewer:
            viewer.close()
