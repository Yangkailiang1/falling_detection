"""
# 算法迭代平台 - 演示数据生成器
# 功能: 生成确定性的脱敏演示事件（含合成骨骼序列），保证演示现场有数据
# 依赖: skeleton_gen（合成骨骼序列）；本模块自身不依赖 Flask
"""
import hashlib
import random
from datetime import datetime, timedelta, timezone

from app.services.skeleton_gen import synthetic_skeleton_for_event

# ── 8 种跌倒场景（对齐 analysis_platform FALL_SCENARIOS）────────────────────
SCENARIOS = {
    'head_forward':      {'touch': 'head',     'dir': 'forward',         'risk': 'I',   'name': '头部着地-向前跌倒'},
    'head_sideways':     {'touch': 'head',     'dir': 'sideways_left',   'risk': 'I',   'name': '头部着地-向侧跌倒'},
    'spine_backward':    {'touch': 'spine',    'dir': 'backward',        'risk': 'I',   'name': '脊柱着地-向后跌倒'},
    'hip_sideways':      {'touch': 'hip',      'dir': 'sideways_left',   'risk': 'II',  'name': '髋部着地-向侧跌倒'},
    'shoulder_forward':  {'touch': 'shoulder', 'dir': 'forward',         'risk': 'II',  'name': '肩部着地-向前跌倒'},
    'hand_forward':      {'touch': 'hand',     'dir': 'forward',         'risk': 'III', 'name': '手部着地-向前跌倒'},
    'elbow_sideways':    {'touch': 'elbow',    'dir': 'sideways_right',  'risk': 'III', 'name': '肘部着地-向侧跌倒'},
    'knee_forward':      {'touch': 'knee',     'dir': 'forward',         'risk': 'III', 'name': '膝部着地-向前跌倒'},
}

RISK_LEVELS = {'I': '高危', 'II': '中危', 'III': '低危'}
RISK_SCENARIOS = {
    'I':   ['head_forward', 'head_sideways', 'spine_backward'],
    'II':  ['hip_sideways', 'shoulder_forward'],
    'III': ['hand_forward', 'elbow_sideways', 'knee_forward'],
}
RISK_COUNTDOWN = {'I': 10, 'II': 30, 'III': 60}
RISK_INJURIES = {
    'I':   ['颅脑损伤', '颅内出血', '颈椎骨折'],
    'II':  ['髋部骨折', '肩关节损伤', '软组织挫伤'],
    'III': ['腕部扭伤', '肘部挫伤', '膝部擦伤'],
}
DEMO_SOURCES = ['src_demo_a', 'src_demo_b', 'src_demo_c']


def _medical_report(risk, scenario_name, impact):
    part = scenario_name.split('-')[0]
    if risk == 'I':
        return (
            f"老人跌倒，{part}着地，冲击速度约{impact:.1f}m/s，身体倾角较大，"
            "初步判断存在颅脑损伤风险。建议立即联系家属并送往医院做进一步检查。"
        )
    if risk == 'II':
        return (
            f"老人跌倒，{part}着地，冲击速度约{impact:.1f}m/s。"
            "存在髋部/关节损伤风险，建议家属关注老人状态，必要时就医。"
        )
    return (
        f"老人跌倒，{part}着地，冲击速度约{impact:.1f}m/s，冲击较轻。"
        "建议观察老人状态，若出现疼痛或行动不便应及时就医。"
    )


