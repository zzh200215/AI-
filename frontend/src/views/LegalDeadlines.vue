<template>
  <div class="legal-deadlines">
    <div v-if="noCase" class="no-case-hint">当前未选择案件。「创建期限」「期限列表」需在顶部案件选择器中选择案件后使用。</div>
    <el-card shadow="never">
      <template #header><span class="card-title">创建期限</span></template>
      <el-form :model="deadlineForm" label-width="100px" size="small" @submit.prevent="submitDeadline">
        <el-row :gutter="16">
          <el-col :span="8">
            <el-form-item label="类型" required>
              <el-select v-model="deadlineForm.deadline_type" style="width:100%">
                <el-option v-for="t in deadlineTypes" :key="t.value" :label="t.label" :value="t.value" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="截止日期" required>
              <el-input v-model="deadlineForm.deadline_at" placeholder="YYYY-MM-DD" />
            </el-form-item>
          </el-col>
          <el-col :span="8">
            <el-form-item label="负责人">
              <el-select v-model="deadlineForm.owner_id" placeholder="选择负责人" style="width:100%">
                <el-option v-for="m in orgMembers" :key="m.user_id" :label="m.username || String(m.user_id)" :value="m.user_id" />
              </el-select>
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述">
          <el-input v-model="deadlineForm.description" type="textarea" :rows="2" placeholder="期限相关说明..." />
        </el-form-item>
        <el-form-item label="提醒">
          <el-select v-model="deadlineForm.reminder_offsets" multiple collapse-tags style="width:100%" placeholder="选择提醒时间">
            <el-option label="提前1天" :value="1" />
            <el-option label="提前3天" :value="3" />
            <el-option label="提前7天" :value="7" />
            <el-option label="提前14天" :value="14" />
            <el-option label="提前30天" :value="30" />
          </el-select>
        </el-form-item>
        <el-button type="primary" :loading="deadlineLoading" @click="submitDeadline">创建期限</el-button>
      </el-form>
    </el-card>

    <el-card shadow="never" style="margin-top:20px">
      <template #header>
        <div class="result-header">
          <span class="card-title">期限列表</span>
          <el-select v-model="deadlineFilter" placeholder="按状态筛选" clearable size="small" style="width:140px; margin-left:auto" @change="loadDeadlines">
            <el-option label="进行中" value="active" />
            <el-option label="已完成" value="completed" />
            <el-option label="已取消" value="cancelled" />
          </el-select>
        </div>
      </template>
      <el-table :data="deadlines" stripe size="small">
        <el-table-column prop="id" label="ID" width="60" />
        <el-table-column prop="deadline_type" label="类型" width="120">
          <template #default="{ row }">
            <el-tag :color="typeColor(row.deadline_type)" effect="dark" size="small" style="border:none; color:#fff">{{ typeLabel(row.deadline_type) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="截止日期" width="120">
          <template #default="{ row }">{{ row.deadline_at?.slice(0, 10) }}</template>
        </el-table-column>
        <el-table-column label="负责人" width="100">
          <template #default="{ row }">{{ orgMembers.find(m => m.user_id === row.owner_id)?.username || row.owner_id }}</template>
        </el-table-column>
        <el-table-column prop="description" label="描述" show-overflow-tooltip />
        <el-table-column prop="status" label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="deadlineStatusType(row.status)" size="small">{{ deadlineStatusLabel(row.status) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="200">
          <template #default="{ row }">
            <el-button v-if="row.status === 'active'" size="small" text type="success" @click="completeDeadline(row.id)">完成</el-button>
            <el-button v-if="row.status === 'active'" size="small" text type="warning" @click="cancelDeadline(row.id)">取消</el-button>
            <el-button v-if="row.status === 'active'" size="small" text type="primary" @click="addToCalendar(row.id)">加入日历</el-button>
          </template>
        </el-table-column>
      </el-table>
      <div class="pagination-row">
        <el-pagination
          v-model:current-page="deadlinePage"
          :page-size="deadlinePageSize"
          :total="deadlineTotal"
          layout="total, prev, pager, next"
          small
          @current-change="loadDeadlines"
        />
      </div>
    </el-card>

    <el-divider />

    <el-card shadow="never">
      <template #header>
        <div class="result-header">
          <span class="card-title">日历视图</span>
          <div style="margin-left:auto; display:flex; gap:8px; align-items:center">
            <el-button size="small" @click="prevMonth">&lt;</el-button>
            <span class="calendar-month">{{ calendarYear }}年{{ calendarMonth }}月</span>
            <el-button size="small" @click="nextMonth">&gt;</el-button>
          </div>
        </div>
      </template>
      <div class="calendar-grid">
        <div class="calendar-header" v-for="d in weekDays" :key="d">{{ d }}</div>
        <div
          v-for="(cell, idx) in calendarCells"
          :key="idx"
          class="calendar-cell"
          :class="{ 'other-month': !cell.currentMonth, 'today': cell.isToday }"
          @click="cell.deadline && selectDeadline(cell.deadline)"
        >
          <span class="cell-day">{{ cell.day }}</span>
          <div v-if="cell.deadline" class="cell-dot" :style="{ background: typeColor(cell.deadline.deadline_type) }" :title="typeLabel(cell.deadline.deadline_type)"></div>
        </div>
      </div>
      <div class="calendar-legend">
        <span v-for="t in deadlineTypes" :key="t.value" class="legend-item">
          <span class="legend-dot" :style="{ background: typeColor(t.value) }"></span>
          {{ t.label }}
        </span>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElCol } from 'element-plus/es/components/col/index'
import { ElRow } from 'element-plus/es/components/row/index'
import { ElDivider } from 'element-plus/es/components/divider/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElPagination } from 'element-plus/es/components/pagination/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/col/style/css'
import 'element-plus/es/components/row/style/css'
import 'element-plus/es/components/divider/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
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

const orgMembers = ref([])
const loadOrgMembers = async () => {
  try {
    const { data } = await api.listOrgMembers(props.orgId)
    orgMembers.value = Array.isArray(data) ? data : (data.members || data.items || [])
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '组织成员加载失败')
  }
}

// --- Deadline form ---
const deadlineTypes = [
  { value: 'hearing', label: '开庭' },
  { value: 'defense', label: '答辩' },
  { value: 'appeal', label: '上诉' },
  { value: 'performance', label: '履行' },
  { value: 'payment', label: '付款' },
  { value: 'expiry', label: '届满' },
  { value: 'custom', label: '自定义' },
]

const typeColor = (t) => ({
  hearing: 'var(--color-cat-hearing)', defense: 'var(--color-cat-defense)', appeal: 'var(--color-cat-appeal)',
  performance: 'var(--color-cat-performance)', payment: 'var(--color-cat-payment)',
  expiry: 'var(--color-cat-expiry)', custom: 'var(--color-cat-custom)',
}[t] || 'var(--color-cat-payment)')

const typeLabel = (t) => deadlineTypes.find(d => d.value === t)?.label || t

const deadlineForm = ref({ deadline_type: 'hearing', deadline_at: '', owner_id: null, description: '', reminder_offsets: [] })
const deadlineLoading = ref(false)

const submitDeadline = async () => {
  if (noCase.value) return ElMessage.warning('请先在顶部选择案件')
  if (!deadlineForm.value.deadline_at) return ElMessage.warning('请选择截止日期')
  if (!deadlineForm.value.owner_id) return ElMessage.warning('请选择负责人')
  deadlineLoading.value = true
  try {
    const payload = {
      deadline_type: deadlineForm.value.deadline_type,
      deadline_at: deadlineForm.value.deadline_at + 'T00:00:00',
      owner_id: deadlineForm.value.owner_id,
      description: deadlineForm.value.description,
      reminder_offsets_json: deadlineForm.value.reminder_offsets.length
        ? JSON.stringify(deadlineForm.value.reminder_offsets)
        : undefined,
    }
    await api.createDeadline(props.orgId, props.caseId, payload)
    ElMessage.success('期限已创建')
    deadlineForm.value = { deadline_type: 'hearing', deadline_at: '', owner_id: null, description: '', reminder_offsets: [] }
    loadDeadlines()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建失败')
  }
  deadlineLoading.value = false
}

// --- Deadline list ---
const deadlines = ref([])
const deadlinePage = ref(1)
const deadlinePageSize = ref(20)
const deadlineTotal = ref(0)
const deadlineFilter = ref('')

const loadDeadlines = async () => {
  if (noCase.value) { deadlines.value = []; deadlineTotal.value = 0; return }
  try {
    const { data } = await api.listDeadlines(props.orgId, props.caseId, deadlineFilter.value, deadlinePage.value, deadlinePageSize.value)
    deadlines.value = data.items || data
    deadlineTotal.value = data.total || (Array.isArray(data) ? data.length : 0)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '关键日期加载失败')
  }
}

const completeDeadline = async (id) => {
  try {
    await api.patchDeadline(id, { action: 'complete' })
    ElMessage.success('已标记完成')
    loadDeadlines()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

const cancelDeadline = async (id) => {
  try {
    await api.patchDeadline(id, { action: 'cancel' })
    ElMessage.success('已取消')
    loadDeadlines()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '操作失败')
  }
}

const addToCalendar = async (id) => {
  try {
    await api.deadlineToCalendar(id)
    ElMessage.success('已添加到日历')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '添加日历失败')
  }
}

const selectDeadline = (d) => {
  ElMessage.info(`${typeLabel(d.deadline_type)}：${d.deadline_at?.slice(0, 10)}${d.description ? ' - ' + d.description : ''}`)
}

// --- Calendar ---
const calendarYear = ref(new Date().getFullYear())
const calendarMonth = ref(new Date().getMonth() + 1)
const weekDays = ['一', '二', '三', '四', '五', '六', '日']

const prevMonth = () => {
  if (calendarMonth.value === 1) { calendarMonth.value = 12; calendarYear.value-- }
  else calendarMonth.value--
}

const nextMonth = () => {
  if (calendarMonth.value === 12) { calendarMonth.value = 1; calendarYear.value++ }
  else calendarMonth.value++
}

const calendarCells = computed(() => {
  const y = calendarYear.value
  const m = calendarMonth.value
  const firstDay = new Date(y, m - 1, 1)
  let startWeekday = firstDay.getDay() - 1
  if (startWeekday < 0) startWeekday = 6
  const daysInMonth = new Date(y, m, 0).getDate()
  const prevMonthDays = new Date(y, m - 1, 0).getDate()

  const deadlineMap = {}
  deadlines.value.forEach(d => {
    if (d.deadline_at) {
      const key = d.deadline_at.slice(0, 10)
      if (!deadlineMap[key]) deadlineMap[key] = d
    }
  })

  const today = new Date()
  const todayStr = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`

  const cells = []
  for (let i = 0; i < startWeekday; i++) {
    cells.push({ day: prevMonthDays - startWeekday + 1 + i, currentMonth: false, deadline: null, isToday: false })
  }
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    cells.push({ day: d, currentMonth: true, deadline: deadlineMap[dateStr] || null, isToday: dateStr === todayStr })
  }
  const remaining = 42 - cells.length
  for (let i = 1; i <= remaining; i++) {
    cells.push({ day: i, currentMonth: false, deadline: null, isToday: false })
  }
  return cells
})

const deadlineStatusType = (s) => ({ active: 'warning', completed: 'success', cancelled: 'info' }[s] || 'info')
const deadlineStatusLabel = (s) => ({ active: '进行中', completed: '已完成', cancelled: '已取消' }[s] || s)

onMounted(() => {
  loadDeadlines()
  loadOrgMembers()
})
</script>

<style scoped>
.legal-deadlines {
  max-width: 1200px;
  margin: 0 auto;
}

.card-title {
  font-weight: 700;
  font-size: 15px;
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

.result-header {
  display: flex;
  align-items: center;
  gap: 10px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}

.calendar-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 2px;
}

.calendar-header {
  text-align: center;
  font-weight: 600;
  font-size: 13px;
  padding: 8px 0;
  color: var(--color-text-muted);
}

.calendar-cell {
  min-height: 60px;
  padding: 4px 6px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
  cursor: default;
  position: relative;
}

.calendar-cell.other-month {
  background: var(--el-fill-color-lighter);
  opacity: 0.5;
}

.calendar-cell.today {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.cell-day {
  font-size: 12px;
  font-weight: 600;
}

.cell-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  position: absolute;
  bottom: 4px;
  right: 4px;
}

.calendar-month {
  font-weight: 700;
  font-size: 14px;
  min-width: 90px;
  text-align: center;
}

.calendar-legend {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  flex-wrap: wrap;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--color-text-secondary);
}

.legend-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}
</style>
