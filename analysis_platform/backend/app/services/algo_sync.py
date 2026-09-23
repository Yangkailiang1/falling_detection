"""
# 算法迭代平台实时同步钩子 [2026-08-12]
# 功能: 客户侧事件进入终态(归档/取消/误报)时，脱敏后实时推送到算法迭代平台
# 原则: 只上传骨骼数据 + 跌倒相关分析；绝不上传实时画面/设备序列号/家庭位置
# 配置(analysis_platform/.env):
#   ALGO_PLATFORM_URL    算法平台地址，如 http://localhost:5003（未配置则同步禁用）
#   ALGO_API_KEY         算法平台写入 Key（未设置时不发送同步请求）
#   ALGO_SOURCE_SALT     站点别名加盐（与算法平台一致，保证 event_id 稳定去重）
#   ALGO_SYNC_ENABLED    总开关，默认 false（需 URL、API Key 均已配置）
#   ALGO_ATTACH_SKELETON 是否附加合成骨骼序列（真实归档无关键点），默认 true
#
# 实现说明: 脱敏/骨骼生成复用算法平台的实现（同一机器兄弟目录），
#   通过 importlib 按文件路径加载，避免把 algorithm_platform 加入 sys.path
#   （两项目都有 app 包，直接加路径会互相遮蔽）。
"""
import importlib.util
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# 本项目根（analysis_platform/）与算法平台 backend 目录
_THIS_ROOT = Path(__file__).resolve().parents[3]           # .../analysis_platform
_ALGO_BACKEND = _THIS_ROOT.parent / 'algorithm_platform' / 'backend'

# 环境变量
load_dotenv(_THIS_ROOT / '.env')
ALGO_PLATFORM_URL = os.getenv('ALGO_PLATFORM_URL', '').rstrip('/')
ALGO_API_KEY = os.getenv('ALGO_API_KEY', '')
ALGO_SOURCE_SALT = os.getenv('ALGO_SOURCE_SALT', '')
ALGO_SYNC_ENABLED = os.getenv('ALGO_SYNC_ENABLED', 'false').lower() in ('1', 'true', 'yes')
ALGO_ATTACH_SKELETON = os.getenv('ALGO_ATTACH_SKELETON', 'true').lower() in ('1', 'true', 'yes')

# 只有进入这些终态的事件才推送（避免问询中间态上传半成品）
TERMINAL_STATUSES = {'archived', 'voice_cancelled', 'false_alarm', 'notified'}


def _load_module_by_path(rel_path: str, alias: str):
    """按文件路径加载算法平台模块（不加入 sys.path，避免 app 包遮蔽）"""
    path = _ALGO_BACKEND / rel_path
    if not path.exists():
        logger.warning('算法平台同步: 模块缺失 %s, 相关能力禁用', path)
        return None
    spec = importlib.util.spec_from_file_location(alias, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_anonymize_mod = _load_module_by_path('app/services/anonymize.py', '_algo_anonymize')
_skeleton_mod = _load_module_by_path('app/services/skeleton_gen.py', '_algo_skeleton_gen')

# 仅在显式启用且 URL、API Key 与脱敏模块均就绪时运行
SYNC_ENABLED = ALGO_SYNC_ENABLED and bool(ALGO_PLATFORM_URL) and bool(ALGO_API_KEY) and _anonymize_mod is not None


def enabled() -> bool:
    return SYNC_ENABLED


def push_event(record) -> bool:
    """把一条归档记录脱敏后推送到算法平台。record 为 FallEventRecord 或 dict。
    返回是否成功（HTTP 200；重复推送也算成功——服务端按 event_id 去重）"""
    if not SYNC_ENABLED:
        return False
    try:
        import requests
        raw = asdict(record) if hasattr(record, 'event_id') else record
        ev = _anonymize_mod.anonymize_record(raw, salt=ALGO_SOURCE_SALT)

        # 附加骨骼序列: 真实归档有真实骨骼(v2.2 收集)则用真实, 否则合成 [2026-08-13]
        if ALGO_ATTACH_SKELETON:
            real_seq = (raw.get('skeleton_sequence') or {}) if isinstance(raw, dict) else {}
            if real_seq.get('origin') == 'real' and real_seq.get('keypoints'):
                ev['skeleton_sequence'] = real_seq
                ev['privacy']['synthetic_skeleton'] = False
            elif _skeleton_mod is not None:
                seq = _skeleton_mod.synthetic_skeleton_for_event(
                    touch_ground_part=ev['skeleton_analysis']['touch_ground_part'],
                    fall_direction=ev['skeleton_analysis']['fall_direction'],
                    is_fall=ev['ground_truth']['is_fall'],
                )
                ev['skeleton_sequence'] = seq
                ev['privacy']['synthetic_skeleton'] = True

        resp = requests.post(
            f'{ALGO_PLATFORM_URL}/api/ingest/events',
            headers={'Content-Type': 'application/json', 'X-API-Key': ALGO_API_KEY},
            json={'source_id': ev['source_id'], 'events': [ev]},
            timeout=5,
        )
        if resp.status_code != 200:
            logger.error('算法平台推送失败 HTTP %s: %s', resp.status_code, resp.text[:200])
            return False
        return True
    except Exception as e:
        logger.warning('算法平台推送异常: %s', e)
        return False