def generate_demo_events(count: int = 40, seed: int = 42, days_back: int = 30) -> list:
    """生成 count 条确定性脱敏演示事件（含骨骼序列）"""
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)
    events = []

    # 风险分配：I:20% / II:50% / III:30%（近似）
    risk_plan = (['I'] * int(count * 0.2) + ['II'] * int(count * 0.5) +
                 ['III'] * int(count * 0.3))
    while len(risk_plan) < count:
        risk_plan.append(rng.choice(['I', 'II', 'III']))
    rng.shuffle(risk_plan)
    risk_plan = risk_plan[:count]

    for i in range(count):
        risk = risk_plan[i]
        scenario_key = rng.choice(RISK_SCENARIOS[risk])
        sc = SCENARIOS[scenario_key]
        is_fall = rng.random() < 0.7

        source_id = DEMO_SOURCES[i % len(DEMO_SOURCES)]
        # 确定性事件ID（同 seed 同 i 稳定）
        event_id = 'algo_' + hashlib.md5(f'demo:{seed}:{i}'.encode()).hexdigest()[:10]
        # 日期落在 [0, days_back-1) 天内，保证 days_back 天趋势窗口全覆盖
        created_at = (now - timedelta(
            days=rng.uniform(0, max(1, days_back - 1)),
            hours=rng.uniform(0, 24),
        )).isoformat()

        confidence = round(rng.uniform(0.72, 0.98), 3)
        impact = round(rng.uniform({'I': 4.2, 'II': 3.0, 'III': 1.6}[risk],
                                   {'I': 6.5, 'II': 4.5, 'III': 3.2}[risk]), 2)
        tilt = round(rng.uniform({'I': 60, 'II': 50, 'III': 35}[risk],
                                 {'I': 90, 'II': 80, 'III': 70}[risk]), 1)
        com_vel = round(rng.uniform(1.2, 2.4), 2)

        # 语音问询结果
        if is_fall:
            voice = rng.choices(['pending', 'help_requested', 'timeout'],
                                weights=[0.4, 0.3, 0.3])[0]
            status = 'archived' if voice != 'pending' else 'inquiring'
            feedback = None
            is_false_alarm = False
        else:
            voice = 'cancelled'
            status = 'voice_cancelled'
            is_false_alarm = True
            feedback = {
                'is_false_alarm': True,
                'comment': rng.choice(['我没事', '只是蹲下捡东西', '碰了一下而已']),
                'reported_by': 'voice',
                'reported_at': created_at,
            }

        event = {
            'event_id': event_id,
            'source_id': source_id,
            'status': status,
            'created_at': created_at,
            'ingested_at': now.isoformat(),
            'ground_truth': {'is_fall': is_fall,
                             'label_origin': 'feedback' if feedback else ('voice' if voice != 'pending' else 'detection')},
            'detection': {
                'confidence': confidence,
                'latency_ms': round(rng.uniform(8, 30), 1),
                'video_window_frames': 16,
                'video_window_duration_s': 0.64,
            },
            'skeleton_analysis': {
                'touch_ground_part': sc['touch'],
                'fall_direction': sc['dir'],
                'impact_velocity': impact,
                'body_tilt_angle': tilt,
                'center_of_mass_velocity': com_vel,
            },
            'risk': {
                'level': risk,
                'level_name': RISK_LEVELS[risk],
                'likely_injury_types': list(RISK_INJURIES[risk]),
            },
            'medical_report': {
                'full_text': _medical_report(risk, sc['name'], impact),
                'recommendation': '立即联系家属' if risk == 'I' else ('家属关注' if risk == 'II' else '观察状态'),
            },
            'response': {
                'strategy': f"{RISK_COUNTDOWN[risk]}秒语音问询确认，无回应则自动紧急联络" if risk == 'I'
                            else (f"{RISK_COUNTDOWN[risk]}秒语音问询确认，无回应则紧急联络" if risk == 'II'
                                  else f"{RISK_COUNTDOWN[risk]}秒温和提示"),
                'countdown_seconds': RISK_COUNTDOWN[risk],
                'voice_confirm_status': voice,
            },
            'feedback': feedback,
            'context': {'scenario_key': scenario_key, 'scenario_name': sc['name']},
            'skeleton_sequence': synthetic_skeleton_for_event(
                touch_ground_part=sc['touch'],
                fall_direction=sc['dir'],
                is_fall=is_fall,
                seed=seed + i,
            ),
            'privacy': {
                'anonymized': True,
                'version': 1,
                'fields_removed': ['device_serial', 'location', 'description', 'capture_pic_url',
                                   'capture_time', 'capture_pic_path', 'video_clip',
                                   'notification_status', 'timeline_detail'],
                'timeline_summary': {'stages': ['detected', 'analyzed', 'reporting', 'inquiry', 'archived'],
                                     'total_duration_ms': rng.randint(1500, 4000)},
                'synthetic_skeleton': True,
            },
        }
        events.append(event)

    return events
