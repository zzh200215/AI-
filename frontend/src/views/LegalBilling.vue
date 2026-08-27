<template>
  <div class="legal-billing">
    <div v-if="noCase" class="no-case-hint">当前未选择案件。「工时计时」「手动录入」「工时记录」需在顶部案件选择器中选择案件后使用。</div>
    <el-card shadow="never">
      <template #header><span class="card-title">工时计时器</span></template>
      <div class="timer-section">
        <div v-if="runningEntry" class="timer-running">
          <div class="timer-display">
            <el-tag type="danger" size="large" effect="dark">{{ formatDuration(elapsedSeconds) }}</el-tag>
            <span class="muted">{{ runningEntry.description || '未命名计时' }}</span>
          </div>
          <div class="timer-actions">
            <el-button v-if="runningEntry.status === 'running'" type="warning" size="small" @click="pauseTimer">暂停</el-button>
            <el-button v-if="runningEntry.status === 'paused'" type="success" size="small" @click="resumeTimer">继续</el-button>
            <el-button type="danger" size="small" @click="stopTimer">完成</el-button>
          </div>
        </div>
        <div v-else class="timer-start">
          <el-input v-model="timerDescription" placeholder="计时描述" style="width:300px" />
          <el-button type="primary" @click="startTimer">开始计时</el-button>
        </div>
      </div>
    </el-card>

    <el-card shadow="never" style="margin-top:20px">
      <template #header><span class="card-title">手动录入工时</span></template>
      <el-form :model="manualForm" inline @submit.prevent="submitManualEntry">
        <el-form-item label="描述">
          <el-input v-model="manualForm.description" placeholder="工作内容描述" style="width:240px" />
        </el-form-item>
        <el-form-item label="时长（分钟）">
          <el-input-number v-model="manualForm.duration_minutes" :min="1" :max="1440" style="width:140px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="manualLoading" @click="submitManualEntry">录入</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:20px">
      <template #header><span class="card-title">工时记录</span></template>
      <el-table :data="timeEntries" stripe size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="description" label="描述" show-overflow-tooltip />
        <el-table-column label="时长" width="100">
          <template #default="{ row }">{{ formatEntryDuration(row) }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="entryStatusType(row.status)" size="small">{{ entryStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="可计费" width="80">
          <template #default="{ row }">
            <el-tag :type="row.billable ? 'success' : 'info'" size="small">{{ row.billable ? '是' : '否' }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="创建时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="240">
          <template #default="{ row }">
            <el-button v-if="row.status === 'running'" size="small" text type="warning" @click="patchEntry(row.id, { action: 'pause' })">暂停</el-button>
            <el-button v-if="row.status === 'paused'" size="small" text type="success" @click="patchEntry(row.id, { action: 'resume' })">继续</el-button>
            <el-button v-if="row.status === 'running' || row.status === 'paused'" size="small" text type="danger" @click="patchEntry(row.id, { action: 'complete' })">完成</el-button>
            <el-button v-if="row.status === 'running' || row.status === 'paused'" size="small" text @click="patchEntry(row.id, { action: 'void' })">作废</el-button>
            <el-button v-if="row.status === 'completed' && !row.billable" size="small" text type="primary" @click="patchEntry(row.id, { billable: 1 })">确认计费</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination-row">
        <el-pagination
          v-model:current-page="entryPage"
          :page-size="entryPageSize"
          :total="entryTotal"
          layout="total, prev, pager, next"
          small
          @current-change="loadTimeEntries"
        />
      </div>
    </el-card>

    <el-divider />

    <el-card shadow="never">
      <template #header><span class="card-title">计费规则</span></template>
      <el-form :model="ruleForm" inline @submit.prevent="submitBillingRule" style="margin-bottom:16px">
        <el-form-item label="规则名称">
          <el-input v-model="ruleForm.name" placeholder="如：标准咨询费" style="width:160px" />
        </el-form-item>
        <el-form-item label="计费模式">
          <el-select v-model="ruleForm.billing_mode" style="width:140px">
            <el-option label="按小时" value="hourly" />
            <el-option label="固定阶段" value="fixed_stage" />
            <el-option label="混合" value="hybrid" />
          </el-select>
        </el-form-item>
        <el-form-item v-if="ruleForm.billing_mode === 'hourly' || ruleForm.billing_mode === 'hybrid'" label="时薪">
          <el-input-number v-model="ruleForm.hourly_rate" :min="0" :precision="2" style="width:130px" />
        </el-form-item>
        <el-form-item v-if="ruleForm.billing_mode === 'fixed_stage' || ruleForm.billing_mode === 'hybrid'" label="固定金额">
          <el-input-number v-model="ruleForm.fixed_amount" :min="0" :precision="2" style="width:130px" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="ruleLoading" @click="submitBillingRule">创建规则</el-button>
        </el-form-item>
      </el-form>
      <el-table :data="billingRules" stripe size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="name" label="规则名称" show-overflow-tooltip />
        <el-table-column prop="billing_mode" label="模式" width="120">
          <template #default="{ row }">{{ modeLabel(row.billing_mode) }}</template>
        </el-table-column>
        <el-table-column label="时薪" width="100">
          <template #default="{ row }">{{ row.hourly_rate != null ? `¥${row.hourly_rate}` : '-' }}</template>
        </el-table-column>
        <el-table-column label="固定金额" width="100">
          <template #default="{ row }">{{ row.fixed_amount != null ? `¥${row.fixed_amount}` : '-' }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 'active' ? 'success' : 'info'" size="small">{{ row.status === 'active' ? '启用' : '停用' }}</el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-divider />

    <el-card shadow="never">
      <template #header>
        <div class="result-header">
          <span class="card-title">费用通知单</span>
          <el-button size="small" type="primary" style="margin-left:auto" @click="invoiceDialogVisible = true">创建费用通知单</el-button>
        </div>
      </template>
      <div class="filter-row">
        <el-select v-model="invoiceFilter" placeholder="按状态筛选" clearable size="small" style="width:140px" @change="loadInvoices">
          <el-option label="草稿" value="draft" />
          <el-option label="已发送" value="sent" />
          <el-option label="已支付" value="paid" />
          <el-option label="逾期" value="overdue" />
          <el-option label="已作废" value="voided" />
        </el-select>
      </div>
      <el-table :data="invoices" stripe size="small" style="margin-top:12px">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="invoice_no" label="通知单号" width="140" />
        <el-table-column prop="client_display_name" label="客户" show-overflow-tooltip />
        <el-table-column label="金额" width="120">
          <template #default="{ row }">¥{{ row.total_amount }}</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="invoiceStatusType(row.status)" size="small">{{ invoiceStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="账期" width="200">
          <template #default="{ row }">{{ row.billing_period_start }} ~ {{ row.billing_period_end }}</template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button v-if="row.status === 'draft'" size="small" text type="primary" @click="sendInvoice(row.id)">发送</el-button>
            <el-button v-if="row.status !== 'voided'" size="small" text type="danger" @click="voidInvoiceHandler(row)">作废</el-button>
            <el-button v-if="row.status === 'sent' || row.status === 'overdue'" size="small" text type="success" @click="openPaymentDialog(row)">收款</el-button>
            <el-button v-if="row.status === 'paid'" size="small" text type="warning" @click="openRefundDialog(row)">退款</el-button>
            <el-button size="small" text @click="viewInvoiceDetail(row)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination-row">
        <el-pagination
          v-model:current-page="invoicePage"
          :page-size="invoicePageSize"
          :total="invoiceTotal"
          layout="total, prev, pager, next"
          small
          @current-change="loadInvoices"
        />
      </div>
    </el-card>

    <el-dialog v-model="invoiceDialogVisible" title="创建费用通知单" width="540px">
      <el-form :model="invoiceForm" label-width="100px" size="small">
        <el-alert title="费用通知单不提供税务发票开具" type="info" :closable="false" show-icon style="margin-bottom:16px" />
        <el-form-item label="客户名称" required>
          <el-input v-model="invoiceForm.client_display_name" placeholder="客户名称" />
        </el-form-item>
        <el-form-item label="通知日期" required>
          <el-input v-model="invoiceForm.issue_date" placeholder="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="账期开始">
          <el-input v-model="invoiceForm.billing_period_start" placeholder="YYYY-MM-DD" />
        </el-form-item>
        <el-form-item label="账期结束">
          <el-input v-model="invoiceForm.billing_period_end" placeholder="YYYY-MM-DD" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="invoiceDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="invoiceCreating" @click="submitInvoice">创建</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="paymentDialogVisible" title="记录收款" width="460px">
      <el-form :model="paymentForm" label-width="100px" size="small">
        <el-form-item label="收款金额" required>
          <el-input-number v-model="paymentForm.amount" :min="0.01" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="支付方式">
          <el-select v-model="paymentForm.payment_method" style="width:100%">
            <el-option label="银行转账" value="bank_transfer" />
            <el-option label="现金" value="cash" />
            <el-option label="支付平台" value="provider" />
            <el-option label="其他" value="other" />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="paymentForm.note" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="paymentDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="paymentSaving" @click="submitPayment">确认收款</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="refundDialogVisible" title="创建退款" width="460px">
      <el-form :model="refundForm" label-width="100px" size="small">
        <el-form-item label="退款金额" required>
          <el-input-number v-model="refundForm.amount" :min="0.01" :precision="2" style="width:100%" />
        </el-form-item>
        <el-form-item label="退款原因" required>
          <el-input v-model="refundForm.reason" type="textarea" :rows="2" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="refundDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="refundSaving" @click="submitRefund">确认退款</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="detailDialogVisible" title="费用通知单详情" width="600px">
      <el-descriptions v-if="detailInvoice" :column="2" border size="small">
        <el-descriptions-item label="通知单号">{{ detailInvoice.invoice_no }}</el-descriptions-item>
        <el-descriptions-item label="客户">{{ detailInvoice.client_display_name }}</el-descriptions-item>
        <el-descriptions-item label="金额">¥{{ detailInvoice.total_amount }}</el-descriptions-item>
        <el-descriptions-item label="状态">
          <el-tag :type="invoiceStatusType(detailInvoice.status)" size="small">{{ invoiceStatusLabel(detailInvoice.status) }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="账期" :span="2">{{ detailInvoice.billing_period_start }} ~ {{ detailInvoice.billing_period_end }}</el-descriptions-item>
      </el-descriptions>
      <el-divider content-position="left">收款记录</el-divider>
      <el-table :data="detailPayments" stripe size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column label="金额" width="100">
          <template #default="{ row }">¥{{ row.amount }}</template>
        </el-table-column>
        <el-table-column prop="payment_method" label="支付方式" width="120" />
        <el-table-column prop="note" label="备注" show-overflow-tooltip />
        <el-table-column label="时间" width="160">
          <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
        </el-table-column>
      </el-table>
      <el-empty v-if="!detailPayments.length" description="暂无收款记录" :image-size="48" />
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElDescriptions, ElDescriptionsItem } from 'element-plus/es/components/descriptions/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElInputNumber } from 'element-plus/es/components/input-number/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/descriptions/style/css'
import 'element-plus/es/components/descriptions-item/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/input-number/style/css'
import 'element-plus/es/components/pagination/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tag/style/css'
import api from '../api'

const props = defineProps({
  orgId: { type: Number, required: true },
  caseId: { type: Number, required: false, default: null },
})

const noCase = computed(() => !props.caseId)

// --- Timer ---
const timerDescription = ref('')
const runningEntry = ref(null)
const elapsedSeconds = ref(0)
let timerInterval = null

const startTimer = async () => {
  if (noCase.value) return ElMessage.warning('请先在顶部选择案件')
  // 与手动录入一致：后端 TimeEntryCreate.description min_length=1，空描述会 422
  if (!timerDescription.value.trim()) return ElMessage.warning('请输入计时描述')
  try {
    const { data } = await api.createTimeEntry(props.orgId, props.caseId, {
      description: timerDescription.value,
      case_id: props.caseId,
    })
    runningEntry.value = data
    elapsedSeconds.value = elapsedSecondsForEntry(data)
    startInterval()
    timerDescription.value = ''
    loadTimeEntries()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '启动计时失败')
  }
}

