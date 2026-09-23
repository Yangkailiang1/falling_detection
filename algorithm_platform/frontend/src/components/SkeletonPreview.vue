<template>
  <div class="skeleton-preview" :style="{ maxWidth: width + 'px' }">
    <svg
      :viewBox="`0 0 ${canvasW} ${canvasH}`"
      class="skeleton-svg"
      preserveAspectRatio="xMidYMid meet"
    >
      <!-- 地面参考线 -->
      <line :x1="0" :y1="canvasH - 4" :x2="canvasW" :y2="canvasH - 4"
            stroke="#d9d9d9" stroke-dasharray="6,4" stroke-width="2" />

      <!-- 骨骼（肢连接） -->
      <polyline
        v-for="(bone, i) in bonePolylines"
        :key="'bone' + i"
        :points="bone"
        fill="none"
        stroke="#409eff"
        stroke-width="4"
        stroke-linecap="round"
        stroke-linejoin="round"
        opacity="0.85"
      />

      <!-- 关节点（透明度=置信度） -->
      <circle
        v-for="(kp, i) in renderKps"
        :key="'kp' + i"
        :cx="kp[0]"
        :cy="kp[1]"
        r="5"
        fill="#f56c6c"
        :opacity="Math.max(0.25, confidences[i] ?? 0.8)"
      />
    </svg>
    <div class="skeleton-caption">
      <el-tag size="small" type="info" effect="plain">
        COCO-17 骨骼 · {{ kpCount }} 关节点
      </el-tag>
      <span class="caption-text" v-if="caption">{{ caption }}</span>
    </div>
  </div>
</template>

<script setup>
// 算法迭代平台 - 单帧静态骨骼预览
// 功能: 将 COCO-17 关键点渲染为 SVG 骨骼图，兼容像素坐标和 0~1 归一化坐标
// props:
//   keypoints   - 17×2 像素坐标（可传 skeleton_sequence.keypoints 某一帧）
//   confidences - 17 置信度
//   caption     - 底部说明文字
import { computed } from 'vue'

const props = defineProps({
  keypoints: { type: Array, default: () => [] },
  confidences: { type: Array, default: () => [] },
  width: { type: Number, default: 360 },
  caption: { type: String, default: '' },
  coords: { type: String, default: '' },
  canvasW: { type: Number, default: 640 },
  canvasH: { type: Number, default: 480 }
})

// COCO-17 肢连接表（与后端 skeleton_gen.BONES 一致）
const BONES = [
  [0, 1], [0, 2], [1, 3], [2, 4],
  [5, 6], [5, 11], [6, 12], [11, 12],
  [5, 7], [7, 9], [6, 8], [8, 10],
  [11, 13], [13, 15], [12, 14], [14, 16]
]

const renderKps = computed(() => {
  const points = props.keypoints || []
  // 新版检测平台上传 normalized；兼容未显式携带 coords 的历史数据。
  const maxAbs = points.reduce((m, kp) => Math.max(m, Math.abs(Number(kp?.[0]) || 0), Math.abs(Number(kp?.[1]) || 0)), 0)
  const normalized = props.coords === 'normalized' || (!props.coords && maxAbs <= 2)
  return points.map(kp => {
    const x = (Number(kp?.[0]) || 0) * (normalized ? props.canvasW : 1)
    const y = (Number(kp?.[1]) || 0) * (normalized ? props.canvasH : 1)
    return [Math.round(x * 100) / 100, Math.round(y * 100) / 100]
  })
})

const kpCount = computed(() => renderKps.value.length)

const bonePolylines = computed(() => {
  const pts = renderKps.value
  if (!pts.length) return []
  return BONES
    .filter(([a, b]) => pts[a] && pts[b])
    .map(([a, b]) => `${pts[a][0]},${pts[a][1]} ${pts[b][0]},${pts[b][1]}`)
})
</script>

<style scoped>
.skeleton-preview {
  width: 100%;
}
.skeleton-svg {
  width: 100%;
  background: #fafafa;
  border: 1px solid #e4e7ed;
  border-radius: 6px;
}
.skeleton-caption {
  margin-top: 8px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.caption-text {
  font-size: 12px;
  color: #909399;
}
</style>
