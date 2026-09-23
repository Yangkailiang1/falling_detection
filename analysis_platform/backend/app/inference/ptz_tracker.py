"""
基于 YOLO 人形位置的云台自动追踪

独立线程轮询 world_av_pipeline 的人员检测结果：
  人偏离画面中心 → 决策方向 → start/stop 脉冲控制云台
  人回到中心死区 → 停止转动

关键设计：
- 独立线程，绝不阻塞 10fps 帧循环
- 每次决策间隔 ≥2.5s（云端 PTZ 命令延迟 2-4 秒）
- 每轮必发 stop_ptz_move（防止云台一直转 = "乱转"根因）
- 死区 ±15% 防抖动，无人不动作
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass, field

from app.services.ezviz_ptz import start_ptz_move, stop_ptz_move
from app.inference.inference_config import DEVICE_SERIAL, TEST_PHONE

logger = logging.getLogger(__name__)

SCAN_DIRECTIONS = ["left", "right", "up", "down"]


@dataclass
class PTZTrackerConfig:
    device_serial: str = DEVICE_SERIAL
    interval: float = 2.5            # 每次决策间隔 (s)
    deadzone: float = 0.15           # 死区：中心 ±15% 宽/高内不动作
    base_duration_ms: int = 250      # 基准转动时长 (ms)
    duration_per_offset: int = 400   # 每 10% 偏移增加的时长 (ms)
    speed: int = 2                   # 云台速度 0-5
    min_duration_ms: int = 150       # 最小转动时长，避免过短脉冲无效
    # --- 扫描模式 (场景无人时周期性自主转动寻找) ---
    scan_enabled: bool = True        # 是否启用无人扫描
    scan_interval: float = 4.0       # 扫描脉冲间隔 (s)
    scan_duration_ms: int = 500      # 每次扫描转动时长 (ms)
    lost_frames_to_scan: int = 2     # 连续 N 次决策无人 → 进入扫描
    limit_hit_pause: float = 6.0     # 云台到限位后暂停 (s) 再反向扫描


class PTZTracker:
    """独立线程：轮询 YOLO 人员位置 → 决策 → start/stop 脉冲控制云台"""

    def __init__(self, config: PTZTrackerConfig | None = None):
        self.config = config or PTZTrackerConfig()
        self._running = False
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        # 状态记录（供 status API 查询）
        self.last_decision: str = "idle"      # idle / left / right / up / down / scan-left / scan-right
        self.state: str = "idle"              # idle / tracking / scanning
        self.last_target_area: int = 0        # 追踪目标的框面积
        self.last_action_time: float = 0.0
        self.command_count: int = 0           # 累计发命令次数
        self.last_error: str = ""
        # 状态机内部变量
        self._no_person_count: int = 0        # 连续无人次数
        self._scan_direction: str = "left"    # 最近扫描方向
        self._limit_hit: bool = False         # 云台到达限位标记（下轮强制反向）

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """启动追踪线程（单例保护，重复调用直接返回）。"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._thread = threading.Thread(target=self._loop, daemon=True, name="ptz-tracker")
            self._thread.start()
            logger.info(f"PTZ tracker started (device={self.config.device_serial}, interval={self.config.interval}s)")
            return True

    def stop(self) -> bool:
        """停止追踪线程，并立即停住云台。"""
        with self._lock:
            if not self._running:
                return False
            self._running = False
        # 停止线程前先停云台，防止云台继续转动
        try:
            stop_ptz_move(self.config.device_serial)
        except Exception as e:
            logger.warning(f"PTZ stop-on-exit failed: {e}")
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        self.last_decision = "idle"
        self.state = "idle"
        logger.info("PTZ tracker stopped")
        return True

    def status(self) -> dict:
        """返回追踪状态（供 API / 前端轮询）。"""
        return {
            "running": self._running,
            "state": self.state,                 # idle / tracking / scanning
            "device_serial": self.config.device_serial,
            "last_decision": self.last_decision,
            "last_target_area": self.last_target_area,
            "last_action_time": round(self.last_action_time, 1),
            "command_count": self.command_count,
            "last_error": self.last_error,
            "interval": self.config.interval,
            "deadzone": self.config.deadzone,
            "scan_enabled": self.config.scan_enabled,
        }

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------

    def _loop(self):
        while self._running:
            t0 = time.time()
            try:
                self._decision_cycle()
            except Exception as e:
                self.last_error = str(e)[:200]
                logger.error(f"PTZ tracker cycle error: {e}")
            elapsed = time.time() - t0
            # 扫描模式用更长间隔（周期性寻找），追踪模式用短间隔（跟人）
            base = self.config.scan_interval if self.state == "scanning" else self.config.interval
            sleep_s = max(0.2, base - elapsed)
            time.sleep(sleep_s)

    def _decision_cycle(self):
        """一次决策：读人员位置 → 判断偏移 → 脉冲转动。

        状态机:
            idle     → 检测到人 → tracking
            tracking → 连续无人 N 次 → scanning
            scanning → 检测到人 → tracking
        """
        from app.inference.world_av_pipeline import get_tracking_snapshot

        snapshot = get_tracking_snapshot()
        if not snapshot:
            return
        persons, frame_w, frame_h = snapshot

        # ---- 无人分支：周期性扫描寻找 ----
        if not persons or frame_w <= 0 or frame_h <= 0:
            self._no_person_count += 1
            if self.config.scan_enabled and self._no_person_count >= self.config.lost_frames_to_scan:
                self._scan_cycle()
            else:
                self.state = "idle"
                self.last_decision = "idle"
            return

        # ---- 有人分支：追踪 ----
        self._no_person_count = 0

        # 追踪目标 = 面积最大的人（检测结果已按面积降序）
        target = persons[0]
        x1, y1, x2, y2 = target["bbox"]
        self.last_target_area = target.get("area", 0)

        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        dx = (cx - frame_w / 2.0) / frame_w   # -0.5..0.5, 负=偏左
        dy = (cy - frame_h / 2.0) / frame_h   # -0.5..0.5, 负=偏上

        dz = self.config.deadzone
        if abs(dx) <= dz and abs(dy) <= dz:
            self.state = "idle"
            self.last_decision = "idle"
            return

        # 决策方向：偏移大的轴优先（水平更有利于让人居中）
        if abs(dx) >= abs(dy):
            direction = "left" if dx < 0 else "right"
            offset = abs(dx)
        else:
            direction = "up" if dy < 0 else "down"
            offset = abs(dy)

        # 转动时长 ∝ 偏移量（超出死区部分，每 10% 增加 duration_per_offset ms）
        excess = max(0.0, offset - dz)
        duration = self.config.base_duration_ms + int(excess / 0.10) * self.config.duration_per_offset
        duration = max(self.config.min_duration_ms, min(duration, 1500))

        self._ptz_pulse(direction, duration)
        self.state = "tracking"
        logger.info(f"PTZ tracker: {direction} {duration}ms (dx={dx:.2f}, dy={dy:.2f})")

    def _scan_cycle(self):
        """扫描模式：随机方向脉冲转动，周期性寻找人物。

        策略:
            - 随机从 left/right/up/down 中选方向（避免固定交替到限位）
            - 云台到达限位后，下轮强制转反方向并暂停
        """
        if self._limit_hit:
            # 限位恢复：强制反方向转，让云台离开极限位置
            direction = "right" if self._scan_direction == "left" else "left"
            self._limit_hit = False
            logger.info(f"PTZ scan: reversing from limit ({direction})")
        else:
            direction = random.choice(SCAN_DIRECTIONS)
        self._scan_direction = direction

        ok = self._ptz_pulse(direction, self.config.scan_duration_ms)
        if not ok:
            # 云台到达限位（start 失败）→ 标记，下轮反向，并暂停让云台稳定
            self._limit_hit = True
            logger.warning(f"PTZ scan limit hit ({direction}); pausing {self.config.limit_hit_pause}s")
            time.sleep(self.config.limit_hit_pause)
        self.state = "scanning"
        self.last_decision = f"scan-{direction}"
        logger.info(f"PTZ tracker: scan {direction} ({self.config.scan_duration_ms}ms)")

    def _ptz_pulse(self, direction: str, duration_ms: int) -> bool:
        """start → 等 → stop 脉冲控制（必发 stop，防云台一直转）。

        Returns:
            True 成功；False 失败（如云台到达限位）。
        """
        try:
            start_ptz_move(self.config.device_serial, direction, self.config.speed)
        except Exception as e:
            self.last_error = str(e)[:200]
            logger.warning(f"PTZ start failed ({direction}): {e}")
            # 仍尝试 stop，防止半启动状态
            try:
                stop_ptz_move(self.config.device_serial)
            except Exception:
                pass
            return False
        self.command_count += 1
        self.last_action_time = time.time()
        time.sleep(max(self.config.min_duration_ms, duration_ms) / 1000.0)
        try:
            stop_ptz_move(self.config.device_serial)
        except Exception as e:
            logger.warning(f"PTZ stop failed: {e}")
        return True


# ---------------------------------------------------------------------------
# 模块级单例（与 world_av_pipeline 相同的模式）
# ---------------------------------------------------------------------------
_tracker: PTZTracker | None = None
_tracker_lock = threading.Lock()


def get_tracker(config: PTZTrackerConfig | None = None) -> PTZTracker:
    """获取全局单例追踪器。"""
    global _tracker
    if _tracker is None:
        with _tracker_lock:
            if _tracker is None:
                _tracker = PTZTracker(config)
    return _tracker


def start_tracking(device_serial: str | None = None) -> bool:
    """启动自动追踪（单例）。"""
    tracker = get_tracker()
    if device_serial:
        tracker.config.device_serial = device_serial
    return tracker.start()


def stop_tracking() -> bool:
    """停止自动追踪并停住云台。"""
    tracker = get_tracker()
    return tracker.stop()


def tracking_status() -> dict:
    """查询追踪状态。"""
    tracker = get_tracker()
    return tracker.status()
