from .domain import Intent


def is_stop(text):
    # Conservative local interception. Never calls a resolver or remote service.
    return any(word in text.lower() for word in ("stop", "停下", "停止", "急停", "别动"))


def resolve(text):
    text = text.strip()
    if text in ("小守，我有点不舒服，帮我叫一下家人", "帮我联系家人", "求助"):
        return Intent(intent="request_help", requires_confirmation=True, reply="请确认是否记录一条模拟求助通知，不会实际发送。")
    if text in ("你现在在哪儿", "状态", "status"):
        return Intent(intent="status")
    if text in ("大头大头", "Hey Babo", "hey babo"):
        return Intent(intent="wake", reply="我在，请说指令。")
    if text in ("跟着我", "跟我走"):
        return Intent(intent="follow_me", reply="好的，我跟着您。")
    if text in ("握手", "握个手"):
        return Intent(intent="shake_hands", reply="握手！")
    if text in ("跟我开始记录场地", "开始记录场地", "建图"):
        return Intent(intent="start_mapping", reply="开始记录场地，请带我走一圈。")
    if text in ("陪我去客厅", "在客厅陪我", "陪陪我"):
        return Intent(intent="accompany", destination="living_room", reply="提供模拟陪伴；MuJoCo 当前只支持原地陪伴。")
    for name, zone in (("客厅", "living_room"), ("卧室", "bedroom"), ("门口", "entrance")):
        if text == f"去{name}":
            return Intent(intent="move_to", destination=zone)
    return Intent(intent="unknown", confidence=0, reply="暂不支持这个指令；未执行动作。")
