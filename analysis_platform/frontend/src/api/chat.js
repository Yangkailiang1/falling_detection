// [开发文档: V3 - DeepSeek对话API模块]
import apiClient from './index'

export function getChatModels() {
  return apiClient.get('/chat/models')
}

export function sendChatMessage(messages, stream = false) {
  return apiClient.post('/chat/completions', { messages, stream })
}

export function simpleChat(message, systemPrompt = null) {
  return apiClient.post('/chat/simple', { message, system_prompt: systemPrompt })
}

export function generateFallReport(fallData) {
  return apiClient.post('/chat/fall-report', { fall_data: fallData })
}
