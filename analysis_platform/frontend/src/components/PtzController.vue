<!--
  对应文档: 四.V3.0 - 云台控制器组件
  功能: 提供上/下/左/右键云台控制面板
  对应文档章节: 三.3.2 - 云台控制功能
-->
<template>
  <div class="ptz-controller">
    <div class="ptz-title">云台控制</div>
    <div class="ptz-grid">
      <!-- 上 -->
      <div class="ptz-empty"></div>
      <el-button
        class="ptz-btn up"
        :icon="CaretTop"
        :disabled="moving"
        @mousedown="startMove('up')"
        @mouseup="stopMove"
        @mouseleave="stopMove"
      />
      <div class="ptz-empty"></div>

      <!-- 左中右 -->
      <el-button
        class="ptz-btn left"
        :icon="CaretLeft"
        :disabled="moving"
        @mousedown="startMove('left')"
        @mouseup="stopMove"
        @mouseleave="stopMove"
      />
      <div class="ptz-center">
        <el-tooltip content="停止转动" placement="top">
          <el-button
            class="ptz-btn stop"
            :icon="CircleCloseFilled"
            @click="stopMove"
          />
        </el-tooltip>
      </div>
      <el-button
        class="ptz-btn right"
        :icon="CaretRight"
        :disabled="moving"
        @mousedown="startMove('right')"
        @mouseup="stopMove"
        @mouseleave="stopMove"
      />

      <!-- 下 -->
      <div class="ptz-empty"></div>
      <el-button
        class="ptz-btn down"
        :icon="CaretBottom"
        :disabled="moving"
        @mousedown="startMove('down')"
        @mouseup="stopMove"
        @mouseleave="stopMove"
      />
      <div class="ptz-empty"></div>
    </div>

    <!-- 速度调节 -->
    <div class="ptz-speed">
      <span class="label">速度</span>
      <el-slider v-model="speed" :min="1" :max="5" :step="1" show-stops size="small" />
    </div>
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - PtzController组件]
import { ref, onBeforeUnmount } from 'vue'
import { CaretTop, CaretBottom, CaretLeft, CaretRight, CircleCloseFilled } from '@element-plus/icons-vue'
import { startPtz, stopPtz } from '@/api/ptz'
import { ElMessage } from 'element-plus'

const props = defineProps({
  deviceSerial: { type: String, required: true }
})

const speed = ref(3)
const moving = ref(false)
let stopTimer = null

// 开始云台转动
async function startMove(direction) {
  try {
    moving.value = true
    await startPtz(props.deviceSerial, direction, speed.value)
    // 设置自动停止（1秒后自动停，防止API状态异常）
    stopTimer = setTimeout(() => stopMove(), 1000)
  } catch {
    moving.value = false
    ElMessage.error('云台控制失败，请检查设备状态')
  }
}

// 停止云台转动
async function stopMove() {
  if (stopTimer) { clearTimeout(stopTimer); stopTimer = null }
  try {
    await stopPtz(props.deviceSerial)
  } catch {
    // 静默处理停止错误
  } finally {
    moving.value = false
  }
}

onBeforeUnmount(() => {
  if (stopTimer) clearTimeout(stopTimer)
})
</script>

<style scoped>
.ptz-controller {
  background: #f5f7fa;
  border-radius: 8px;
  padding: 16px;
  width: 200px;
}
.ptz-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  text-align: center;
  color: #606266;
}
.ptz-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  justify-items: center;
  margin-bottom: 12px;
}
.ptz-empty {
  width: 48px;
  height: 48px;
}
.ptz-btn {
  width: 48px !important;
  height: 48px !important;
  border-radius: 8px !important;
  font-size: 20px !important;
}
.ptz-btn.up { background: #ecf5ff; border-color: #409eff; color: #409eff; }
.ptz-btn.down { background: #ecf5ff; border-color: #409eff; color: #409eff; }
.ptz-btn.left { background: #ecf5ff; border-color: #409eff; color: #409eff; }
.ptz-btn.right { background: #ecf5ff; border-color: #409eff; color: #409eff; }
.ptz-btn.stop { background: #fef0f0; border-color: #f56c6c; color: #f56c6c; }
.ptz-center {
  display: flex;
  align-items: center;
  justify-content: center;
}
.ptz-speed {
  padding: 0 4px;
}
.ptz-speed .label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
  display: block;
}
</style>
