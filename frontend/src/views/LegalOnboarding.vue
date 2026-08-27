<template>
  <main class="onboarding">
    <el-card class="onboarding-card">
      <div class="role-row">
        <span class="role-label">我的角色</span>
        <el-radio-group v-model="role">
          <el-radio-button :value="'solo_lawyer'">独立律师</el-radio-button>
          <el-radio-button :value="'firm_admin'">律所管理员</el-radio-button>
          <el-radio-button :value="'enterprise_legal'">企业法务</el-radio-button>
        </el-radio-group>
      </div>
      <el-steps direction="vertical" :active="completed.length" class="steps">
        <el-step v-for="item in steps" :key="item" :title="item" />
      </el-steps>
      <div class="onboarding-actions">
        <el-button @click="complete">保存进度</el-button>
        <el-button type="primary" @click="enterWorkspace">进入工作台</el-button>
      </div>
    </el-card>
  </main>
</template>
<script setup>
import { onMounted, ref, computed } from 'vue'
import { useRouter } from 'vue-router'
import http from '../api/http'
import { ElMessage } from 'element-plus'
import { ElCard } from 'element-plus/es/components/card/index'
import { ElRadioGroup, ElRadioButton } from 'element-plus/es/components/radio/index'
import { ElSteps, ElStep } from 'element-plus/es/components/steps/index'
import 'element-plus/es/components/card/style/css'
import 'element-plus/es/components/radio-group/style/css'
import 'element-plus/es/components/radio-button/style/css'
import 'element-plus/es/components/steps/style/css'
import 'element-plus/es/components/step/style/css'

const router = useRouter()
const role = ref('solo_lawyer')
const completed = ref([])
const steps = computed(() => ({
  solo_lawyer: ['创建案件', '完成合同审查', '发布客户门户'],
  firm_admin: ['配置成员', '创建审查策略', '查看运营数据'],
  enterprise_legal: ['创建合同台账', '设置关键日期', '发起审批'],
}[role.value]))

onMounted(async () => {
  try {
    const { data } = await http.get('/developer/onboarding')
    if (data) {
      role.value = data.user_role || role.value
      let parsed = []
      try {
        parsed = JSON.parse(data.completed_steps_json || '[]')
      } catch (e) {
        console.warn('解析 completed_steps_json 失败', e)
        parsed = []
      }
      completed.value = Array.isArray(parsed) ? parsed : []
    }
  } catch {}
})

async function complete() {
  completed.value = steps.value
  try {
    await http.put('/developer/onboarding', { user_role: role.value, completed_steps_json: JSON.stringify(completed.value) })
    ElMessage.success('引导进度已保存')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '保存引导进度失败')
  }
}

function enterWorkspace() {
  router.push('/legal-workspace')
}
</script>
<style scoped>
.onboarding { max-width: 640px; }
.onboarding-card { padding: 0; }
.role-row { display: flex; align-items: center; gap: 12px; margin-bottom: 4px; }
.role-label { font-size: var(--text-sm); color: var(--color-text-muted); }
.steps { margin: 24px 0 8px; }
.onboarding-actions { display: flex; justify-content: flex-end; gap: 12px; padding-top: 16px; border-top: 1px solid var(--color-border); }
</style>
