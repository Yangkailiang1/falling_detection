#!/usr/bin/env python3
"""
# 算法迭代平台 - 客户侧采集工具
# 功能: 读取客户侧分析平台的归档 JSON，逐条脱敏（只保留骨骼+跌倒分析），
#       分批发 POST 到算法迭代平台 /api/ingest/events
# 用法:
#   python3 tools/collector.py \
#     --archive <客户归档.json> \
#     --api http://localhost:5003 \
#     --api-key <your-local-key> \
#     --batch 50 --attach-skeletons --dry-run
# 注意: 客户侧代码不改动；本工具在算法迭代平台目录内运行
"""
import argparse
import json
import sys
from pathlib import Path

# 允许 import 算法迭代平台的后端 services（无需 Flask 环境）
BACKEND_DIR = Path(__file__).resolve().parent.parent / 'backend'
sys.path.insert(0, str(BACKEND_DIR))

from app.services.anonymize import anonymize_record, DROPPED_FIELDS  # noqa: E402
from config import Config  # noqa: E402  —— 读取平台配置的脱敏盐（保证与推送钩子 event_id 一致）


def load_archive(path: str):
    """读取客户侧归档 JSON（兼容 dict event_id→record 或 list）"""
    raw = json.loads(Path(path).read_text(encoding='utf-8'))
    if isinstance(raw, dict):
        return list(raw.values())
    if isinstance(raw, list):
        return raw
    raise ValueError(f'无法识别的归档格式: {type(raw)}')


def send_batch(api: str, api_key: str, batch: list, batch_id: str, source_id: str):
    """POST 一批脱敏事件到 ingest 接口"""
    import requests
    resp = requests.post(
        f'{api.rstrip("/")}/api/ingest/events',
        headers={'Content-Type': 'application/json', 'X-API-Key': api_key},
        json={'batch_id': batch_id, 'source_id': source_id, 'events': batch},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()['data']


def main():
    parser = argparse.ArgumentParser(description='客户侧脱敏事件采集上传')
    parser.add_argument('--archive', required=True, help='客户侧 fall_events_archive.json 路径')
    parser.add_argument('--api', default='http://localhost:5003', help='算法迭代平台地址')
    parser.add_argument('--api-key', default='', help='X-API-Key')
    parser.add_argument('--batch', type=int, default=50, help='每批事件数')
    parser.add_argument('--attach-skeletons', action='store_true',
                        help='附加合成骨骼序列（真实归档当前无关键点）')
    parser.add_argument('--source', default='auto',
                        help='来源站点ID；auto=按设备序列号自动派生脱敏别名')
    parser.add_argument('--salt', default=Config.ALGO_SOURCE_SALT,
                        help='source_id 加盐（默认取平台 .env 的 ALGO_SOURCE_SALT，与推送钩子一致）')
    parser.add_argument('--dry-run', action='store_true', help='只打印脱敏结果，不发送')
    parser.add_argument('--max', type=int, default=0, help='最多处理 N 条（0=全部）')
    args = parser.parse_args()

    records = load_archive(args.archive)
    if args.max:
        records = records[:args.max]
    print(f'读取归档: {len(records)} 条记录')

    events = []
    for i, rec in enumerate(records):
        if not isinstance(rec, dict) or not rec.get('event_id'):
            continue
        ev = anonymize_record(rec, salt=args.salt, attach_skeleton=args.attach_skeletons)
        if args.source and args.source != 'auto':
            ev['source_id'] = args.source
        events.append(ev)
    print(f'脱敏完成: {len(events)} 条（删除字段: {len(DROPPED_FIELDS)} 类）')

    if args.dry_run:
        print('\n[DRY-RUN] 前 2 条脱敏预览:')
        print(json.dumps(events[:2], ensure_ascii=False, indent=2)[:1500])
        print(f'\n[DRY-RUN] 未发送。去掉 --dry-run 以实际上传。')
        return

    # 分批上传
    total_ins = total_skip = total_err = 0
    for start in range(0, len(events), args.batch):
        batch = events[start:start + args.batch]
        batch_id = f'collector-{start // args.batch + 1}'
        try:
            data = send_batch(args.api, args.api_key, batch, batch_id,
                              events[start].get('source_id', ''))
            total_ins += data['inserted']
            total_skip += data['skipped_duplicates']
            total_err += data['errors']
            print(f"  批 {start//args.batch+1}: inserted={data['inserted']} "
                  f"skipped={data['skipped_duplicates']} errors={data['errors']}")
        except Exception as e:
            print(f'  批 {start//args.batch+1} 失败: {e}', file=sys.stderr)
            sys.exit(1)

    print(f'\n完成: 上传 {len(events)} 条，入库 {total_ins}，去重跳过 {total_skip}，错误 {total_err}')


if __name__ == '__main__':
    main()
