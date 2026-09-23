<template>
  <div class="events-page">
    <div class="page-header">
      <h2>事件浏览</h2>
      <el-button @click="loadEvents" :loading="loading">
        <el-icon><Refresh /></el-icon>
        刷新
      </el-button>
    </div>

    <!-- 筛选栏 -->
    <div class="filter-bar page-card">
      <el-select v-model="filters.risk_level" placeholder="风险等级" clearable style="width:150px" @change="loadEvents">
        <el-option label="I级 高危" value="I" />
        <el-option label="II级 中危" value="II" />
        <el-option label="III级 低危" value="III" />
      </el-select>
      <el-select v-model="filters.source_id" placeholder="来源站点" clearable filterable style="width:160px" @change="loadEvents">
        <el-option v-for="(c, src) in sourceOptions" :key="src" :label="`${src} (${c})`" :value="src" />
      </el-select>
      <el-select v-model="filters.is_fall" placeholder="真实/误报" clearable style="width:130px" @change="loadEvents">
        <el-option label="真实跌倒" :value="true" />
        <el-option label="误报" :value="false" />
      </el-select>
      <el-select v-model="filters.status" placeholder="状态" clearable style="width:140px" @change="loadEvents">
        <el-option label="已归档" value="archived" />
        <el-option label="问询中" value="inquiring" />
        <el-option label="语音取消" value="voice_cancelled" />
        <el-option label="误报" value="false_alarm" />
      </el-select>
      <el-select v-model="filters.has_skeleton" placeholder="骨骼序列" clearable style="width:130px" @change="loadEvents">
        <el-option label="含骨骼" :value="true" />
      </el-select>
      <el-date-picker
        v-model="dateRange" type="daterange" value-format="YYYY-MM-DD"
        range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期"
        style="width:260px" @change="onDateChange"
      />
      <el-input v-model="filters.q" placeholder="搜索事件ID/来源" clearable style="width:190px"
                @keyup.enter="loadEvents" @clear="loadEvents">
        <template #append>
          <el-button @click="loadEvents"><el-icon><Search /></el-icon></el-button>
        </template>
      </el-input>
    </div>

    <!-- 事件表格 -->
    <div class="page-card">
      <el-table :data="events" v-loading="loading" stripe @row-click="goDetail" style="cursor:pointer">
        <el-table-column label="事件ID" width="150">
          <template #default="{ row }">
            <span class="event-id">{{ row.event_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="120">
          <template #default="{ row }">
            <el-tag size="small" type="info" effect="plain">{{ row.source_id }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="场景" min-width="150">
          <template #default="{ row }">{{ row.context?.scenario_name || '-' }}</template>
        </el-table-column>
        <el-table-column label="风险" width="110" align="center">
          <template #default="{ row }">
            <el-tag :type="riskTagType(row.risk?.level)" effect="dark">{{ row.risk?.level }}级 {{ row.risk?.level_name }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="触地部位" width="90" align="center">
          <template #default="{ row }">{{ partLabel(row.skeleton_analysis?.touch_ground_part) }}</template>
        </el-table-column>
        <el-table-column label="真实/误报" width="100" align="center">
          <template #default="{ row }">
            <el-tag :type="row.ground_truth?.is_fall ? 'danger' : 'success'" size="small">
              {{ row.ground_truth?.is_fall ? '真实跌倒' : '误报' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="置信度" width="110" align="center">
          <template #default="{ row }">
            <el-progress :percentage="Math.round((row.detection?.confidence || 0) * 100)" :stroke-width="8"
                         :color="(row.detection?.confidence || 0) > 0.9 ? '#67c23a' : '#e6a23c'" />
          </template>
        </el-table-column>
        <el-table-column label="骨骼" width="80" align="center">
          <template #default="{ row }">
            <el-icon v-if="row.skeleton_sequence" color="#409eff"><Checked /></el-icon>
            <el-icon v-else color="#c0c4cc"><CircleClose /></el-icon>
          </template>
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

      <div class="pagination-wrap">
        <el-pagination
          v-model:current-page="page" :page-size="pageSize" :total="total"
          layout="total, prev, pager, next, jumper" @current-change="loadEvents"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { eventsApi } from '@/api/events'
import { dashboardApi } from '@/api/dashboard'

const router = useRouter()
const loading = ref(false)
const events = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const dateRange = ref(null)
const sourceOptions = ref({})

const filters = reactive({
  risk_level: null, source_id: null, is_fall: null,
  status: null, has_skeleton: null, q: ''
})

const riskTagType = l => ({ I: 'danger', II: 'warning', III: 'info' }[l] || 'info')
const partLabel = p => ({ head: '头部', spine: '脊柱', hip: '髋部', shoulder: '肩部',
  hand: '手部', elbow: '肘部', knee: '膝部' }[p] || p || '-')
const statusLabel = s => ({ archived: '已归档', inquiring: '问询中', voice_cancelled: '语音取消',
  false_alarm: '误报', notified: '已通知' }[s] || s || '-')
const statusTagType = s => ({ archived: 'success', inquiring: 'warning', voice_cancelled: 'info',
  false_alarm: 'danger' }[s] || 'info')
const formatTime = t => t ? new Date(t).toLocaleString('zh-CN', { hour12: false }) : '-'

async function loadSourceOptions() {
  try {
    const res = await dashboardApi.getStats()
    sourceOptions.value = res.data.by_source || {}
  } catch { /* ignore */ }
}

async function loadEvents() {
  loading.value = true
  try {
    const params = {
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
      ...filters
    }
    if (filters.is_fall === null) delete params.is_fall
    if (filters.has_skeleton === null) delete params.has_skeleton
    const res = await eventsApi.getList(params)
    events.value = res.data.items
    total.value = res.data.total
  } finally {
    loading.value = false
  }
}

function onDateChange() {
  filters.date_from = dateRange.value?.[0] ? dateRange.value[0] + 'T00:00:00' : null
  filters.date_to = dateRange.value?.[1] ? dateRange.value[1] + 'T23:59:59' : null
  page.value = 1
  loadEvents()
}

function goDetail(row) {
  router.push(`/events/${row.event_id}`)
}

onMounted(() => {
  loadSourceOptions()
  loadEvents()
})
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-header h2 { font-size: 20px; color: #303133; }
.filter-bar { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 16px; padding: 14px; }
.event-id { font-family: 'JetBrains Mono', Consolas, monospace; font-size: 12px; }
.pagination-wrap { margin-top: 14px; display: flex; justify-content: flex-end; }
</style>
