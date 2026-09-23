<template>
  <div class="dashboard">
    <div class="page-header">
      <h2>算法迭代 · 数据仪表盘</h2>
      <div class="header-actions">
        <el-button @click="loadAll" :loading="loading">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
      </div>
    </div>

    <!-- 统计卡片 -->
    <el-row :gutter="16" class="stat-row" v-loading="loading">
      <el-col :span="4" v-for="card in statCards" :key="card.label">
        <div class="stat-card" :class="card.cssClass">
          <div class="stat-number">{{ card.value }}</div>
          <div class="stat-label">{{ card.label }}</div>
        </div>
      </el-col>
    </el-row>

    <!-- 图表 -->
    <el-row :gutter="16">
      <el-col :span="10">
        <div class="chart-card">
          <h3 class="chart-title">风险等级分布</h3>
          <div ref="riskChartRef" class="chart"></div>
        </div>
      </el-col>
      <el-col :span="14">
        <div class="chart-card">
          <h3 class="chart-title">近 30 天跌倒事件趋势</h3>
          <div ref="trendChartRef" class="chart"></div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="16">
      <el-col :span="24">
        <div class="chart-card">
          <h3 class="chart-title">来源站点分布</h3>
          <div ref="sourceChartRef" class="chart chart-sm"></div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import { dashboardApi } from '@/api/dashboard'

const loading = ref(false)
const stats = ref(null)
const trend = ref([])

const riskChartRef = ref(null)
const trendChartRef = ref(null)
const sourceChartRef = ref(null)
let riskChart, trendChart, sourceChart

const statCards = computed(() => {
  const s = stats.value || {}
  return [
    { label: '总事件数', value: s.total_events ?? '-', cssClass: 'card-blue' },
    { label: '来源站点', value: s.total_sources ?? '-', cssClass: 'card-purple' },
    { label: '真实跌倒', value: s.by_is_fall?.falls ?? '-', cssClass: 'card-red' },
    { label: '误报', value: s.by_is_fall?.false_alarms ?? '-', cssClass: 'card-green' },
    { label: '误报率', value: s.false_alarm_rate != null ? (s.false_alarm_rate * 100).toFixed(1) + '%' : '-', cssClass: 'card-orange' },
    { label: '含骨骼序列', value: s.total_with_skeleton ?? '-', cssClass: 'card-cyan' }
  ]
})

function renderRisk() {
  const byRisk = stats.value?.by_risk_level || {}
  const levelNames = { I: 'I级 高危', II: 'II级 中危', III: 'III级 低危' }
  const data = Object.entries(byRisk).map(([level, count]) => ({
    name: levelNames[level] || level,
    value: count
  }))
  riskChart = riskChart || echarts.init(riskChartRef.value)
  riskChart.setOption({
    tooltip: { trigger: 'item' },
    legend: { bottom: 0 },
    series: [{
      type: 'pie',
      radius: ['45%', '68%'],
      center: ['50%', '45%'],
      itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
      data
    }]
  })
}

function renderTrend() {
  const dates = trend.value.map(d => d.date.slice(5))
  trendChart = trendChart || echarts.init(trendChartRef.value)
  trendChart.setOption({
    tooltip: { trigger: 'axis' },
    legend: { data: ['真实跌倒', '误报'] },
    grid: { left: 40, right: 20, top: 40, bottom: 30 },
    xAxis: { type: 'category', data: dates },
    yAxis: { type: 'value', minInterval: 1 },
    series: [
      { name: '真实跌倒', type: 'line', smooth: true, areaStyle: { opacity: 0.15 },
        data: trend.value.map(d => d.falls), itemStyle: { color: '#f56c6c' } },
      { name: '误报', type: 'line', smooth: true, areaStyle: { opacity: 0.15 },
        data: trend.value.map(d => d.false_alarms), itemStyle: { color: '#67c23a' } }
    ]
  })
}

function renderSource() {
  const bySource = stats.value?.by_source || {}
  const names = Object.keys(bySource)
  const counts = names.map(n => bySource[n])
  sourceChart = sourceChart || echarts.init(sourceChartRef.value)
  sourceChart.setOption({
    tooltip: { trigger: 'axis' },
    grid: { left: 80, right: 20, top: 20, bottom: 30 },
    xAxis: { type: 'category', data: names, axisLabel: { interval: 0, rotate: names.length > 6 ? 30 : 0 } },
    yAxis: { type: 'value', minInterval: 1 },
    series: [{
      type: 'bar',
      data: counts,
      barWidth: '45%',
      itemStyle: { color: '#409eff', borderRadius: [4, 4, 0, 0] }
    }]
  })
}

function resizeCharts() {
  riskChart?.resize(); trendChart?.resize(); sourceChart?.resize()
}

async function loadAll() {
  loading.value = true
  try {
    const [s, t] = await Promise.all([dashboardApi.getStats(), dashboardApi.getTrend(30)])
    stats.value = s.data
    trend.value = t.data
    renderRisk(); renderTrend(); renderSource()
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadAll()
  window.addEventListener('resize', resizeCharts)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeCharts)
  riskChart?.dispose(); trendChart?.dispose(); sourceChart?.dispose()
})
</script>

<style scoped>
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 { font-size: 20px; color: #303133; }
.stat-row { margin-bottom: 16px; }
.stat-card {
  background: #fff;
  border-radius: 8px;
  padding: 18px 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
  cursor: default;
  border-top: 3px solid transparent;
}
.stat-card .stat-number { font-size: 26px; font-weight: 700; color: #303133; }
.stat-card .stat-label { font-size: 13px; color: #909399; margin-top: 4px; }
.card-blue { border-top-color: #409eff; }
.card-purple { border-top-color: #9c27b0; }
.card-red { border-top-color: #f56c6c; }
.card-green { border-top-color: #67c23a; }
.card-orange { border-top-color: #e6a23c; }
.card-cyan { border-top-color: #18b8c4; }
.chart-card {
  background: #fff;
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.06);
}
.chart-title { font-size: 15px; color: #303133; margin-bottom: 10px; }
.chart { width: 100%; height: 320px; }
.chart-sm { height: 240px; }
</style>
