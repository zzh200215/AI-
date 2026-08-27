<template>
  <div class="chat-page grid h-[calc(100vh-220px)] min-h-[620px] grid-cols-[280px_minmax(0,1fr)] gap-5">
    <aside class="ui-card flex min-h-0 flex-col overflow-hidden">
      <div class="border-b border-slate-200 px-5 py-4">
        <div class="text-sm font-semibold text-slate-950">对话记录</div>
        <div class="mt-1 text-xs leading-5 text-slate-500">保留当前会话上下文，支持实时流式返回。</div>
      </div>
      <div class="flex-1 overflow-auto p-3">
        <button class="mb-2 w-full rounded-lg border border-blue-100 bg-blue-50 px-3 py-3 text-left">
          <div class="truncate text-sm font-semibold text-blue-700">当前实时会话</div>
          <div class="mt-1 text-xs text-blue-500">{{ currentSessionId || '连接后自动生成 Session' }}</div>
        </button>
        <div class="rounded-lg border border-slate-200 bg-slate-50 px-3 py-3">
          <div class="text-xs font-medium text-slate-500">消息统计</div>
          <div class="mt-2 flex items-end justify-between">
            <span class="text-2xl font-semibold text-slate-950">{{ messages.length }}</span>
            <span class="text-xs text-slate-500">条消息</span>
          </div>
        </div>
        <section class="mt-3 border-t border-slate-200 pt-3">
          <div class="flex items-center justify-between gap-2">
            <span class="text-xs font-medium text-slate-600">个人偏好</span>
            <button type="button" class="text-xs font-medium text-blue-700" @click="preferencesOpen = !preferencesOpen">
              {{ preferencesOpen ? '收起' : '管理' }}
            </button>
          </div>
          <div v-if="preferencesOpen" class="mt-3 space-y-2">
            <div v-for="item in preferences" :key="item.id" class="border-b border-slate-200 pb-2 text-xs">
              <div class="flex items-start justify-between gap-2">
                <div class="min-w-0">
                  <div class="truncate font-medium text-slate-700">{{ item.preference_key }}</div>
                  <div class="mt-0.5 break-words text-slate-500">{{ item.preference_value }}</div>
                </div>
                <button type="button" class="shrink-0 text-slate-400 hover:text-red-600" @click="removePreference(item.id)">删除</button>
              </div>
            </div>
            <div v-if="!preferences.length" class="text-xs text-slate-400">尚未保存个人偏好</div>
            <el-input v-model="preferenceKey" size="small" maxlength="128" placeholder="偏好名称，例如：审查风格" />
            <el-input v-model="preferenceValue" size="small" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="偏好内容，例如：结论优先，语气简洁" />
            <el-button size="small" type="primary" :loading="preferenceSaving" @click="savePreference">保存偏好</el-button>
          </div>
        </section>
      </div>
      <div class="border-t border-slate-200 px-4 py-3">
        <div class="flex items-center gap-2 text-xs text-slate-500">
          <span class="h-2 w-2 rounded-full" :class="loading ? 'bg-amber-500' : 'bg-emerald-500'"></span>
          <span>{{ loading ? '正在生成回复' : '实时通道待命' }}</span>
        </div>
      </div>
    </aside>

    <section class="ui-card flex min-h-0 flex-col overflow-hidden">
      <header class="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <div>
          <div class="text-base font-semibold text-slate-950">法律对话</div>
          <div class="mt-1 text-xs text-slate-500">结合知识库检索，回答附带法条与案例引用。</div>
        </div>
        <div class="flex items-center gap-2 text-xs text-slate-500">
          <span class="h-2 w-2 rounded-full" :class="connected ? 'bg-emerald-500' : 'bg-slate-300'"></span>
          <span>{{ connected ? '实时通道已连接' : '连接中' }}</span>
        </div>
      </header>

      <div class="chat-scroll flex-1 overflow-auto bg-slate-50/70 px-6 py-5">
        <div v-if="!messages.length" class="mx-auto flex h-full max-w-2xl flex-col items-center justify-center text-center">
          <div class="empty-icon mb-4 flex h-12 w-12 items-center justify-center rounded-lg">
            <el-icon :size="22"><ChatLineRound /></el-icon>
          </div>
          <div class="text-lg font-semibold text-slate-950">开始一次法律对话</div>
          <p class="mt-2 text-sm leading-6 text-slate-500">可以询问法律依据、条款解释、风险识别或文书草稿。涉及知识库的问题建议到知识库页选择文档后提问。</p>
        </div>

        <div
          v-for="(msg, i) in messages"
          :key="msg.id ?? `msg-${i}`"
          class="mb-5 flex"
          :class="msg.role === 'user' ? 'justify-end' : 'justify-start'"
        >
          <div class="max-w-[74%]">
            <div class="mb-1 flex items-center gap-2 text-xs text-slate-400" :class="msg.role === 'user' ? 'justify-end' : 'justify-start'">
              <span>{{ msg.role === 'user' ? '你' : 'AI 助手' }}</span>
              <span>{{ msg.streaming ? '正在输出' : '已完成' }}</span>
            </div>
            <div
              class="rounded-lg border px-4 py-3 text-sm leading-6 shadow-sm"
              :class="msg.role === 'user'
                ? 'border-blue-600 bg-blue-600 text-white'
                : 'border-slate-200 bg-white text-slate-800'"
            >
              <p class="m-0 whitespace-pre-wrap">{{ msg.content }}<span v-if="msg.streaming" class="cursor-blink">|</span></p>
              <div v-if="msg.streaming && !msg.content" class="typing-dots mt-1">
                <span></span><span></span><span></span>
              </div>
            </div>

            <div v-if="msg.citations?.length" class="mt-3 grid gap-2">
              <div v-for="(item, index) in msg.citations" :key="`citation-${i}-${index}`" class="rounded-lg border border-blue-100 bg-blue-50/70 p-3">
                <div class="mb-1 flex items-center justify-between gap-3">
                  <span class="text-xs font-semibold text-blue-700">引用 {{ index + 1 }}</span>
                  <span v-if="item.page_number" class="text-xs text-blue-500">第 {{ item.page_number }} 页</span>
                </div>
                <p class="m-0 line-clamp-3 text-xs leading-5 text-slate-600">{{ item.source_text || item.quote || item.content }}</p>
              </div>
            </div>
          </div>
        </div>
      </div>

      <footer class="border-t border-slate-200 bg-white px-6 py-4">
        <div class="flex gap-3">
          <el-input
            v-model="input"
            type="textarea"
            :rows="2"
            placeholder="输入消息，Enter 发送，Shift+Enter 换行"
            class="chat-input"
            @keydown.enter.exact.prevent="send"
          />
          <el-button type="primary" :loading="loading" class="send-button" @click="send">发送</el-button>
        </div>
      </footer>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ChatLineRound } from '@element-plus/icons-vue'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import memory from '../api/memory'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/input/style/css'

