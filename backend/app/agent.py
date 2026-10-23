from .domain import Intent


def is_stop(text):
    # Conservative local interception. Never calls a resolver or remote service.
    return any(word in text.lower() for word in ("stop", "停下", "停止", "急停", "别动"))


def resolve(text):
    text = text.strip()
    if text == "土豆丝生日快乐":
        return Intent(intent="birthday_song", reply="祝土豆丝生日快乐，然后唱生日快乐歌。")
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
    if text in ("保存地图", "完成建图", "结束建图", "保存这张地图"):
        return Intent(intent="record_field_save", reply="正在保存刚才记录的地图。")
    if text in ("检查链接状态", "检查连接状态", "链路自检"):
        return Intent(intent="check_link", reply="正在执行链路自检（只读，不碰控制）。")
    if text in ("陪我去客厅", "在客厅陪我", "陪陪我"):
        return Intent(intent="accompany", destination="living_room", reply="提供模拟陪伴；MuJoCo 当前只支持原地陪伴。")
    for name, zone in (("客厅", "living_room"), ("卧室", "bedroom"), ("门口", "entrance")):
        if text == f"去{name}":
            return Intent(intent="move_to", destination=zone)
    return Intent(intent="unknown", confidence=0, reply="暂不支持这个指令；未执行动作。")


async def dify_resolve(text):
    """意图解析入口：Dify workflow 优先；未配置/关闭/超时/解析失败 → 本地规则降级。

    本地规则是保底：Dify 挂了狗不聋（核心指令仍认得）。
    急停（is_stop）永远走本地，不经过这里，不依赖任何远程服务。
    """
    # This fixed phrase must stay deterministic even when Dify is enabled.
    if text.strip() == "土豆丝生日快乐":
        return resolve(text)
    from . import dify_client, vbot_config
    cfg = vbot_config.load()
    if cfg.get("dify_enabled") and cfg.get("dify_api_key"):
        try:
            import asyncio
            parsed, _raw, _ms = await asyncio.to_thread(dify_client.call_workflow, cfg, text)
            return _dify_to_intent(dify_client.normalize(parsed, text))
        except Exception:
            pass  # 网络/超时/格式问题一律静默降级本地规则
    return resolve(text)


_DIFY_TO_INTENT = {
    "record_field": "start_mapping",      # 复用已有 start_mapping 链路
    "record_field_save": "record_field_save",
    "check_link": "check_link",
    "find_seat": "find_seat",
    "nearby_fun": "nearby_fun",
    "passthrough": "passthrough",
}


def _dify_to_intent(norm):
    return Intent(intent=_DIFY_TO_INTENT.get(norm["intent"], "passthrough"),
                  confidence=norm["confidence"], slots=norm["slots"],
                  speak_text=norm["speak_text"], raw_text=norm["raw_text"],
                  reply=norm["speak_text"])
