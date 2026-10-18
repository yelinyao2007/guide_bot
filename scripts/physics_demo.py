"""Run from repo root: python -m scripts.physics_demo [--viewer] [--scene obstacle]."""
import argparse
import asyncio
from backend.app.robot.mujoco_adapter import MujocoRobotAdapter


async def main(args):
    robot = MujocoRobotAdapter(scene=args.scene, viewer=args.viewer)
    try:
        await robot.start()
        for _ in range(args.seconds * 10):
            print(robot.get_status().model_dump_json(), flush=True)
            await asyncio.sleep(.1)
        await robot.stop()
    finally:
        await robot.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--viewer", action="store_true")
    parser.add_argument("--scene", choices=["living_room", "help", "obstacle"], default="living_room")
    parser.add_argument("--seconds", type=int, default=10)
    asyncio.run(main(parser.parse_args()))
