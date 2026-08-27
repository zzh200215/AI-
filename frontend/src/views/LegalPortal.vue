<template>
  <div class="legal-portal">
    <div v-if="errorState" class="portal-error">
      <el-card shadow="never">
        <el-empty :description="errorState" :image-size="80" />
      </el-card>
    </div>

    <div v-else-if="step === 'otp'" class="portal-otp">
      <el-card shadow="never">
        <template #header><span class="card-title">案件查询验证</span></template>
        <el-alert v-if="maskedEmail" type="info" :closable="false" show-icon style="margin-bottom:16px">
          <template #title>验证码已发送至 {{ maskedEmail }}</template>
        </el-alert>
        <el-form @submit.prevent="verifyOtp">
          <el-form-item label="6位验证码">
            <el-input v-model="otpCode" maxlength="6" placeholder="请输入验证码" class="otp-input" @input="otpCode = otpCode.replace(/\D/g, '')" />
          </el-form-item>
          <div class="otp-actions">
            <el-button type="primary" :loading="verifyLoading" @click="verifyOtp">验证</el-button>
            <el-button :loading="resendLoading" :disabled="resendCooldown > 0" @click="resendOtp">
              {{ resendCooldown > 0 ? `${resendCooldown}s 后重发` : '重新发送' }}
            </el-button>
          </div>
        </el-form>
      </el-card>
    </div>

    <div v-else-if="step === 'content'" class="portal-content">
      <div v-if="portalContent.organization && (portalContent.organization.portal_logo_url || portalContent.organization.portal_welcome_message || portalContent.organization.name)" class="portal-brand">
        <img v-if="portalContent.organization.portal_logo_url" :src="portalContent.organization.portal_logo_url" class="portal-logo" alt="logo" />
        <div class="portal-brand-text">
          <div v-if="portalContent.organization.portal_welcome_message" class="portal-welcome">{{ portalContent.organization.portal_welcome_message }}</div>
          <div v-if="portalContent.organization.name" class="portal-org-name">{{ portalContent.organization.name }}</div>
        </div>
      </div>
      <el-card shadow="never">
        <template #header>
          <div class="result-header">
            <span class="card-title">案件进展</span>
          </div>
        </template>
        <div v-if="progressUpdates.length" class="progress-timeline">
          <div v-for="u in progressUpdates" :key="u.id" class="timeline-item">
            <div class="timeline-dot"></div>
            <div class="timeline-body">
              <div class="progress-header">
                <strong>{{ u.title }}</strong>
                <div class="progress-meta">
                  <el-tag v-if="u.status === 'published'" size="small" type="success" effect="plain">已发布</el-tag>
                  <span class="muted">{{ formatDate(u.published_at || u.created_at) }}</span>
                </div>
              </div>
              <p class="progress-body">{{ u.body }}</p>
              <div v-if="u.next_steps" class="progress-next">
                <strong>后续步骤：</strong>
                <p>{{ u.next_steps }}</p>
              </div>
            </div>
          </div>
        </div>
        <el-empty v-else description="暂无进展更新" :image-size="60" />
      </el-card>

      <el-card v-if="publishedDocs.length" shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">公开文档</span></template>
        <el-table :data="publishedDocs" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="title" label="文档名称" show-overflow-tooltip />
          <el-table-column label="操作" width="120">
            <template #default="{ row }">
              <el-button size="small" text type="primary" @click="downloadDoc(row)">下载</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card v-if="portalContent.invoice" shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">账单摘要</span></template>
        <el-descriptions :column="2" border size="small" class="invoice-desc">
          <el-descriptions-item label="账单号">{{ portalContent.invoice.invoice_number }}</el-descriptions-item>
          <el-descriptions-item label="金额">¥{{ portalContent.invoice.total_amount }}</el-descriptions-item>
          <el-descriptions-item label="状态">{{ invoiceStatusLabel(portalContent.invoice.status) }}</el-descriptions-item>
          <el-descriptions-item label="账期">{{ portalContent.invoice.period_start }} ~ {{ portalContent.invoice.period_end }}</el-descriptions-item>
          <el-descriptions-item v-if="portalContent.invoice.paid_amount != null" label="已收">¥{{ portalContent.invoice.paid_amount }}</el-descriptions-item>
          <el-descriptions-item v-if="portalContent.invoice.due_date" label="应付款日">{{ portalContent.invoice.due_date }}</el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-card shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">服务反馈</span></template>
        <div v-if="feedbackDone" class="feedback-done">
          <el-tag size="small" :type="feedbackValue === 1 ? 'success' : 'info'" effect="plain">
            {{ feedbackValue === 1 ? '已收到反馈：有帮助' : '已收到反馈：待改进' }}
          </el-tag>
          <span class="muted">感谢您的评价。</span>
        </div>
        <div v-else class="feedback-row">
          <span class="feedback-label">本次服务对您有帮助吗？</span>
          <el-button size="small" type="success" plain :loading="feedbackSubmitting === 1" @click="submitFeedback(1)">有帮助</el-button>
          <el-button size="small" type="warning" plain :loading="feedbackSubmitting === -1" @click="submitFeedback(-1)">待改进</el-button>
          <div v-if="feedbackNoteOpen" class="feedback-panel">
            <el-input v-model="feedbackNote" type="textarea" :rows="2" maxlength="500" show-word-limit placeholder="补充说明（可选，最多500字）" />
            <div class="feedback-actions">
              <el-button size="small" type="primary" @click="confirmFeedback">提交反馈</el-button>
              <el-button size="small" @click="feedbackNoteOpen = false; feedbackNote = ''">取消</el-button>
            </div>
          </div>
        </div>
      </el-card>

      <el-card v-if="portalContent.sign_requests?.length" shadow="never" style="margin-top:20px">
        <template #header><span class="card-title">签署请求</span></template>
        <el-table :data="portalContent.sign_requests" stripe size="small">
          <el-table-column prop="id" label="ID" width="60" />
          <el-table-column prop="title" label="文档" show-overflow-tooltip />
          <el-table-column prop="status" label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="row.status === 'pending' ? 'warning' : 'success'" size="small">{{ row.status === 'pending' ? '待签署' : '已签署' }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="100">
            <template #default="{ row }">
              <el-button v-if="row.status === 'pending'" size="small" text type="primary" @click="openSignLink(row)">前往签署</el-button>
              <span v-else class="muted">已完成</span>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../api'

const route = useRoute()
const token = route.params.token

const step = ref('otp')
const errorState = ref('')
const maskedEmail = ref('')
const otpCode = ref('')
const verifyLoading = ref(false)
const resendLoading = ref(false)
const resendCooldown = ref(0)
let cooldownTimer = null

const portalContent = ref({})
const portalSession = ref(sessionStorage.getItem(`portal-session:${token}`) || '')
const progressUpdates = ref([])
const publishedDocs = ref([])

const feedbackValue = ref(null)
const feedbackDone = ref(false)
const feedbackSubmitting = ref(0)
const feedbackNoteOpen = ref(false)
const feedbackNote = ref('')

const submitFeedback = (score) => {
  if (score === 1) {
    doSubmitFeedback(1)
  } else {
    feedbackNoteOpen.value = true
  }
}

const confirmFeedback = async () => {
  await doSubmitFeedback(-1)
}

const doSubmitFeedback = async (score) => {
  feedbackSubmitting.value = score
  try {
    const note = feedbackNote.value.trim()
    await api.portalSubmitFeedback(token, { score, note: note || null }, portalSession.value)
    feedbackValue.value = score
    feedbackDone.value = true
    feedbackNoteOpen.value = false
    ElMessage.success('反馈已提交')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '反馈提交失败')
  }
  feedbackSubmitting.value = 0
}

