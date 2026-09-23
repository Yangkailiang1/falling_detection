<!--
  对应文档: 四.V3.0 - AI对话页面
  功能: 与萤石DeepSeek大模型对话，支持医疗简报生成
-->
<template>
  <div class="page-container">
    <h2 class="page-title">AI对话 (DeepSeek)</h2>

    <el-row :gutter="16">
      <!-- 对话面板 -->
      <el-col :span="17">
        <ChatPanel />
      </el-col>

      <!-- 快捷功能 -->
      <el-col :span="7">
        <!-- 跌倒报告生成 -->
        <el-card class="page-card">
          <template #header>跌倒医疗简报</template>
          <el-form size="small" label-width="90px">
            <el-form-item label="着地部位">
              <el-select v-model="fallData.landing_part" style="width:100%">
                <el-option label="头部 (高危)" value="head" />
                <el-option label="脊柱 (高危)" value="spine" />
                <el-option label="髋部 (中危)" value="hip" />
                <el-option label="肩部 (中危)" value="shoulder" />
                <el-option label="手部 (低危)" value="hand" />
                <el-option label="肘部 (低危)" value="elbow" />
                <el-option label="膝部 (低危)" value="knee" />
              </el-select>
            </el-form-item>
            <el-form-item label="置信度">
              <el-input-number v-model="fallData.confidence" :min="0" :max="1" :step="0.1" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="generateReport" :loading="generating">
                生成医疗简报
              </el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <!-- 报告结果 -->
        <el-card v-if="report" class="page-card">
          <template #header>
            <span>报告结果</span>
            <el-tag :type="tagType" size="small" style="margin-left:8px">
              {{ report.risk_level }}
            </el-tag>
          </template>
          <div class="report-content">{{ report.report }}</div>
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - AI对话页面]
import { ref, computed } from 'vue'
import { generateFallReport } from '@/api/chat'
import { ElMessage } from 'element-plus'
import ChatPanel from '@/components/ChatPanel.vue'

const fallData = ref({ landing_part: 'hip', confidence: 0.9 })
const report = ref(null)
const generating = ref(false)

const tagType = computed(() => {
  if (!report.value) return 'info'
  const level = report.value.risk_level || ''
  if (level.includes('I级')) return 'danger'
  if (level.includes('II级')) return 'warning'
  return 'success'
})

async function generateReport() {
  generating.value = true
  try {
    const res = await generateFallReport({
      time: new Date().toISOString(),
      landing_part: fallData.value.landing_part,
      confidence: fallData.value.confidence
    })
    report.value = res.data
  } catch {
    ElMessage.error('生成简报失败')
  } finally {
    generating.value = false
  }
}
</script>

<style scoped>
.report-content {
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  color: #303133;
}
</style>
