<!--
  对应文档: 四.V3.0 - AI对话面板组件
  功能: 提供与DeepSeek大模型的聊天界面
  对应文档章节: 三.3.2 - AI对话功能
-->
<template>
  <div class="chat-panel">
    <!-- 消息列表 -->
    <div class="chat-messages" ref="msgContainer">
      <div v-for="(msg, idx) in messages" :key="idx" :class="['msg-item', msg.role]">
        <div class="msg-avatar">
          {{ msg.role === 'user' ? '👤' : '🤖' }}
        </div>
        <div class="msg-bubble">
          <div class="msg-text">{{ msg.content }}</div>
          <div class="msg-time">{{ msg.time }}</div>
        </div>
      </div>
      <div v-if="typing" class="msg-item assistant">
        <div class="msg-avatar">🤖</div>
        <div class="msg-bubble typing">思考中...</div>
      </div>
    </div>

    <!-- 输入区域 -->
    <div class="chat-input">
      <el-input
        v-model="inputText"
        placeholder="输入消息，按 Enter 发送..."
        @keyup.enter="sendMessage"
        :disabled="typing"
      >
        <template #append>
          <el-button :icon="Promotion" @click="sendMessage" :loading="typing" />
        </template>
      </el-input>
    </div>
  </div>
</template>

<script setup>
// [开发文档 四.V3.0 - ChatPanel组件]
import { ref, nextTick } from 'vue'
import { Promotion } from '@element-plus/icons-vue'
import { simpleChat } from '@/api/chat'
import { ElMessage } from 'element-plus'

const messages = ref([])
const inputText = ref('')
const typing = ref(false)
const msgContainer = ref(null)

// 创建消息对象
function createMsg(role, content) {
  const now = new Date()
  return {
    role,
    content,
    time: `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`
  }
}

// 滚动到底部
function scrollToBottom() {
  nextTick(() => {
    const el = msgContainer.value
    if (el) el.scrollTop = el.scrollHeight
  })
}

// 发送消息
async function sendMessage() {
  const text = inputText.value.trim()
  if (!text || typing.value) return

  messages.value.push(createMsg('user', text))
  inputText.value = ''
  typing.value = true
  scrollToBottom()

  try {
    const res = await simpleChat(text)
    messages.value.push(createMsg('assistant', res.data?.content || '回复获取失败'))
  } catch {
    ElMessage.error('AI对话失败')
    messages.value.push(createMsg('assistant', '抱歉，AI服务当前不可用。'))
  } finally {
    typing.value = false
    scrollToBottom()
  }
}
</script>

<style scoped>
.chat-panel {
  height: 500px;
  display: flex;
  flex-direction: column;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  overflow: hidden;
  background: #fff;
}
.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 16px;
  background: #fafafa;
}
.msg-item {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}
.msg-item.user { flex-direction: row-reverse; }
.msg-avatar {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f0f0f0;
  font-size: 18px;
  flex-shrink: 0;
}
.msg-bubble {
  max-width: 75%;
  padding: 10px 14px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.msg-item.user .msg-bubble {
  background: var(--primary-color);
  color: #fff;
}
.msg-text { font-size: 14px; line-height: 1.6; white-space: pre-wrap; }
.msg-time { font-size: 11px; color: #999; margin-top: 4px; }
.msg-item.user .msg-time { color: rgba(255,255,255,0.7); }
.typing { color: #909399; font-style: italic; }
.chat-input { padding: 12px; border-top: 1px solid #e4e7ed; background: #fff; }
</style>
