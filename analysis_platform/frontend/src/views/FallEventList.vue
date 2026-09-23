<template>
  <div class="fall-events-page">
    <!-- 页面标题和操作栏 -->
    <div class="page-header">
      <h2>跌倒事件中心</h2>
      <div class="header-actions">
        <el-button type="primary" @click="showSimulateDialog = true">
          <el-icon><VideoPlay /></el-icon>
          模拟跌倒事件
        </el-button>
        <el-button @click="loadEvents">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stat-row" v-loading="statsLoading">
      <el-col :span="6" v-for="card in statCards" :key="card.label">
        <div class="stat-card" :class="card.cssClass" @click="card.onClick && card.onClick()">
          <div class="stat-number">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- 筛选栏 -->
    <div class="filter-bar">
      <el-select v-model="filterRisk" placeholder="风险等级" clearable style="width:140px" @change="loadEvents">
        <el-option label="I级 高危" value="I" />
        <el-option label="II级 中危" value="II" />
        <el-option label="III级 低危" value="III" />
      </el-select>
      <el-select v-model="filterStatus" placeholder="事件状态" clearable style="width:140px" @change="loadEvents">
        <el-option label="已归档" value="archived" />
        <el-option label="已通知" value="notified" />
        <el-option label="已报告" value="reported" />
        <el-option label="已分析" value="analyzed" />
        <el-option label="误报" value="false_alarm" />
      </el-select>
    </div>

    <!-- 事件列表 -->
    <el-table :data="events" v-loading="loading" stripe @row-click="goDetail" style="cursor:pointer">
      <el-table-column label="事件ID" width="110">
        <template #default="{ row }">
          <span class="event-id">{{ row.event_id }}</span>
        </template>
      </el-table-column>
      <el-table-column label="场景" min-width="180">
        <template #default="{ row }">
          <div class="scenario-cell">
            <span class="scenario-name">{{ row.context?.scenario_name || row.context?.description }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="风险等级" width="120" align="center">
        <template #default="{ row }">
          <el-tag :type="riskTagType(row.risk?.level)" size="large" effect="dark">
            {{ row.risk?.level }}级 {{ row.risk?.level_name }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="触地部位" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="partTagType(row.skeleton_analysis?.touch_ground_part)">
            {{ partLabel(row.skeleton_analysis?.touch_ground_part) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="置信度" width="100" align="center">
        <template #default="{ row }">
          <el-progress
            :percentage="Math.round((row.detection?.confidence || 0) * 100)"
            :color="row.detection?.confidence > 0.9 ? '#67c23a' : '#e6a23c'"
            :stroke-width="8"
          />
        </template>
      </el-table-column>
      <el-table-column label="位置" width="100" align="center">
        <template #default="{ row }">{{ row.context?.location }}</template>
      </el-table-column>
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)" size="small">{{ statusLabel(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="170">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
    </el-table>

    <!-- 分页 -->
    <div class="pagination-wrap">
      <el-pagination
        v-model:current-page="page"
        :page-size="pageSize"
        :total="total"
        layout="total, prev, pager, next"
        @current-change="loadEvents"
      />
    </div>

    <!-- 模拟跌倒对话窗 -->
    <el-dialog v-model="showSimulateDialog" title="模拟跌倒事件" width="600px">
      <div class="simulate-form">
        <el-alert
          title="提示"
          type="info"
          description="从下方选择一个预定义场景，或自定义参数。系统将模拟完整的检测→分析→报告→干预→归档流程。"
          :closable="false"
          show-icon
          style="margin-bottom:20px"
        />

        <el-radio-group v-model="simMode" class="mode-select">
          <el-radio value="preset">预设场景</el-radio>
          <el-radio value="custom">自定义参数</el-radio>
        </el-radio-group>

        <!-- 预设场景 -->
        <div v-if="simMode === 'preset'" class="scenario-grid">
          <div
            v-for="s in scenarios"
            :key="s.key"
            class="scenario-card"
            :class="{ selected: selectedScenario === s.key }"
            @click="selectedScenario = s.key"
          >
            <div class="card-risk">
              <el-tag :type="riskTagType(s.risk_level_expected)" size="small">
                {{ s.risk_level_expected }}级
              </el-tag>
            </div>
            <div class="card-name">{{ s.name }}</div>
            <div class="card-desc">{{ s.description }}</div>
            <div class="card-location">
              <el-icon><Location /></el-icon> {{ s.location }}
            </div>
          </div>
        </div>

        <!-- 自定义参数 -->
        <div v-else class="custom-form">
          <el-form label-width="100px" size="default">
            <el-form-item label="触地部位">
              <el-select v-model="customParams.touch_ground_part" style="width:100%">
                <el-option v-for="r in riskLevels" :key="r.key" :label="`${r.label} (${r.level}级)`" :value="r.key" />
              </el-select>
            </el-form-item>
            <el-form-item label="跌倒方向">
              <el-select v-model="customParams.fall_direction" style="width:100%">
                <el-option v-for="(v,k) in fallDirections" :key="k" :label="v" :value="k" />
              </el-select>
            </el-form-item>
            <el-form-item label="冲击速度">
              <el-slider v-model="customParams.impact_velocity" :min="0.5" :max="8" :step="0.1" show-input />
            </el-form-item>
            <el-form-item label="检测置信度">
              <el-slider v-model="customParams.detection_confidence" :min="0.5" :max="1.0" :step="0.01" show-input />
            </el-form-item>
            <el-form-item label="发生地点">
              <el-input v-model="customParams.location" placeholder="客厅" />
            </el-form-item>
          </el-form>
        </div>
      </div>

      <template #footer>
        <el-button @click="showSimulateDialog = false">取消</el-button>
        <el-button type="primary" @click="doSimulate" :loading="simulating">
          <el-icon><CaretRight /></el-icon>
          模拟跌倒
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { VideoPlay, Refresh, Location, CaretRight } from '@element-plus/icons-vue'
import { fallEventApi } from '@/api/fallEvents'

const router = useRouter()
const events = ref([])
const loading = ref(false)
const statsLoading = ref(false)
const simulating = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = 20
const filterRisk = ref('')
const filterStatus = ref('')

// 模拟对话框
const showSimulateDialog = ref(false)
const simMode = ref('preset')
const selectedScenario = ref('')
const scenarios = ref([])
const riskLevels = ref([])
const fallDirections = ref({})
const customParams = reactive({
  touch_ground_part: 'hip',
  fall_direction: 'sideways_right',
  impact_velocity: 3.0,
  detection_confidence: 0.90,
  location: '客厅'
})

// 统计
const stats = ref({ total_events: 0, by_risk_level: {}, false_alarms: 0 })

const statCards = computed(() => [
  { label: '事件总数', value: stats.value.total_events, cssClass: 'total', onClick: () => { filterRisk.value = ''; filterStatus.value = ''; loadEvents() } },
  { label: 'I级高危', value: stats.value.by_risk_level?.I || 0, cssClass: 'risk-i', onClick: () => { filterRisk.value = 'I'; filterStatus.value = ''; loadEvents() } },
  { label: 'II级中危', value: stats.value.by_risk_level?.II || 0, cssClass: 'risk-ii', onClick: () => { filterRisk.value = 'II'; filterStatus.value = ''; loadEvents() } },
  { label: 'III级低危', value: stats.value.by_risk_level?.III || 0, cssClass: 'risk-iii', onClick: () => { filterRisk.value = 'III'; filterStatus.value = ''; loadEvents() } },
])

// 工具函数
const riskTagType = (level) => ({ I: 'danger', II: 'warning', III: 'success' }[level] || 'info')
const partTagType = (part) => ({ head: 'danger', spine: 'danger', hip: 'warning', shoulder: 'warning', hand: 'success', elbow: 'success', knee: 'success' }[part] || 'info')
const partLabel = (p) => ({ head: '头部', spine: '脊柱', hip: '髋部', shoulder: '肩部', hand: '手部', elbow: '肘部', knee: '膝部' }[p] || p)
const statusTagType = (s) => ({ archived: 'success', notified: 'primary', reported: 'warning', analyzed: 'info', detected: '', inquiring: 'warning', voice_cancelled: 'info', false_alarm: 'danger' }[s] || 'info')
const statusLabel = (s) => ({ archived: '已归档', notified: '已通知', reported: '已报告', analyzed: '已分析', detected: '检测到', inquiring: '问询中', voice_cancelled: '已取消', false_alarm: '误报' }[s] || s)
const formatTime = (t) => t ? new Date(t).toLocaleString('zh-CN') : '-'

async function loadEvents() {
  loading.value = true
  try {
    const res = await fallEventApi.getList({
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      risk_level: filterRisk.value || undefined,
      status: filterStatus.value || undefined,
      days: 30
    })
    events.value = res.data?.items || []
    total.value = res.data?.total || 0
  } catch { events.value = [] }
  loading.value = false
}

async function loadStats() {
  statsLoading.value = true
  try {
    const res = await fallEventApi.getStats()
    stats.value = res.data || {}
  } catch {}
  statsLoading.value = false
}

async function loadScenarios() {
  try {
    const res = await fallEventApi.getScenarios()
    scenarios.value = res.data?.scenarios || []
    fallDirections.value = res.data?.fall_directions || {}
    // 构建风险等级列表
    const rl = res.data?.risk_levels || {}
    riskLevels.value = Object.entries(rl).map(([k, v]) => ({
      key: k,
      label: partLabel(k),
      level: v.level,
      name: v.name,
    }))
  } catch {}
}

function goDetail(row) {
  router.push(`/fall-events/${row.event_id}`)
}

async function doSimulate() {
  simulating.value = true
  try {
    if (simMode.value === 'preset') {
      if (!selectedScenario.value) {
        ElMessage.warning('请选择一个场景')
        simulating.value = false
        return
      }
      await fallEventApi.quickSimulate(selectedScenario.value)
    } else {
      await fallEventApi.simulate({
        scenario_key: 'custom',
        ...customParams
      })
    }
    ElMessage.success('跌倒事件模拟完成')
    showSimulateDialog.value = false
    selectedScenario.value = ''
    await loadEvents()
    await loadStats()
  } catch (e) {
    ElMessage.error('模拟失败: ' + (e.response?.data?.message || e.message))
  }
  simulating.value = false
}

onMounted(() => {
  loadEvents()
  loadStats()
  loadScenarios()
})
</script>

<style scoped>
.fall-events-page { padding: 0; }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 20px; color: #303133; }
.header-actions { display: flex; gap: 8px; }

.stat-row { margin-bottom: 16px; }
.stat-card {
  padding: 20px; border-radius: 8px; text-align: center; cursor: pointer;
  transition: transform 0.2s;
}
.stat-card:hover { transform: translateY(-2px); }
.stat-card.total { background: #f0f9ff; border: 1px solid #bae6fd; }
.stat-card.risk-i { background: #fef2f2; border: 1px solid #fecaca; }
.stat-card.risk-ii { background: #fffbeb; border: 1px solid #fde68a; }
.stat-card.risk-iii { background: #f0fdf4; border: 1px solid #bbf7d0; }
.stat-number { font-size: 28px; font-weight: bold; color: #303133; }
.stat-label { font-size: 13px; color: #909399; margin-top: 4px; }

.filter-bar { display: flex; gap: 12px; margin-bottom: 16px; }

.event-id { font-family: monospace; font-size: 13px; color: #409eff; }
.scenario-cell { line-height: 1.4; }
.scenario-name { font-weight: 500; }
.scenario-desc { font-size: 12px; color: #909399; display: block; }

.velocity { font-family: monospace; font-weight: 500; }

.pagination-wrap { display: flex; justify-content: flex-end; margin-top: 16px; }

/* 模拟对话框 */
.mode-select { display: flex; gap: 16px; margin-bottom: 20px; }
.scenario-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; max-height: 400px; overflow-y: auto; }
.scenario-card {
  border: 2px solid #e4e7ed; border-radius: 8px; padding: 14px; cursor: pointer;
  transition: all 0.2s;
}
.scenario-card:hover { border-color: #409eff; }
.scenario-card.selected { border-color: #409eff; background: #ecf5ff; }
.card-risk { margin-bottom: 6px; }
.card-name { font-weight: 600; font-size: 14px; margin-bottom: 4px; }
.card-desc { font-size: 12px; color: #909399; line-height: 1.4; margin-bottom: 6px; }
.card-location { font-size: 12px; color: #606266; display: flex; align-items: center; gap: 4px; }
.custom-form { padding: 0 10px; }
</style>