const pauseTimer = async () => {
  if (!runningEntry.value) return
  try {
    const { data } = await api.patchTimeEntry(runningEntry.value.id, { action: 'pause' })
    runningEntry.value = data
    stopInterval()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '暂停失败')
  }
}

const resumeTimer = async () => {
  if (!runningEntry.value) return
  try {
    const { data } = await api.patchTimeEntry(runningEntry.value.id, { action: 'resume' })
    runningEntry.value = data
    startInterval()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '继续计时失败')
  }
}

const stopTimer = async () => {
  if (!runningEntry.value) return
  try {
    const { data } = await api.patchTimeEntry(runningEntry.value.id, { action: 'complete' })
    runningEntry.value = null
    stopInterval()
    elapsedSeconds.value = 0
    loadTimeEntries()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '完成计时失败')
  }
}

const startInterval = () => {
  stopInterval()
  timerInterval = setInterval(() => { elapsedSeconds.value += 1 }, 1000)
}

const stopInterval = () => {
  if (timerInterval) { clearInterval(timerInterval); timerInterval = null }
}

// --- Manual entry ---
const manualForm = ref({ description: '', duration_minutes: 30 })
const manualLoading = ref(false)

const submitManualEntry = async () => {
  if (noCase.value) return ElMessage.warning('请先在顶部选择案件')
  if (!manualForm.value.description.trim()) return ElMessage.warning('请输入描述')
  manualLoading.value = true
  try {
    await api.createTimeEntry(props.orgId, props.caseId, {
      description: manualForm.value.description,
      duration_minutes: manualForm.value.duration_minutes,
      case_id: props.caseId,
    })
    ElMessage.success('工时已录入')
    manualForm.value = { description: '', duration_minutes: 30 }
    loadTimeEntries()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '录入失败')
  }
  manualLoading.value = false
}

