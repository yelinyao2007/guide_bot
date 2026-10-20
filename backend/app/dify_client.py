"""Dify workflow 客户端：把一句话变成结构化意图。

原则：任何失败（未配置/网络/超时/JSON 解析/校验不过）都不抛给调用方用户路径，
由 agent.dify_resolve 降级本地规则。宁可透传，不可误触发。
"""
import json
import re
import time
import urllib.request

VALID_INTENTS = {"record_field", "record_field_save", "check_link",
                 "find_seat", "nearby_fun", "passthrough"}
_JSON_OBJ = re.compile(r"\{.*\}", re.S)


def _extract_json(text):
    """从 LLM 输出里抠出第一个 JSON 对象（容忍 markdown 代码围栏或前后废话）。"""
    m = _JSON_OBJ.search(text.strip())
    if not m:
        raise ValueError("Dify 输出中未找到 JSON 对象")
    return json.loads(m.group(0))


def call_workflow(cfg, text, source="dog", confidence=1.0):
    """调用 Dify workflow（blocking）。返回 (parsed_dict, raw_output, elapsed_ms)。

    失败抛异常，调用方负责降级。cfg 来自 vbot_config.load()。
    """
    body = json.dumps({
        "inputs": {"text": str(text), "source": str(source), "confidence": float(confidence)},
        "response_mode": "blocking",
        "user": "dog-bridge",
    }).encode()
    req = urllib.request.Request(
        cfg["dify_url"], data=body, method="POST",
        headers={"Authorization": f"Bearer {cfg['dify_api_key']}",
                 "Content-Type": "application/json"})
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=float(cfg.get("dify_timeout") or 3.0)) as resp:
        payload = json.load(resp)
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    data = payload.get("data") or {}
    if data.get("status") and data["status"] != "succeeded":
        raise RuntimeError(f"Dify 工作流执行失败: {data.get('error') or data['status']}")
    outputs = data.get("outputs") or {}
    raw = outputs.get("output") or outputs.get("text")
    if raw is None:
        strs = [v for v in outputs.values() if isinstance(v, str)]
        raw = strs[0] if strs else json.dumps(outputs, ensure_ascii=False)
    if not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    return _extract_json(raw), raw, elapsed_ms


def normalize(parsed, text):
    """校验/清洗 Dify 返回的意图 JSON。校验不过一律 passthrough。"""
    intent = parsed.get("intent") if isinstance(parsed, dict) else None
    try:
        conf = float((parsed or {}).get("confidence", 0))
    except (TypeError, ValueError):
        conf = 0.0
    if intent not in VALID_INTENTS or conf < 0.75:
        intent = "passthrough"
    slots = (parsed or {}).get("slots")
    slots = slots if isinstance(slots, dict) else {}
    speak = (parsed or {}).get("speak_text")
    speak = speak if isinstance(speak, str) else ""
    if intent == "passthrough":
        speak = ""
    return {"intent": intent, "confidence": round(min(max(conf, 0.0), 1.0), 2),
            "slots": {"target": str(slots.get("target", ""))[:50]},
            "speak_text": speak[:60], "raw_text": str(text)[:200]}
