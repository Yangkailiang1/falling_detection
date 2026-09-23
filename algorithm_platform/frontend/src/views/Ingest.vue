<template>
  <div class="ingest-page">
    <div class="page-header">
      <h2>数据接入</h2>
      <div class="key-row">
        <span class="key-label">API Key</span>
        <el-input v-model="apiKey" :type="showKey ? 'text' : 'password'" style="width:220px" placeholder="请输入本地 API Key">
          <template #append>
            <el-button :icon="showKey ? 'Hide' : 'View'" @click="showKey = !showKey" />
          </template>
        </el-input>
      </div>
    </div>

    <el-row :gutter="16">
      <!-- ① 采集接口 -->
      <el-col :span="12">
        <div class="page-card">
          <h3 class="card-title">① 采集接口</h3>
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item label="端点">POST {{ info.endpoint || '/api/ingest/events' }}</el-descriptions-item>
            <el-descriptions-item label="鉴权">X-API-Key 请求头（写入/导出需鉴权，读取开放）</el-descriptions-item>
            <el-descriptions-item label="单批上限">{{ info.max_batch || 500 }} 条事件</el-descriptions-item>
          </el-descriptions>
          <div class="code-block">
            <code>{{ curlExample }}</code>
            <el-button size="small" text type="primary" @click="copyText(curlExample)">复制</el-button>
          </div>
        </div>

        <div class="page-card">
          <h3 class="card-title">② Collector 采集工具</h3>
          <p class="desc">在算法迭代平台目录下运行，读取客户侧归档 JSON → 脱敏 → 上传。</p>
          <div class="code-block">
            <code>{{ collectorCommand }}</code>
            <el-button size="small" text type="primary" @click="copyText(collectorCommand)">复制</el-button>
          </div>
        </div>
      </el-col>

      <el-col :span="12">
        <!-- ③ 演示数据 -->
        <div class="page-card">
          <h3 class="card-title">③ 生成演示数据</h3>
          <p class="desc">生成含合成骨骼序列的脱敏演示事件（客户侧当前不持久化关键点，演示用合成数据）。</p>
          <div class="btn-row">
            <el-input-number v-model="seedCount" :min="10" :max="200" style="width:120px" />
            <el-button type="primary" @click="doSeed" :loading="seeding">
              <el-icon><MagicStick /></el-icon>
              生成并入库
            </el-button>
          </div>
          <el-alert v-if="seedResult" :title="seedResult" type="success" :closable="false" style="margin-top:10px" />
        </div>

        <!-- ④ 手动上传 -->
        <div class="page-card">
          <h3 class="card-title">④ 手动上传脱敏事件</h3>
          <p class="desc">粘贴一条脱敏事件 JSON（schema 见 CLAUDE.md）后提交。</p>
          <el-input v-model="manualJson" type="textarea" :rows="5"
                    placeholder='{"event_id":"...","source_id":"...","created_at":"...","ground_truth":{"is_fall":true},...}' />
          <div class="btn-row">
            <el-button type="primary" @click="doManualUpload" :loading="uploading">提交</el-button>
          </div>
        </div>

        <!-- ⑤ 数据集导出 -->
        <div class="page-card">
          <h3 class="card-title">⑤ 数据集导出</h3>
          <p class="desc">导出脱敏事件 JSON，或导出 s-jepa 可消费的训练数据集 zip（.npy 骨骼序列 + split.json）。</p>
          <div class="btn-row">
            <el-button @click="exportJson" :loading="exporting">
              <el-icon><Download /></el-icon>
              导出事件 JSON
            </el-button>
            <el-button type="warning" @click="exportZip" :loading="exporting">
              <el-icon><Download /></el-icon>
              导出训练数据集 (zip)
            </el-button>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ingestApi, downloadWithKey } from '@/api/ingest'

const info = ref({})
const apiKey = ref(localStorage.getItem('algo_api_key') || '')
const showKey = ref(false)
const seedCount = ref(40)
const seeding = ref(false)
const seedResult = ref('')
const manualJson = ref('')
const uploading = ref(false)
const exporting = ref(false)

const curlExample = computed(() =>
  `curl -X POST http://localhost:5003/api/ingest/events \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: ${apiKey.value}" \\
  -d '{"source_id":"src_demo_a","events":[{...}]}'`)

const collectorCommand = computed(() =>
  `python3 tools/collector.py \\
  --archive <客户归档.json> \\
  --api http://localhost:5003 \\
  --api-key ${apiKey.value} \\
  --attach-skeletons`)

function saveKey() {
  localStorage.setItem('algo_api_key', apiKey.value)
}
function copyText(text) {
  navigator.clipboard?.writeText(text).then(() => ElMessage.success('已复制'))
}

async function doSeed() {
  seeding.value = true
  saveKey()
  try {
    const res = await ingestApi.seedDemo(seedCount.value)
    seedResult.value = `入库 ${res.data.inserted} 条，库内共 ${res.data.existing} 条`
    ElMessage.success('演示数据生成完成')
  } finally {
    seeding.value = false
  }
}

async function doManualUpload() {
  uploading.value = true
  saveKey()
  try {
    let event = JSON.parse(manualJson.value)
    if (!Array.isArray(event)) event = [event]
    const res = await ingestApi.ingest(event, null, 'manual-upload')
    ElMessage.success(`上传 ${res.data.total} 条，入库 ${res.data.inserted}，去重 ${res.data.skipped_duplicates}`)
    manualJson.value = ''
  } catch {
    ElMessage.error('JSON 解析失败，请检查格式')
  } finally {
    uploading.value = false
  }
}

async function exportJson() {
  exporting.value = true
  saveKey()
  try {
    await downloadWithKey(() => ingestApi.exportEvents(), 'falling_events_anonymized.json')
  } finally {
    exporting.value = false
  }
}

async function exportZip() {
  exporting.value = true
  saveKey()
  try {
    await downloadWithKey(() => ingestApi.exportTraining(''), 'falling_dataset.zip')
  } finally {
    exporting.value = false
  }
}

onMounted(async () => {
  try {
    const res = await ingestApi.getInfo()
    info.value = res.data
  } catch { /* ignore */ }
})
</script>

<style scoped>
.page-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.page-header h2 { font-size: 20px; color: #303133; }
.key-row { display: flex; align-items: center; gap: 8px; }
.key-label { color: #909399; font-size: 13px; }
.card-title { font-size: 15px; color: #303133; margin-bottom: 10px; }
.desc { font-size: 13px; color: #606266; margin-bottom: 10px; }
.code-block {
  background: #f7f8fa; border: 1px solid #ebeef5; border-radius: 6px;
  padding: 10px; margin-top: 10px; display: flex; align-items: flex-start; justify-content: space-between; gap: 8px;
}
.code-block code { font-family: Consolas, monospace; font-size: 12px; color: #303133; white-space: pre-wrap; word-break: break-all; }
.btn-row { display: flex; gap: 10px; margin-top: 10px; align-items: center; }
</style>
