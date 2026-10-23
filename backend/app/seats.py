"""Same-origin gateway to the companion-PC seat detector."""
import asyncio
import json
import os
from urllib.error import URLError
from urllib.request import Request, build_opener, ProxyHandler

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

router = APIRouter(prefix="/api/seats", tags=["seats"])


def _request(path, method="GET"):
    base = os.getenv("SEAT_DETECTOR_URL", "http://127.0.0.1:8766").rstrip("/")
    request = Request(base + path, method=method)
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=4) as response:
            return response.read()
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(503, "空位识别服务未连接，请启动电脑端 seats.py") from exc


@router.get("/status")
async def status():
    try:
        result = json.loads(await asyncio.to_thread(_request, "/api/status"))
        if not isinstance(result, dict):
            raise ValueError("invalid detector status")
        if result.get("status") == "ok" and result.get("age_seconds", 0) > 5:
            result.update(status="error", error="识别画面已过期，请检查相机连接", chairs=[], candidate_count=0)
        return result
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "空位识别服务返回了无效状态") from exc


@router.get("/frame")
async def frame():
    image = await asyncio.to_thread(_request, "/frame.jpg")
    if not image.startswith(b"\xff\xd8"):
        raise HTTPException(502, "空位识别服务未返回 JPEG 图像")
    return Response(image, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/find", status_code=202)
async def find():
    await asyncio.to_thread(_request, "/api/find", "POST")
    return {"accepted": True, "message": "已提交找空位请求，识别完成后由机器狗播报"}
