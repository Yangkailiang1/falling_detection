<template>
  <div class="detail-page" v-loading="loading">
    <div class="page-header">
      <el-button @click="$router.back()">
        <el-icon><ArrowLeft /></el-icon>
        返回
      </el-button>
      <span class="event-id-large">{{ event?.event_id }}</span>
      <el-tag :type="riskTagType(event?.risk?.level)" size="large" effect="dark">
        {{ event?.risk?.level }}级 {{ event?.risk?.level_name }}
      </el-tag>
    </div>

    <template v-if="event">
      <!-- 风险横幅 -->
      <div class="risk-banner" :class="'risk-' + (event.risk?.level || 'III').toLowerCase()">
        <div class="banner-left">
          <div class="banner-title">
            触地部位：{{ partLabel(event.skeleton_analysis?.touch_ground_part) }}
            <template v-if="reliableDirection"> · 跌倒方向：{{ reliableDirection }}</template>
          </div>
          <div class="banner-sub">身体倾角 {{ event.skeleton_analysis?.body_tilt_angle }}° · 时间 {{ formatTime(event.created_at) }}</div>
        </div>
        <div class="banner-right">
          <el-tag :type="event.ground_truth?.is_fall ? 'danger' : 'success'" size="large">
            {{ event.ground_truth?.is_fall ? '✓ 真实跌倒' : '✗ 误报' }}
          </el-tag>
        </div>
      </div>

      <el-row :gutter="16">
        <!-- 左列 -->
        <el-col :span="14">
          <div class="page-card">
            <h3 class="card-title">骨骼着地分析</h3>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="触地部位">{{ partLabel(event.skeleton_analysis?.touch_ground_part) }}</el-descriptions-item>
              <el-descriptions-item v-if="reliableDirection" label="跌倒方向">{{ reliableDirection }}</el-descriptions-item>
              <el-descriptions-item label="身体倾角">{{ event.skeleton_analysis?.body_tilt_angle }}°</el-descriptions-item>
              <el-descriptions-item label="潜在伤情">
                <el-tag v-for="t in event.risk?.likely_injury_types" :key="t" size="small" style="margin-right:4px">{{ t }}</el-tag>
              </el-descriptions-item>
            </el-descriptions>
          </div>

          <div class="page-card">
            <h3 class="card-title">检测参数</h3>
            <el-descriptions :column="2" border>
              <el-descriptions-item label="检测置信度">{{ event.detection?.confidence }}</el-descriptions-item>
              <el-descriptions-item label="推理延迟">{{ event.detection?.latency_ms }} ms</el-descriptions-item>
              <el-descriptions-item label="视频窗口">{{ event.detection?.video_window_frames }} 帧</el-descriptions-item>
              <el-descriptions-item label="窗口时长">{{ event.detection?.video_window_duration_s }} s</el-descriptions-item>
            </el-descriptions>
          </div>

          <div class="page-card">
            <h3 class="card-title">医疗简报</h3>
            <div class="report-text" v-html="renderMarkdown(event.medical_report?.full_text)"></div>
            <el-divider v-if="event.medical_report?.recommendation" />
            <el-alert v-if="event.medical_report?.recommendation" :title="'建议：' + event.medical_report.recommendation"
                      type="info" :closable="false" show-icon />
          </div>

          <div class="page-card">
            <h3 class="card-title">响应策略与反馈</h3>
            <el-descriptions :column="1" border>
              <el-descriptions-item label="响应策略">{{ event.response?.strategy || '-' }}</el-descriptions-item>
              <el-descriptions-item label="语音问询">{{ event.response?.voice_confirm_status }}
                <el-tag size="small" style="margin-left:6px">{{ event.response?.countdown_seconds }}s</el-tag></el-descriptions-item>
              <el-descriptions-item label="真值标签">
                <el-tag :type="event.ground_truth?.is_fall ? 'danger' : 'success'" size="small">
                  {{ event.ground_truth?.is_fall ? '真实跌倒' : '误报' }}
                </el-tag>
                <span class="muted" style="margin-left:8px">来源: {{ event.ground_truth?.label_origin }}</span>
              </el-descriptions-item>
              <el-descriptions-item v-if="event.feedback" label="用户反馈">
                {{ event.feedback.is_false_alarm ? '误报反馈：' : '确认跌倒：' }}{{ event.feedback.comment || '（无评论）' }}
              </el-descriptions-item>
            </el-descriptions>
          </div>
        </el-col>

        <!-- 右列 -->
        <el-col :span="10">
          <div class="page-card">
            <h3 class="card-title">骨骼序列
              <el-tag v-if="event.skeleton_sequence" size="small"
                      :type="event.skeleton_sequence.origin === 'synthetic' ? 'warning' : 'success'">
                {{ event.skeleton_sequence.origin === 'synthetic' ? '合成' : '真实' }}
              </el-tag>
            </h3>
            <template v-if="event.skeleton_sequence">
              <SkeletonPreview
                :keypoints="frameKeypoints"
                :confidences="frameConfidences"
                :coords="event.skeleton_sequence.coords"
                :caption="`第 ${previewFrameIndex + 1} / ${event.skeleton_sequence.n_frames} 帧（风险关键帧）`"
              />
              <div class="skeleton-meta">
                <el-descriptions :column="2" size="small">
                  <el-descriptions-item label="帧数">{{ event.skeleton_sequence.n_frames }}</el-descriptions-item>
                  <el-descriptions-item label="帧率">{{ event.skeleton_sequence.fps }} fps</el-descriptions-item>
                  <el-descriptions-item label="格式">{{ event.skeleton_sequence.format }}</el-descriptions-item>
                  <el-descriptions-item label="来源">{{ event.skeleton_sequence.origin }}</el-descriptions-item>
                </el-descriptions>
              </div>
            </template>
            <el-empty v-else description="该事件无骨骼序列数据" :image-size="60" />
            <el-alert type="warning" :closable="false" show-icon style="margin-top:10px"
                      title="仅上传骨骼关键点，不含任何实时画面/图像/视频" />
          </div>

          <div class="page-card">
            <h3 class="card-title">隐私审计</h3>
            <div class="audit-row">
              <span class="audit-label">已删除字段：</span>
              <el-tag v-for="f in event.privacy?.fields_removed" :key="f" size="small" type="info" effect="plain" style="margin:2px">
                {{ f }}
              </el-tag>
            </div>
            <div class="audit-row" v-if="event.privacy?.timeline_summary">
              <span class="audit-label">处理阶段：</span>
              <span class="muted">{{ (event.privacy.timeline_summary.stages || []).join(' → ') || '-' }}
                （{{ event.privacy.timeline_summary.total_duration_ms }} ms）</span>
            </div>
            <div class="audit-row">
              <span class="audit-label">脱敏标记：</span>
              <el-tag size="small" :type="event.privacy?.synthetic_skeleton ? 'warning' : 'success'">
                {{ event.privacy?.synthetic_skeleton ? '合成骨骼（演示数据）' : '真实分析数据' }}
              </el-tag>
            </div>
          </div>
        </el-col>
      </el-row>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { eventsApi } from '@/api/events'
