#!/usr/bin/env python3
"""
# 算法迭代平台 - 演示数据播种 CLI
# 功能: 生成本地脱敏演示事件并 POST 到平台（远程/托管部署也能播种）
# 用法:
#   python3 tools/seed_demo.py --api http://localhost:5003 --api-key <your-local-key> --count 40
# 说明: 生成的数据含合成骨骼序列；重复播种同 seed 会被去重（同 event_id）
"""
import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / 'backend'
sys.path.insert(0, str(BACKEND_DIR))

from app.services.demo_generator import generate_demo_events  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description='生成脱敏演示数据并上传')
    parser.add_argument('--api', default='http://localhost:5003', help='平台地址')
    parser.add_argument('--api-key', default='', help='X-API-Key')
    parser.add_argument('--count', type=int, default=40, help='生成条数')
    parser.add_argument('--seed', type=int, default=42, help='随机种子（同 seed 事件ID稳定）')
    parser.add_argument('--dry-run', action='store_true', help='只打印不发送')
    args = parser.parse_args()

    events = generate_demo_events(count=args.count, seed=args.seed)
    print(f'生成演示事件: {len(events)} 条 (seed={args.seed})')
    if args.dry_run:
        print('[DRY-RUN] 未发送。')
        return

    import requests
    resp = requests.post(
        f'{args.api.rstrip("/")}/api/ingest/events',
        headers={'Content-Type': 'application/json', 'X-API-Key': args.api_key},
        json={'source_id': 'seed', 'events': events},
        timeout=60,
    )
    resp.raise_for_status()
    print('结果:', resp.json()['data'])


if __name__ == '__main__':
    main()