const initOtp = async () => {
  try {
    const { data } = await api.portalSendOtp(token)
    maskedEmail.value = data.email_masked || ''
    startCooldown()
  } catch (e) {
    const detail = e.response?.data?.detail
    if (e.response?.status === 404) errorState.value = '链接不存在'
    else if (e.response?.status === 410) errorState.value = '链接已过期'
    else if (e.response?.status === 403) errorState.value = '链接已被撤销'
    else errorState.value = detail || '无法访问'
  }
}

const verifyOtp = async () => {
  if (otpCode.value.length !== 6) return ElMessage.warning('请输入6位验证码')
  verifyLoading.value = true
  try {
    const { data } = await api.portalVerifyOtp(token, otpCode.value)
    portalSession.value = data.session_token
    sessionStorage.setItem(`portal-session:${token}`, data.session_token)
    await loadContent()
    step.value = 'content'
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '验证失败')
  }
  verifyLoading.value = false
}

const resendOtp = async () => {
  resendLoading.value = true
  try {
    const { data } = await api.portalSendOtp(token)
    maskedEmail.value = data.email_masked || maskedEmail.value
    startCooldown()
    ElMessage.success('验证码已重新发送')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  }
  resendLoading.value = false
}

const startCooldown = () => {
  resendCooldown.value = 60
  if (cooldownTimer) clearInterval(cooldownTimer)
  cooldownTimer = setInterval(() => {
    resendCooldown.value--
    if (resendCooldown.value <= 0) clearInterval(cooldownTimer)
  }, 1000)
}

