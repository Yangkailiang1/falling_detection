"""
ECNU VL 视觉模型分析工具

用途: 分析图片/截图内容 (开发者工具界面、事件截图、摄像头帧等)。
配置: .env → ECNU_VL_API_KEY / ECNU_VL_API_URL / ECNU_VL_MODEL

用法:
    python scripts/ecnu_vl.py <图片路径> [提示词]
"""

import base64
import json
import os
import sys
from pathlib import Path

import requests

# 加载 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv(Path(__file__).resolve().parents[2] / ".env")  # analysis_platform/.env

API_KEY = os.getenv("ECNU_VL_API_KEY", "")
API_URL = os.getenv("ECNU_VL_API_URL", "https://chat.ecnu.edu.cn/open/api/v1/chat/completions")
MODEL = os.getenv("ECNU_VL_MODEL", "ecnu-vl")


def analyze_image(image_path: str, prompt: str = "请详细描述这张图片的内容") -> str:
    """分析本地图片文件, 返回模型文本回复。"""
    if not API_KEY:
        return "错误: ECNU_VL_API_KEY 未配置 (.env)"
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    payload = {
        "model": MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
            ],
        }],
        "stream": False,
    }
    try:
        resp = requests.post(
            API_URL,
            json=payload,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            timeout=60,
        )
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as e:
        return f"调用失败: {e}"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python ecnu_vl.py <图片路径> [提示词]")
        sys.exit(1)
    img = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else "请详细描述这张图片的内容"
    print(analyze_image(img, prompt))
