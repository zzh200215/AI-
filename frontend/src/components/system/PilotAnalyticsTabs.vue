<template>
  <div class="pilot-analytics-tabs">
    <template v-if="section !== 'retention'">
      <div class="app-section-intro tab-intro">
      <strong>试点转化漏斗（P-1）</strong>
      <span>注册 → 首次咨询 → 首次审查 → 首次文书 → 首次审核通过 → 升级付费，定位最大流失点。数据由既有业务表推导，可追溯历史，无需额外埋点。</span>
    </div>

    <el-card class="system-panel-card">
      <div class="app-toolbar">
        <span>注册队列窗口：</span>
        <el-radio-group v-model="funnelDays" @change="fetchFunnel">
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button :loading="funnelLoading" @click="fetchFunnel" style="margin-left: auto">刷新</el-button>
      </div>
    </el-card>

    <el-row :gutter="16" class="system-block-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="队列注册" :value="funnelData?.cohort?.registered || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="激活·首次咨询" :value="funnelData?.activation?.cohort_users_with_consultation || 0" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="注册→首次咨询(天)" :value="funnelData?.activation?.avg_days_reg_to_first_consult ?? '—'" />
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="升级付费" :value="funnelUpgradedUsers" />
        </el-card>
      </el-col>
    </el-row>

    <el-card class="system-panel-card">
      <el-table :data="funnelRows" v-loading="funnelLoading" border size="small">
        <el-table-column prop="label" label="阶段" width="150" />
        <el-table-column prop="users" label="人数" width="90" />
        <el-table-column label="占注册比例" width="120">
          <template #default="{ row }">{{ row.overall_pct }}%</template>
        </el-table-column>
        <el-table-column label="上一跳转化" width="120">
          <template #default="{ row }">
            <span v-if="row.stage === 'registered'">—</span>
            <template v-else>{{ row.hop_pct }}%</template>
          </template>
        </el-table-column>
        <el-table-column label="相对占比">
          <template #default="{ row }">
            <div class="funnel-bar-track">
              <div class="funnel-bar" :style="{ width: row.width + '%' }"></div>
            </div>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    </template>

    <template v-if="section !== 'funnel'">
      <div class="app-section-intro tab-intro">
        <strong>北极星指标 + 新用户留存（P-5）</strong>
      <span>按周跟踪活跃律师与案件闭环信号；按注册周分群计算 7 日 / 30 日留存。未完全观察的窗口显示「—」。</span>
    </div>

    <el-card class="system-panel-card">
      <div class="app-toolbar">
        <span>北极星周期：</span>
        <el-radio-group v-model="northStarWeeks" @change="fetchNorthStar">
          <el-radio-button :value="4">近 4 周</el-radio-button>
          <el-radio-button :value="12">近 12 周</el-radio-button>
          <el-radio-button :value="26">近 26 周</el-radio-button>
        </el-radio-group>
        <el-button :loading="northStarLoading" @click="fetchNorthStar" style="margin-left: 12px">刷新</el-button>
      </div>
    </el-card>

    <el-row :gutter="16" class="system-block-row">
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="本周活跃律师" :value="northStarCurrent.active_lawyers" />
          <div :style="deltaStyle(northStarChangePct.active_lawyers)">{{ deltaText(northStarChangePct.active_lawyers) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="持进行中案件" :value="northStarCurrent.with_active_case" />
          <div :style="deltaStyle(northStarChangePct.with_active_case)">{{ deltaText(northStarChangePct.with_active_case) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="本周任务数" :value="northStarCurrent.tasks" />
          <div :style="deltaStyle(northStarChangePct.tasks)">{{ deltaText(northStarChangePct.tasks) }}</div>
        </el-card>
      </el-col>
      <el-col :span="6">
        <el-card shadow="hover">
          <el-statistic title="案件内任务" :value="northStarCurrent.case_tasks" />
          <div :style="deltaStyle(northStarChangePct.case_tasks)">{{ deltaText(northStarChangePct.case_tasks) }}</div>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="system-panel-card">
      <el-table :data="northStarWeekly" v-loading="northStarLoading" border size="small">
        <el-table-column prop="week_start" label="周（周一首）" width="130" />
        <el-table-column prop="active_lawyers" label="活跃律师" width="110" />
        <el-table-column prop="with_active_case" label="持进行中案件" width="130" />
        <el-table-column prop="tasks" label="任务数" width="90" />
        <el-table-column prop="case_tasks" label="案件内任务" width="110" />
      </el-table>
    </el-card>

    <el-card class="system-panel-card">
      <div class="app-toolbar">
        <span>留存窗口：</span>
        <el-radio-group v-model="retentionDays" @change="fetchRetention">
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button :loading="retentionLoading" @click="fetchRetention" style="margin-left: 12px">刷新</el-button>
      </div>
    </el-card>

    <el-card class="system-panel-card">
      <el-table :data="retentionCohorts" v-loading="retentionLoading" border size="small">
        <el-table-column prop="week_start" label="注册周" width="130" />
        <el-table-column prop="cohort_size" label="新用户" width="90" />
        <el-table-column label="D7 留存" width="160">
          <template #default="{ row }">{{ rateText(row.d7) }}</template>
        </el-table-column>
        <el-table-column label="D30 留存" width="160">
          <template #default="{ row }">{{ rateText(row.d30) }}</template>
        </el-table-column>
        <el-table-column label="说明" min-width="240">
          <template #default="{ row }">{{ windowNote(row) }}</template>
        </el-table-column>
      </el-table>
    </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { ElMessage } from 'element-plus/es/components/message/index'
import { ElRadioButton, ElRadioGroup } from 'element-plus/es/components/radio/index'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElStatistic } from 'element-plus/es/components/statistic/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/radio/style/css'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/statistic/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/button/style/css'
import api from '../../api'
import { useSystemFunnel } from '../../composables/useSystemFunnel'
import { useSystemRetention } from '../../composables/useSystemRetention'

const {
  funnelDays, funnelData, funnelLoading, funnelRows, funnelUpgradedUsers, fetchFunnel,
} = useSystemFunnel({ client: api, message: ElMessage })
const {
  retentionDays, retentionData, retentionLoading, fetchRetention,
  northStarWeeks, northStarData, northStarLoading, fetchNorthStar,
} = useSystemRetention({ client: api, message: ElMessage })

const props = defineProps({
  section: { type: String, default: 'all' },
})

onMounted(async () => {
  const jobs = []
  if (props.section !== 'retention') jobs.push(fetchFunnel())
  if (props.section !== 'funnel') jobs.push(fetchRetention(), fetchNorthStar())
  await Promise.allSettled(jobs)
})

const northStarCurrent = computed(() => northStarData.value?.current || { active_lawyers: 0, with_active_case: 0, tasks: 0, case_tasks: 0 })
const northStarChangePct = computed(() => northStarData.value?.weekly_change_pct || {})
const northStarWeekly = computed(() => northStarData.value?.weekly || [])
const retentionCohorts = computed(() => retentionData.value?.cohorts || [])

const deltaText = (pct) => (pct == null ? '较上周 —' : `较上周 ${pct >= 0 ? '+' : ''}${pct}%`)
const deltaStyle = (pct) => {
  if (pct == null) return { color: 'var(--color-text-muted)', fontSize: '12px' }
  return { color: pct >= 0 ? 'var(--color-success)' : 'var(--color-danger)', fontSize: '12px' }
}
const rateText = (metric) => {
  if (!metric || metric.rate == null) return '—（未完全观察）'
  return `${(metric.rate * 100).toFixed(1)}%（${metric.active}/${metric.observed}）`
}
const windowNote = (row) => {
  const parts = []
  if (!row.d7 || row.d7.observed === 0) parts.push('D7 窗口未经历')
  if (!row.d30 || row.d30.observed === 0) parts.push('D30 窗口未经历')
  return parts.length ? parts.join('；') : 'D7/D30 窗口均已完全观察'
}
</script>

<style scoped>
.pilot-analytics-tabs {
  display: grid;
  gap: var(--space-4);
}
.funnel-bar-track {
  width: 100%;
  height: 14px;
  border-radius: 7px;
  background: var(--color-border);
  overflow: hidden;
}
.funnel-bar {
  height: 100%;
  border-radius: 7px;
  background: var(--color-primary);
  transition: width 0.3s ease;
}
</style>