const loadContent = async () => {
  try {
    const { data } = await api.portalGetContent(token, portalSession.value)
    portalContent.value = data
    progressUpdates.value = data.progress_updates || []
    publishedDocs.value = data.documents || []
  } catch (e) {
    errorState.value = e.response?.data?.detail || '加载内容失败'
  }
}

const downloadDoc = async (doc) => {
  try {
    const { data } = await api.portalDownloadDocument(token, doc.id, portalSession.value)
    const url = URL.createObjectURL(data)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = doc.title || 'document'
    anchor.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '文件暂不可下载')
  }
}

const openSignLink = (req) => {
  if (req.sign_url) {
    window.open(req.sign_url, '_blank')
  } else {
    ElMessage.info('暂无签署链接')
  }
}

const invoiceStatusLabel = (s) => ({ draft: '草稿', sent: '已发送', paid: '已支付', overdue: '逾期', voided: '已作废' }[s] || s)
const formatDate = (v) => {
  if (!v) return ''
  return String(v).replace('T', ' ').slice(0, 16)
}

onMounted(() => {
  if (!token) {
    errorState.value = '链接无效'
    return
  }
  initOtp()
})

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer)
})
</script>

<style scoped>
.legal-portal {
  max-width: 800px;
  margin: 40px auto;
  min-height: 60vh;
}

.otp-input {
  width: 240px;
}

.card-title {
  font-weight: 700;
  font-size: 15px;
}

.muted {
  color: var(--color-text-muted);
  font-size: 13px;
}

.result-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.portal-error {
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 40vh;
}

.portal-brand {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 20px;
  padding: 18px 20px;
  background: var(--color-bg-card, #fff);
  border-radius: 10px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.06);
}
.portal-logo {
  max-height: 56px;
  max-width: 140px;
  object-fit: contain;
  border-radius: 6px;
}
.portal-brand-text {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.portal-welcome {
  font-size: 16px;
  font-weight: 700;
  color: var(--color-text);
}
.portal-org-name {
  font-size: 13px;
  color: var(--color-text-muted);
}

.otp-actions {
  display: flex;
  gap: 12px;
  align-items: center;
}

.progress-timeline {
  position: relative;
  padding-left: 20px;
}

.progress-timeline::before {
  content: '';
  position: absolute;
  left: 5px;
  top: 6px;
  bottom: 6px;
  width: 2px;
  background: var(--el-border-color-lighter);
}

.timeline-item {
  position: relative;
  padding: 12px 0;
}

.timeline-item:not(:last-child) {
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.timeline-dot {
  position: absolute;
  left: -20px;
  top: 18px;
  width: 12px;
  height: 12px;
  border-radius: 50%;
  background: var(--el-color-primary);
  border: 2px solid #fff;
  box-shadow: 0 0 0 2px var(--el-color-primary-light-5);
}

.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.progress-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.progress-body {
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text-secondary);
  margin: 0;
}

.progress-next {
  margin-top: 8px;
  font-size: 13px;
}

.progress-next p {
  margin: 4px 0 0;
  line-height: 1.6;
}

.feedback-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.feedback-label {
  font-size: 13px;
  color: var(--color-text-muted);
}

.feedback-done {
  display: flex;
  align-items: center;
  gap: 10px;
}

.feedback-panel {
  display: grid;
  gap: 8px;
  width: 100%;
  padding: 12px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
  margin-top: 4px;
}

.feedback-actions {
  display: flex;
  gap: 8px;
}

@media (max-width: 640px) {
  .legal-portal {
    max-width: 100%;
    margin: 16px auto;
    padding: 0 12px;
  }

  .otp-input {
    width: 100%;
  }

  .invoice-desc :deep(.el-descriptions__table) {
    table-layout: fixed;
    word-break: break-all;
  }

  .progress-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 4px;
  }
}
</style>
