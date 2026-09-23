#!/usr/bin/env python3
"""
批量回填历史事件现场抓拍图 (V9.4)

背景: 萤石签名 URL 24h 过期(403), 历史事件的 capture_pic_url 均已失效。
本脚本调用后端 /api/fall-events/<id>/capture/refresh 逐个重新抓拍并持久化本地,
让演示数据直接可用。

用法:
    python3 scripts/backfill_captures.py [--limit N] [--delay S] [--only-missing]
    --limit N        最多处理 N 个事件 (默认全部)
    --delay S        每次抓拍间隔秒数 (默认 3, 避免频控)
    --only-missing   只处理本地无图片文件的事件 (默认开启)
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

BASE = "http://localhost:5001"
ARCHIVE = Path(__file__).resolve().parents[1] / "data" / "fall_events_archive.json"
CAPTURES = Path(__file__).resolve().parents[1] / "data" / "captures"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="最多处理 N 个")
    parser.add_argument("--delay", type=float, default=3.0, help="每次间隔秒数")
    parser.add_argument("--only-missing", action="store_true", default=True,
                        help="只处理本地无图的事件")
    args = parser.parse_args()

    raw = json.loads(ARCHIVE.read_text(encoding="utf-8"))
    items = list(raw.values()) if isinstance(raw, dict) else raw
    # 倒序(新的在前)
    items.sort(key=lambda e: e.get("created_at") or "", reverse=True)

    targets = []
    for e in items:
        eid = e.get("event_id")
        if not eid:
            continue
        if args.only_missing and (CAPTURES / f"{eid}.jpg").exists():
            continue
        targets.append(eid)

    if args.limit:
        targets = targets[:args.limit]

    print(f"共 {len(items)} 个事件, 需回填 {len(targets)} 个, 间隔 {args.delay}s...")
    ok_cnt = 0
    for i, eid in enumerate(targets, 1):
        try:
            r = requests.post(f"{BASE}/api/fall-events/{eid}/capture/refresh", timeout=30)
            body = r.json()
            if body.get("success") and body.get("data", {}).get("local_persisted"):
                ok_cnt += 1
                print(f"  [{i}/{len(targets)}] ✅ {eid} 已持久化本地")
            else:
                print(f"  [{i}/{len(targets)}] ❌ {eid} {body.get('message','')[:80]}")
        except Exception as ex:
            print(f"  [{i}/{len(targets)}] ❌ {eid} 请求异常: {str(ex)[:80]}")
        time.sleep(args.delay)

    print(f"\n完成: 成功 {ok_cnt}/{len(targets)}")


if __name__ == "__main__":
    sys.exit(main())