// --- Time entries list ---
const timeEntries = ref([])
const entryPage = ref(1)
const entryPageSize = ref(20)
const entryTotal = ref(0)

const loadTimeEntries = async () => {
  if (noCase.value) { timeEntries.value = []; entryTotal.value = 0; runningEntry.value = null; stopInterval(); return }
  try {
    const { data } = await api.listTimeEntries(props.orgId, props.caseId, entryPage.value, entryPageSize.value)
    timeEntries.value = data.items || data
    entryTotal.value = data.total || (Array.isArray(data) ? data.length : 0)
    // Check for running/paused entry
    const active = (data.items || data).find(e => e.status === 'running' || e.status === 'paused')
    if (active) {
      runningEntry.value = active
      elapsedSeconds.value = elapsedSecondsForEntry(active)
      if (active.status === 'running') startInterval()
    }
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '工时记录加载失败')
  }
}

const patchEntry = async (entryId, payload) => {
  try {
    await api.patchTimeEntry(entryId, payload)
    ElMessage.success('状态已更新')
    if (runningEntry.value?.id === entryId && (payload.action === 'complete' || payload.action === 'void')) {
      runningEntry.value = null
      stopInterval()
      elapsedSeconds.value = 0
    }
    loadTimeEntries()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

// --- Billing rules ---
const billingRules = ref([])
const ruleForm = ref({ name: '', billing_mode: 'hourly', hourly_rate: 500, fixed_amount: 0 })
const ruleLoading = ref(false)

const loadBillingRules = async () => {
  try {
    const { data } = await api.listBillingRules(props.orgId, props.caseId)
    billingRules.value = data
  } catch {}
}

const submitBillingRule = async () => {
  if (!ruleForm.value.name.trim()) return ElMessage.warning('请输入规则名称')
  ruleLoading.value = true
  try {
    await api.createBillingRule(props.orgId, { ...ruleForm.value, case_id: props.caseId || null })
    ElMessage.success('计费规则已创建')
    ruleForm.value = { name: '', billing_mode: 'hourly', hourly_rate: 500, fixed_amount: 0 }
    loadBillingRules()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  }
  ruleLoading.value = false
}

// --- Invoices ---
const invoices = ref([])
const invoicePage = ref(1)
const invoicePageSize = ref(20)
const invoiceTotal = ref(0)
const invoiceFilter = ref('')

const loadInvoices = async () => {
  try {
    const { data } = await api.listInvoices(props.orgId, props.caseId, invoiceFilter.value, invoicePage.value, invoicePageSize.value)
    invoices.value = data.items || data
    invoiceTotal.value = data.total || (Array.isArray(data) ? data.length : 0)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '计费规则加载失败')
  }
}

const invoiceDialogVisible = ref(false)
const invoiceCreating = ref(false)
const invoiceForm = ref({ client_display_name: '', issue_date: '', billing_period_start: '', billing_period_end: '' })

const submitInvoice = async () => {
  if (!invoiceForm.value.client_display_name.trim()) return ElMessage.warning('请输入客户名称')
  if (!invoiceForm.value.issue_date) return ElMessage.warning('请输入通知日期')
  invoiceCreating.value = true
  try {
    // 账期可空字段以 null 发送（后端 Optional[date] 拒绝空串）
    const payload = {
      ...invoiceForm.value,
      billing_period_start: invoiceForm.value.billing_period_start || null,
      billing_period_end: invoiceForm.value.billing_period_end || null,
      case_id: props.caseId,
    }
    await api.createInvoice(props.orgId, payload)
    ElMessage.success('费用通知单已创建')
    invoiceDialogVisible.value = false
    invoiceForm.value = { client_display_name: '', issue_date: '', billing_period_start: '', billing_period_end: '' }
    loadInvoices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建费用通知单失败')
  }
  invoiceCreating.value = false
}

const sendInvoice = async (id) => {
  try {
    await api.sendInvoice(id)
    ElMessage.success('已创建外发草稿，待审批并发送成功后生效')
    loadInvoices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '发送失败')
  }
}

