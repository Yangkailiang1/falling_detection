"""
微信消息通知通道 — 免费方案

主选: 企业微信群机器人 Webhook (免费无限制, 秒级到达)
  配置: WECOM_WEBHOOK_KEY (企业微信群 → 群机器人 → webhook key)

备选: PushPlus (扫码关注即用)
  配置: PUSHPLUS_TOKEN

对应调研: 大厂免费方案选型 (2026-08)
"""

import logging
import os

import requests

logger = logging.getLogger(__name__)

# === 企业微信群机器人 Webhook ===
WECOM_WEBHOOK_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"


def send_wecom_webhook(content: str, webhook_key: str = "") -> dict:
    """
    企业微信群机器人推送文本消息。

    Args:
        content: 消息内容 (text)
        webhook_key: webhook key, 默认从环境变量 WECOM_WEBHOOK_KEY 读取

    Returns:
        {"success": bool, "message": str}
    """
    key = webhook_key or os.getenv("WECOM_WEBHOOK_KEY", "")
    if not key:
        return {"success": False, "message": "WECOM_WEBHOOK_KEY 未配置"}
    url = f"{WECOM_WEBHOOK_URL}?key={key}"
    payload = {"msgtype": "text", "text": {"content": content[:2000]}}
    try:
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            logger.info(f"[WeChat] ✅ 企业微信推送成功: {content[:50]}...")
            return {"success": True, "message": "ok"}
        return {"success": False, "message": f"企业微信错误: {data.get('errmsg')}"}
    except Exception as e:
        logger.warning(f"[WeChat] 企业微信推送失败: {e}")
        return {"success": False, "message": str(e)}


