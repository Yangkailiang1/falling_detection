<!--
  对应文档: 四.V3.0 - 告警中心页面
  功能: 展示告警消息列表，支持按类型、时间筛选
-->
<template>
  <div class="page-container">
    <h2 class="page-title">告警中心</h2>

    <!-- 筛选条件 -->
    <el-card class="page-card" style="margin-bottom:16px">
      <el-form :inline="true" size="small">
        <el-form-item label="告警类型">
          <el-select v-model="filters.alarmType" placeholder="全部" clearable style="width:150px">
            <el-option label="移动侦测" :value="10000" />
            <el-option label="人形检测" :value="10001" />
            <el-option label="人体感应" :value="10005" />
          </el-select>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="filters.dateRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始"
            end-placeholder="结束"
            value-format="x"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="fetchAlarms">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 告警列表 -->
    <el-table :data="alarmList" v-loading="loading" stripe style="width:100%">
      <el-table-column label="截图" width="100">
        <template #default="{ row }">
          <el-image
            v-if="row.alarmPicUrl"
            :src="row.alarmPicUrl"
            :preview-src-list="[row.alarmPicUrl]"
            preview-teleported
            fit="cover"
            style="width:72px;height:54px;border-radius:4px"
            lazy
          />
          <span v-else style="color:#c0c4cc">无</span>
        </template>
      </el-table-column>
      <el-table-column prop="alarmType" label="告警类型" width="120">
        <template #default="{ row }">
          <el-tag size="small">{{ formatAlarmType(row.alarmType) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="alarmName" label="告警名称" min-width="150" />
      <el-table-column label="告警时间" width="180">
        <template #default="{ row }">
          {{ formatTime(row.alarmTime) }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.isChecked === 0 ? 'warning' : 'info'" size="small">
            {{ row.isChecked === 0 ? '未读' : '已读' }}
          </el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      style="margin-top:20px;justify-content:center"
      :current-page="currentPage + 1"
      :page-size="pageSize"
      :total="total"
      layout="total, prev, pager, next"
      @current-change="onPageChange"
    />

    <el-empty v-if="!loading && alarmList.length === 0" description="暂无告警消息" />
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - 告警中心]
import { ref, onMounted } from 'vue'
import { getAlarmList } from '@/api/alarms'
import { ElMessage } from 'element-plus'

const alarmList = ref([])
const loading = ref(false)
const currentPage = ref(0)
const pageSize = ref(20)
const total = ref(0)

const filters = ref({
  alarmType: null,
  dateRange: null
})

function formatAlarmType(type) {
  return { 10000: '移动侦测', 10001: '人形检测', 10005: '人体感应', 10120: '移动侦测' }[type] || `类型:${type}`
}

function formatTime(ts) {
  if (!ts) return '-'
  return new Date(Number(ts)).toLocaleString('zh-CN')
}

async function fetchAlarms() {
  loading.value = true
  try {
    const params = {
      page: currentPage.value,
      page_size: pageSize.value,
      alarm_type: filters.value.alarmType
    }
    if (filters.value.dateRange) {
      params.start_time = Math.floor(filters.value.dateRange[0] / 1000)
      params.end_time = Math.floor(filters.value.dateRange[1] / 1000)
    }
    const res = await getAlarmList(params)
    if (res.success) {
      alarmList.value = res.data.items
      total.value = res.data.total
    }
  } catch {
    ElMessage.error('获取告警列表失败')
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  filters.value = { alarmType: null, dateRange: null }
  currentPage.value = 0
  fetchAlarms()
}

function onPageChange(page) {
  currentPage.value = page - 1
  fetchAlarms()
}

onMounted(() => fetchAlarms())
</script>