const voidInvoiceHandler = async (row) => {
  try {
    const { value: reason } = await ElMessageBox.prompt('请输入作废原因', `作废账单 #${row.id}`, {
      confirmButtonText: '确认作废',
      cancelButtonText: '取消',
      inputPlaceholder: '作废原因...',
    })
    await api.voidInvoice(row.id, reason || '')
    ElMessage.success('账单已作废')
    loadInvoices()
  } catch (e) {
    if (e === 'cancel' || e === 'close') return
    ElMessage.error(e.response?.data?.detail || '费用通知单加载失败')
  }
}

// --- Payment ---
const paymentDialogVisible = ref(false)
const paymentSaving = ref(false)
const currentInvoice = ref(null)
const paymentForm = ref({ amount: 0, payment_method: 'bank_transfer', note: '' })

const openPaymentDialog = (row) => {
  currentInvoice.value = row
  paymentForm.value = { amount: row.total_amount, payment_method: 'bank_transfer', note: '' }
  paymentDialogVisible.value = true
}

const submitPayment = async () => {
  if (paymentForm.value.amount <= 0) return ElMessage.warning('收款金额必须大于0')
  paymentSaving.value = true
  try {
    await api.recordPayment(currentInvoice.value.id, paymentForm.value)
    ElMessage.success('收款已记录')
    paymentDialogVisible.value = false
    loadInvoices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '收款记录失败')
  }
  paymentSaving.value = false
}

