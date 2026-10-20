"""场景合成公共模块：scene_empty_seat.xml + 狗资产注入（run_sim 与 dog_viewer 共用）。"""
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def compose_scene():
    scene = (HERE / "scene_empty_seat.xml").read_text()
    dog_xml = HERE / "assets/vbot_babo_edu_act.xml"
    if not dog_xml.exists():
        print("⚠️  缺狗资产：先跑 bash fetch_assets.sh（本帧以无狗模式运行）")
        return scene.replace("<!--DOG_INJECT-->", "").replace("<!--DOG_ACTUATORS-->", ""), False
    d = dog_xml.read_text()
    wb = re.search(r"<worldbody>(.*)</worldbody>", d, re.S).group(1)
    wb = f'<body name="dog_root" mocap="true" pos="0 0 0.32">\n{wb}\n</body>'
    act = re.search(r"<actuator>(.*)</actuator>", d, re.S)
    asset = re.search(r"<asset>(.*)</asset>", d, re.S)
    scene = scene.replace("<!--DOG_INJECT-->", wb)
    scene = scene.replace("<!--DOG_ACTUATORS-->", f"<actuator>{act.group(1)}</actuator>" if act else "")
    head = f'<compiler meshdir="{HERE / "assets"}"/>\n  <asset>{asset.group(1) if asset else ""}</asset>\n  <worldbody>'
    scene = scene.replace("<worldbody>", head, 1)
    return scene, True