def send_wecom_image(content: str = "", webhook_key: str = "") -> dict:
    """
    企业微信群机器人推送文本 + 摄像头抓拍图（用管线最近帧，零成本）。

    Args:
        content: 文本消息（可选，先发文本再发图）
        webhook_key: webhook key, 默认从环境变量 WECOM_WEBHOOK_KEY 读取

    Returns:
        {"success": bool, "message": str}
    """
    key = webhook_key or os.getenv("WECOM_WEBHOOK_KEY", "")
    if not key:
        return {"success": False, "message": "WECOM_WEBHOOK_KEY 未配置"}
    url = f"{WECOM_WEBHOOK_URL}?key={key}"
    try:
        import base64
        import hashlib
        import cv2
        # 从推理管线取最近一帧（无需额外连接 RTSP）
        from app.inference import world_av_pipeline
        frame = world_av_pipeline._last_raw_frame
        if frame is None:
            return {"success": False, "message": "尚无视频帧可抓拍"}
        _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        img_b64 = base64.b64encode(jpeg.tobytes()).decode()
        img_md5 = hashlib.md5(jpeg.tobytes()).hexdigest()

        # 先发文本
        if content:
            payload = {"msgtype": "text", "text": {"content": content[:2000]}}
            requests.post(url, json=payload, timeout=10)
        # 再发图片
        payload = {"msgtype": "image", "image": {"base64": img_b64, "md5": img_md5}}
        resp = requests.post(url, json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            logger.info(f"[WeChat] ✅ 企业微信 文本+抓拍图 推送成功 ({len(jpeg.tobytes())//1024}KB)")
            return {"success": True, "message": "ok"}
        return {"success": False, "message": f"企业微信错误: {data.get('errmsg')}"}
    except Exception as e:
        logger.warning(f"[WeChat] 企业微信抓拍图推送失败: {e}")
        return {"success": False, "message": str(e)}


def send_wecom_file(file_path: str, webhook_key: str = "") -> dict:
    """
    企业微信群机器人推送文件（视频/录音等）。

    Args:
        file_path: 本地文件路径 (≤20MB)
        webhook_key: webhook key, 默认从环境变量 WECOM_WEBHOOK_KEY 读取

    Returns:
        {"success": bool, "message": str}
    """
    key = webhook_key or os.getenv("WECOM_WEBHOOK_KEY", "")
    if not key:
        return {"success": False, "message": "WECOM_WEBHOOK_KEY 未配置"}
    if not os.path.exists(file_path):
        return {"success": False, "message": f"文件不存在: {file_path}"}
    try:
        # 1. 上传获取 media_id
        with open(file_path, "rb") as f:
            files = {"media": (os.path.basename(file_path), f, "application/octet-stream")}
            up = requests.post(
                f"{WECOM_WEBHOOK_URL.replace('/send', '/upload_media')}?key={key}&type=file",
                files=files, timeout=30)
        up_data = up.json()
        if up_data.get("errcode") != 0 or "media_id" not in up_data:
            return {"success": False, "message": f"上传失败: {up_data.get('errmsg')}"}
        # 2. 发送文件消息
        payload = {"msgtype": "file", "file": {"media_id": up_data["media_id"]}}
        resp = requests.post(f"{WECOM_WEBHOOK_URL}?key={key}", json=payload, timeout=10)
        data = resp.json()
        if data.get("errcode") == 0:
            logger.info(f"[WeChat] ✅ 企业微信文件推送成功: {os.path.basename(file_path)}")
            return {"success": True, "message": "ok"}
        return {"success": False, "message": f"企业微信错误: {data.get('errmsg')}"}
    except Exception as e:
        logger.warning(f"[WeChat] 企业微信文件推送失败: {e}")
        return {"success": False, "message": str(e)}


# === PushPlus (备选) ===
PUSHPLUS_URL = "http://www.pushplus.plus/send"


def send_pushplus(title: str, content: str, token: str = "") -> dict:
    """
    PushPlus 微信公众号推送。

    Args:
        title: 标题
        content: 内容 (支持 html/markdown)
        token: PushPlus token, 默认从环境变量 PUSHPLUS_TOKEN 读取

    Returns:
        {"success": bool, "message": str}
    """
    tkn = token or os.getenv("PUSHPLUS_TOKEN", "")
    if not tkn:
        return {"success": False, "message": "PUSHPLUS_TOKEN 未配置"}
    payload = {"token": tkn, "title": title[:100], "content": content[:3000], "template": "html"}
    try:
        resp = requests.post(PUSHPLUS_URL, data=payload, timeout=10)
        data = resp.json()
        if data.get("code") == 200:
            logger.info(f"[WeChat] ✅ PushPlus 推送成功: {title[:50]}...")
            return {"success": True, "message": "ok"}
        return {"success": False, "message": f"PushPlus 错误: {data.get('msg')}"}
    except Exception as e:
        logger.warning(f"[WeChat] PushPlus 推送失败: {e}")
        return {"success": False, "message": str(e)}


# === 告警通知构造 (接入 notification_service) ===
RISK_LABEL = {"I": "Ⅰ级高危", "II": "Ⅱ级中危", "III": "Ⅲ级低危"}


def build_alert_content(
    event_id: str,
    risk_level: str,
    location: str,
    description: str,
    touch_part: str = "",
    medical_report: str = "",
) -> str:
    """构造微信告警消息文本（含可选医疗简报全文）。"""
    risk = RISK_LABEL.get(risk_level, risk_level)
    lines = [
        "🚨 跌倒告警",
        f"风险等级: {risk}",
        f"地点: {location}",
        f"着地部位: {touch_part or '未知'}",
        f"事件: {description or '检测到跌倒'}",
        f"事件编号: {event_id}",
    ]
    if medical_report:
        lines += [
            "────────────",
            "📋 医疗简报:",
            medical_report[:1200],
        ]
    return "\n".join(lines)


def _wecom_send_images(content: str, image_list: list, webhook_key: str = "") -> dict:
    """企业微信群: 文本一次 + 多张图依次发送。每张图独立结果，互不影响。"""
    key = webhook_key or os.getenv("WECOM_WEBHOOK_KEY", "")
    url = f"{WECOM_WEBHOOK_URL}?key={key}"
    results = []
    try:
        # 文本
        if content:
            payload = {"msgtype": "text", "text": {"content": content[:2000]}}
            requests.post(url, json=payload, timeout=10)
        # 多张图
        for img_bytes in image_list:
            import base64
            import hashlib
            img_b64 = base64.b64encode(img_bytes).decode()
            img_md5 = hashlib.md5(img_bytes).hexdigest()
            payload = {"msgtype": "image", "image": {"base64": img_b64, "md5": img_md5}}
            resp = requests.post(url, json=payload, timeout=10)
            data = resp.json()
            results.append(data.get("errcode") == 0)
            if data.get("errcode") != 0:
                logger.warning(f"[WeChat] 图片推送失败: {data.get('errmsg')}")
        ok = bool(results) and all(results)
        logger.info(f"[WeChat] ✅ 企业微信 文本+{len(image_list)}图 推送完成")
        return {"success": ok, "message": "ok" if ok else "部分图片失败"}
    except Exception as e:
        logger.warning(f"[WeChat] 企业微信图片推送异常: {e}")
        return {"success": False, "message": str(e)}


def send_wechat_notify(
    event_id: str,
    risk_level: str,
    location: str,
    description: str,
    touch_part: str = "",
    medical_report: str = "",
    video_clip: str = "",
    snapshot_count: int = 1,
) -> dict:
    """
    微信告警推送（企业微信优先, PushPlus 兜底, 逐级降级）。

    企业微信通道:
        文本(含医疗简报) + N 张抓拍图 + 视频文件(可选)
        - 抓拍图: 优先管线缓存帧; 无管线时独立 RTSP 抓帧 (capture_assets)
        - 失败降级: 图失败→纯文本; 文本失败→PushPlus

    Args:
        snapshot_count: 抓拍图数量 (默认1; 告警场景传3)
        video_clip: 视频文件路径 (可选, 推送后删除)
    """
    content = build_alert_content(
        event_id, risk_level, location, description, touch_part, medical_report)

    # ---- 企业微信通道 ----
    if os.getenv("WECOM_WEBHOOK_KEY"):
        images = []
        try:
            from app.services.capture_assets import capture_frames
            images = capture_frames(snapshot_count, interval=2.0)  # 独立 RTSP 抓帧
        except Exception as e:
            logger.warning(f"[WeChat] 抓帧失败: {e}")
            images = []
        # 无独立抓帧时尝试管线缓存帧
        if not images:
            try:
                from app.inference import world_av_pipeline
                import cv2
                frame = world_av_pipeline._last_raw_frame
                if frame is not None:
                    _, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    images.append(jpeg.tobytes())
            except Exception:
                pass

        result = _wecom_send_images(content, images)
        if not result["success"]:
            # 降级: 纯文本
            result = send_wecom_webhook(content)
        # 视频文件（视频失败不影响文本/图结果）
        if video_clip and os.path.exists(video_clip):
            try:
                send_wecom_file(video_clip)
            except Exception as e:
                logger.warning(f"[WeChat] 视频推送失败: {e}")
            # [V9.3] 视频保留供小程序回放, 不再推送后删除
        result["channel"] = "wecom"
        return result

    # ---- PushPlus 兜底 ----
    if os.getenv("PUSHPLUS_TOKEN"):
        result = send_pushplus("🚨 跌倒告警", content.replace("\n", "<br>"))
        result["channel"] = "pushplus"
        return result
    return {"success": False, "message": "未配置任何微信推送通道 (WECOM_WEBHOOK_KEY / PUSHPLUS_TOKEN)", "channel": "none"}