const messages = ref([])
const input = ref('')
const loading = ref(false)
const connected = ref(false)
const currentSessionId = ref(null)
const preferences = ref([])
const preferencesOpen = ref(false)
const preferenceKey = ref('')
const preferenceValue = ref('')
const preferenceSaving = ref(false)
let ws = null
let reconnectTimer = null

const connectWS = () => {
  const token = localStorage.getItem('token')
  if (!token) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const wsHost = import.meta.env.DEV
    ? (import.meta.env.VITE_WS_HOST || 'localhost:8001')
    : location.host
  const wsUrl = `${proto}://${wsHost}/api/ws/chat`
  ws = new WebSocket(wsUrl, ['json', `bearer.${token}`])

  ws.onopen = () => {
    connected.value = true
  }

  ws.onmessage = (event) => {
    const data = JSON.parse(event.data)
    if (data.type === 'session') {
      currentSessionId.value = data.session_id
    } else if (data.type === 'chunk') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming === true) {
        last.content += data.content
      }
    } else if (data.type === 'done') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming === true) {
        last.streaming = false
      }
      loading.value = false
    } else if (data.type === 'error') {
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && last.streaming === true) {
        last.streaming = false
      }
      ElMessage.error(data.content)
      loading.value = false
    }
  }

  ws.onerror = (e) => {
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.streaming === true) {
      last.streaming = false
    }
    console.error('WebSocket error:', e)
    ElMessage.error('实时连接失败')
    loading.value = false
  }

  ws.onclose = (e) => {
    connected.value = false
    const last = messages.value[messages.value.length - 1]
    if (last && last.role === 'assistant' && last.streaming === true) {
      last.streaming = false
    }
    loading.value = false
    if (e.code !== 1000) {
      reconnectTimer = setTimeout(connectWS, 3000)
    }
  }
}