import { renderMarkdown } from '@/utils/markdown'
import SkeletonPreview from '@/components/SkeletonPreview.vue'

const route = useRoute()
const loading = ref(false)
const event = ref(null)

const partLabel = p => ({ head: '头部', spine: '脊柱', hip: '髋部', shoulder: '肩部',
  hand: '手部', elbow: '肘部', knee: '膝部' }[p] || p || '-')
const dirLabel = d => ({ forward: '向前', backward: '向后', sideways_left: '向左侧',
  sideways_right: '向右侧', unknown: '未知' }[d] || d || '-')
const reliableDirection = computed(() => ({ forward: '向前', backward: '向后', sideways_left: '向左侧',
  sideways_right: '向右侧' }[event.value?.skeleton_analysis?.fall_direction] || ''))
const riskTagType = l => ({ I: 'danger', II: 'warning', III: 'info' }[l] || 'info')
const formatTime = t => t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '-'

// 优先取后端标记的跌倒帧；真实序列未标记时取末帧，避免固定展示动作尚未发生的第 1 帧。
const previewFrameIndex = computed(() => {
  const seq = event.value?.skeleton_sequence
  if (!seq?.keypoints?.length) return 0
  const marked = Number(seq.fall_frame_index)
  return Number.isInteger(marked) && marked >= 0 && marked < seq.keypoints.length
    ? marked
    : seq.keypoints.length - 1
})
const frameKeypoints = computed(() => {
  const seq = event.value?.skeleton_sequence
  if (!seq?.keypoints?.length) return []
  return seq.keypoints[previewFrameIndex.value] || seq.keypoints[0]
})
const frameConfidences = computed(() => {
  const seq = event.value?.skeleton_sequence
  if (!seq?.confidences?.length) return []
  return seq.confidences[previewFrameIndex.value] || seq.confidences[0]
})

onMounted(async () => {
  loading.value = true
  try {
    const res = await eventsApi.getDetail(route.params.eventId)
    event.value = res.data
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.page-header { display: flex; align-items: center; gap: 14px; margin-bottom: 16px; }
.event-id-large { font-family: monospace; font-size: 16px; font-weight: 600; }
.risk-banner {
  display: flex; align-items: center; justify-content: space-between;
  border-radius: 8px; padding: 18px 20px; margin-bottom: 16px; color: #fff;
}
.risk-i { background: linear-gradient(135deg, #f56c6c, #e64242); }
.risk-ii { background: linear-gradient(135deg, #e6a23c, #d98a1f); }
.risk-iii { background: linear-gradient(135deg, #67c23a, #4fa52c); }
.banner-title { font-size: 18px; font-weight: 700; }
.banner-sub { font-size: 13px; opacity: 0.92; margin-top: 6px; }
.card-title { font-size: 15px; color: #303133; margin-bottom: 12px; }
.report-text { line-height: 1.75; color: #303133; word-break: break-word; }
.report-text strong { color: #e64242; font-weight: 700; }
.report-text em { color: #606266; }
.report-text h1, .report-text h2, .report-text h3,
.report-text h4, .report-text h5, .report-text h6 {
  margin: 14px 0 6px; font-weight: 700; color: #303133;
}
.report-text h1 { font-size: 18px; }
.report-text h2 { font-size: 16px; }
.report-text h3, .report-text h4 { font-size: 15px; }
.report-text code {
  background: #f4f4f5; border-radius: 3px; padding: 1px 5px;
  font-size: 13px; color: #476582;
}
.audit-row { margin-bottom: 8px; }
.audit-label { font-size: 13px; color: #909399; }
.muted { color: #909399; font-size: 12px; }
.skeleton-meta { margin-top: 12px; }
</style>