// --- Refund ---
const refundDialogVisible = ref(false)
const refundSaving = ref(false)
const refundForm = ref({ amount: 0, reason: '' })

const openRefundDialog = (row) => {
  currentInvoice.value = row
  refundForm.value = { amount: row.total_amount, reason: '' }
  refundDialogVisible.value = true
}

const submitRefund = async () => {
  if (refundForm.value.amount <= 0) return ElMessage.warning('退款金额必须大于0')
  if (!refundForm.value.reason.trim()) return ElMessage.warning('请输入退款原因')
  refundSaving.value = true
  try {
    await api.createRefund(currentInvoice.value.id, refundForm.value)
    ElMessage.success('退款已创建')
    refundDialogVisible.value = false
    loadInvoices()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '退款创建失败')
  }
  refundSaving.value = false
}

// --- Invoice detail ---
const detailDialogVisible = ref(false)
const detailInvoice = ref(null)
const detailPayments = ref([])

const viewInvoiceDetail = async (row) => {
  detailInvoice.value = row
  detailDialogVisible.value = true
  try {
    const { data } = await api.listPayments(row.id)
    detailPayments.value = data
  } catch {
    detailPayments.value = []
  }
}

// --- Helpers ---
const formatDuration = (seconds) => {
  if (!seconds) return '00:00:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

const elapsedSecondsForEntry = (entry) => {
  const completedSeconds = Number(entry.duration_minutes || 0) * 60
  if (completedSeconds || entry.status !== 'running') return completedSeconds
  const startedAt = Date.parse(entry.started_at || '')
  return Number.isNaN(startedAt) ? 0 : Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
}

const formatEntryDuration = (entry) => formatDuration(elapsedSecondsForEntry(entry))

const entryStatusType = (s) => ({ running: 'danger', paused: 'warning', completed: 'success', voided: 'info' }[s] || 'info')
const entryStatusLabel = (s) => ({ running: '计时中', paused: '已暂停', completed: '已完成', voided: '已作废' }[s] || s)
const modeLabel = (m) => ({ hourly: '按小时', fixed_stage: '固定阶段', hybrid: '混合' }[m] || m)
const invoiceStatusType = (s) => ({ draft: 'info', sent: 'warning', paid: 'success', overdue: 'danger', voided: 'info' }[s] || 'info')
const invoiceStatusLabel = (s) => ({ draft: '草稿', sent: '已发送', paid: '已支付', overdue: '逾期', voided: '已作废' }[s] || s)
const formatDate = (v) => {
  if (!v) return ''
  return String(v).replace('T', ' ').slice(0, 16)
}

onMounted(() => {
  loadTimeEntries()
  loadBillingRules()
  loadInvoices()
})

onUnmounted(() => {
  stopInterval()
})
</script>

<style scoped>
.legal-billing {
  max-width: 1200px;
  margin: 0 auto;
}

.no-case-hint {
  background: var(--color-warning-light);
  border: 1px solid #F0E2CC;
  color: var(--color-warning);
  border-radius: 6px;
  padding: 10px 14px;
  margin-bottom: 16px;
  font-size: 13px;
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

.filter-row {
  display: flex;
  gap: 12px;
  margin-bottom: 8px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.timer-section {
  display: flex;
  align-items: center;
}

.timer-running {
  display: flex;
  align-items: center;
  gap: 16px;
  width: 100%;
}

.timer-display {
  display: flex;
  align-items: center;
  gap: 12px;
}

.timer-actions {
  display: flex;
  gap: 8px;
}

.timer-start {
  display: flex;
  align-items: center;
  gap: 12px;
}
</style>