const send = () => {
  if (!input.value.trim()) return
  if (!ws || ws.readyState !== WebSocket.OPEN) {
    ElMessage.error('实时连接尚未建立')
    return
  }

  const ts = Date.now()
  messages.value.push({ id: `u-${ts}`, role: 'user', content: input.value })
  const assistantMsg = { id: `a-${ts}`, role: 'assistant', content: '', streaming: true }
  const assistantIndex = messages.value.push(assistantMsg) - 1

  try {
    ws.send(JSON.stringify({
      content: input.value,
      session_id: currentSessionId.value,
    }))
  } catch (e) {
    console.error('发送消息失败', e)
    if (messages.value[assistantIndex] && messages.value[assistantIndex].streaming) {
      messages.value[assistantIndex].streaming = false
    }
    loading.value = false
    ElMessage.error('消息发送失败')
    return
  }

  input.value = ''
  loading.value = true
}

const loadPreferences = async () => {
  try {
    const { data } = await memory.listMemoryPreferences()
    preferences.value = data || []
  } catch {
    preferences.value = []
  }
}

const savePreference = async () => {
  if (!preferenceKey.value.trim() || !preferenceValue.value.trim()) {
    ElMessage.warning('请填写偏好名称和内容')
    return
  }
  preferenceSaving.value = true
  try {
    await memory.saveMemoryPreference({
      category: 'writing',
      preference_key: preferenceKey.value.trim(),
      preference_value: preferenceValue.value.trim(),
    })
    preferenceKey.value = ''
    preferenceValue.value = ''
    await loadPreferences()
    ElMessage.success('偏好已保存')
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '保存偏好失败')
  } finally {
    preferenceSaving.value = false
  }
}

const removePreference = async (id) => {
  try {
    await memory.deleteMemoryPreference(id)
    preferences.value = preferences.value.filter((item) => item.id !== id)
  } catch (error) {
    ElMessage.error(error?.response?.data?.detail || '删除偏好失败')
  }
}

onMounted(() => {
  connectWS()
  loadPreferences()
})
onUnmounted(() => {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (ws) ws.close(1000)
})
</script>

<style scoped>
.chat-page :deep(.ui-card) {
  border-color: var(--color-border-light);
  border-radius: var(--radius-xl);
  background: #ffffff;
  box-shadow: var(--shadow-xs);
}

.chat-page :deep(.bg-slate-50),
.chat-page :deep(.bg-slate-50\/70) {
  background: var(--color-surface-subtle);
}

.chat-page :deep(.bg-blue-50),
.chat-page :deep(.bg-blue-50\/70),
.chat-page :deep(.bg-slate-100) {
  background: var(--color-primary-light);
}

.chat-page :deep(.bg-blue-600) {
  background: var(--color-primary);
}

.chat-page :deep(.text-blue-700),
.chat-page :deep(.text-blue-500) {
  color: var(--color-primary);
}

.chat-page :deep(.text-slate-950),
.chat-page :deep(.text-slate-800) {
  color: var(--color-text);
}

.chat-page :deep(.text-slate-600),
.chat-page :deep(.text-slate-500) {
  color: var(--color-text-secondary);
}

.chat-page :deep(.border-slate-200),
.chat-page :deep(.border-blue-100) {
  border-color: var(--color-border-light);
}

.chat-page :deep(.border-blue-600) {
  border-color: transparent;
}

.cursor-blink {
  animation: blink 1s step-end infinite;
}

.empty-icon {
  border: 1px solid var(--color-border);
  background: var(--color-surface-subtle);
  color: var(--color-text-muted);
}

.chat-scroll {
  scrollbar-width: thin;
  scrollbar-color: var(--color-border) transparent;
}

.typing-dots {
  display: inline-flex;
  gap: 4px;
  align-items: center;
}

.typing-dots span {
  width: 6px;
  height: 6px;
  border-radius: var(--radius-full);
  background: var(--color-text-muted);
  animation: typing 1s infinite ease-in-out;
}

.typing-dots span:nth-child(2) {
  animation-delay: 0.15s;
}

.typing-dots span:nth-child(3) {
  animation-delay: 0.3s;
}

.send-button {
  min-width: 92px;
  height: 56px;
  border-radius: var(--radius-lg);
}

:deep(.chat-input .el-textarea__inner) {
  min-height: 56px !important;
  border-radius: var(--radius-lg);
  box-shadow: 0 0 0 1px var(--color-border-light) inset !important;
  background: #ffffff;
}

@keyframes blink {
  50% { opacity: 0; }
}

@keyframes typing {
  0%,
  80%,
  100% {
    transform: translateY(0);
    opacity: 0.45;
  }
  40% {
    transform: translateY(-3px);
    opacity: 1;
  }
}

/* Chat message hover interactions */
:deep(.chat-message-bubble) {
  transition: box-shadow var(--transition-fast), transform var(--transition-fast);
}
:deep(.chat-message-bubble:hover) {
  box-shadow: var(--shadow-md);
}
</style>
