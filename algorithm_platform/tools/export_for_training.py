#!/usr/bin/env python3
"""
# 算法迭代平台 - 训练数据集导出 CLI
# 功能: 从平台下载 s-jepa 训练数据集 zip，解压到目标目录
#       （keypoints/{fall,normal}/*.npy + split.json，与 Le2i 骨骼数据集同构）
# 用法:
#   python3 tools/export_for_training.py \
#     --api http://localhost:5003 --api-key <your-local-key> \
#     --out /mnt/d/project/s-jepa/le2i_keypoints --scene-prefix Home_01
# 说明: --scene-prefix 用于绕过 SkeletonDataset 的场景前缀限制（见 CLAUDE.md）
"""
import argparse
import io
import sys
import zipfile
from pathlib import Path

import requests


def main():
    parser = argparse.ArgumentParser(description='导出 s-jepa 训练数据集')
    parser.add_argument('--api', default='http://localhost:5003', help='平台地址')
    parser.add_argument('--api-key', default='', help='X-API-Key')
    parser.add_argument('--out', default='./falling_dataset', help='输出目录')
    parser.add_argument('--scene-prefix', default='', help='Le2i 场景前缀（如 Home_01），绕过加载限制')
    args = parser.parse_args()

    url = f'{args.api.rstrip("/")}/api/export/training'
    if args.scene_prefix:
        url += f'?scene_prefix={args.scene_prefix}'
    print(f'下载: {url}')
    resp = requests.get(url, headers={'X-API-Key': args.api_key}, timeout=120)
    if resp.status_code != 200:
        print(f'导出失败: HTTP {resp.status_code} {resp.text[:200]}', file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        zf.extractall(out_dir)

    npy = len(list(out_dir.rglob('*.npy')))
    print(f'解压完成 -> {out_dir}')
    print(f'  .npy 文件: {npy}')
    split = out_dir / 'split.json'
    if split.exists():
        print(f'  split.json: {split}')
    print('下一步: 在 s-jepa 中用 SkeletonDataset 加载（见 CLAUDE.md）')


if __name__ == '__main__':
    main()
