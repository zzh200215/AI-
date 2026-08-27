<template>
  <div class="login-page">
    <div class="login-brand">
      <div class="brand-content">
        <div class="brand-title-row">
          <div class="brand-mark">律</div>
          <div>
            <h1 class="brand-title">律智检</h1>
            <p class="brand-subtitle">法律检索、合同审查、文书草稿与律师审核</p>
          </div>
        </div>
        <ul class="brand-capabilities">
          <li>
            <strong>法规与案例检索</strong>
            <span>混合召回与重排序，答案标注引用出处与法源有效性</span>
          </li>
          <li>
            <strong>合同条款风险识别</strong>
            <span>逐条定位风险与缺失义务，给出页码与原文依据</span>
          </li>
          <li>
            <strong>文书草稿与律师审核</strong>
            <span>草稿生成后转入律师审核，保留修改痕迹与版本记录</span>
          </li>
        </ul>
      </div>
      <p class="brand-footer">本系统用于法律工作辅助，模型输出需由具备资质的人员复核后使用。</p>
    </div>

    <div class="login-form-panel">
      <div class="form-container">
        <div class="form-header">
          <h2>账号登录</h2>
          <p>登录后进入法律工作台</p>
          <el-tabs v-model="tab" class="login-tabs">
            <el-tab-pane label="登录" name="login" />
            <el-tab-pane label="注册" name="register" />
          </el-tabs>
        </div>

        <el-form v-show="tab === 'login'" @submit.prevent="handleLogin" class="login-form">
          <el-form-item>
            <el-input v-model="loginForm.username" placeholder="用户名" :prefix-icon="UserIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="loginForm.password" type="password" placeholder="密码" show-password :prefix-icon="LockIcon" size="large" />
          </el-form-item>
          <el-button type="primary" :loading="loading" @click="handleLogin" class="submit-btn" size="large">
            {{ loading ? '登录中...' : '登录' }}
          </el-button>
        </el-form>

        <el-form v-show="tab === 'register'" @submit.prevent="handleRegister" class="login-form">
          <el-form-item>
            <el-input v-model="regForm.username" placeholder="用户名" :prefix-icon="UserIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="regForm.email" placeholder="邮箱" :prefix-icon="MessageIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="regForm.password" type="password" placeholder="密码" show-password :prefix-icon="LockIcon" size="large" />
          </el-form-item>
          <el-form-item>
            <el-input v-model="regForm.full_name" placeholder="姓名（可选）" :prefix-icon="UserIcon" size="large" />
          </el-form-item>
          <el-button type="primary" :loading="loading" @click="handleRegister" class="submit-btn" size="large">
            {{ loading ? '注册中...' : '注册' }}
          </el-button>
        </el-form>

        <p class="form-hint">首次使用可注册账号，注册后自动登录。</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { h, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTabs, ElTabPane } from 'element-plus/es/components/tabs/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/form-item/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/tab-pane/style/css'
import 'element-plus/es/components/tabs/style/css'
import api from '../api'
import { setAccessToken, setRefreshToken } from '../api/http'
import { ElMessage } from 'element-plus/es/components/message/index'
import { useAuthStore } from '../stores/auth'

const authStore = useAuthStore()
const router = useRouter()
const tab = ref('login')
const loading = ref(false)

const loginForm = ref({ username: '', password: '' })
const regForm = ref({ username: '', email: '', password: '', full_name: '' })

const UserIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('path', { d: 'M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2' }),
  h('circle', { cx: 12, cy: 7, r: 4 }),
])
const LockIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('rect', { x: 3, y: 11, width: 18, height: 11, rx: 2 }),
  h('path', { d: 'M7 11V7a5 5 0 0110 0v4' }),
])
const MessageIcon = h('svg', { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': 2, width: 18, height: 18 }, [
  h('path', { d: 'M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z' }),
])

const handleLogin = async () => {
  if (!loginForm.value.username || !loginForm.value.password) {
    return ElMessage.warning('请输入用户名和密码')
  }
  loading.value = true
  try {
    const { data } = await api.login(loginForm.value)
    setAccessToken(data.access_token)
    setRefreshToken(data.refresh_token || null)
    try {
      const me = await api.getMe()
      localStorage.setItem('user_role', me.data.role || 'user')
      authStore.setUser(me.data)
    } catch {
      setAccessToken(null)
      setRefreshToken(null)
      ElMessage.error('登录失败，无法获取用户信息')
      loading.value = false
      return
    }
    ElMessage.success('登录成功')
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '登录失败')
  }
  loading.value = false
}

const handleRegister = async () => {
  if (!regForm.value.username || !regForm.value.email || !regForm.value.password) {
    return ElMessage.warning('请填写必填项')
  }
  loading.value = true
  try {
    const { data } = await api.register(regForm.value)
    setAccessToken(data.access_token)
    setRefreshToken(data.refresh_token || null)
    try {
      const me = await api.getMe()
      localStorage.setItem('user_role', me.data.role || 'user')
      authStore.setUser(me.data)
    } catch {
      setAccessToken(null)
      setRefreshToken(null)
      ElMessage.error('注册失败，无法获取用户信息')
      loading.value = false
      return
    }
    ElMessage.success('注册成功')
    router.push('/')
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '注册失败')
  }
  loading.value = false
}
</script>

<style scoped>
.login-page {
  display: flex;
  min-height: 100vh;
  background: var(--color-bg);
}

.login-brand {
  flex: 1.1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: flex-start;
  position: relative;
  background: var(--color-bg);
  color: var(--color-text);
  padding: 56px 72px;
}

.brand-content {
  max-width: 520px;
}

.brand-title-row {
  display: flex;
  gap: 14px;
  align-items: center;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-xs);
  background: var(--color-primary);
  color: #ffffff;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 600;
  font-size: 16px;
  flex-shrink: 0;
}

.brand-title {
  font-size: 24px;
  font-weight: 600;
  margin: 0 0 4px;
  color: var(--color-text);
}

.brand-subtitle {
  font-size: var(--text-base);
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.5;
}

.brand-capabilities {
  list-style: none;
  margin: 40px 0 0;
  padding: 0;
  border-top: 1px solid var(--color-border);
}

.brand-capabilities li {
  display: grid;
  gap: 3px;
  padding: 16px 0;
  border-bottom: 1px solid var(--color-border);
}

.brand-capabilities strong {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text);
}

.brand-capabilities span {
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--color-text-muted);
}

.brand-footer {
  position: absolute;
  left: 72px;
  right: 72px;
  bottom: 32px;
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.6;
  color: var(--color-text-muted);
}

.login-form-panel {
  width: 480px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  padding: 48px;
}

.form-container {
  width: 100%;
  max-width: 360px;
}

.form-header {
  margin-bottom: 24px;
}

.form-header h2 {
  margin: 0 0 6px;
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text);
}

.form-header p {
  margin: 0 0 18px;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

.login-tabs {
  --el-tabs-header-height: 40px;
}

.login-tabs :deep(.el-tabs__item) {
  font-size: var(--text-base);
  font-weight: 400;
  height: 40px;
  line-height: 40px;
  color: var(--color-text-muted);
}

.login-tabs :deep(.el-tabs__item.is-active) {
  color: var(--color-primary);
}

.login-tabs :deep(.el-tabs__nav-wrap::after) {
  display: none;
}

.login-form {
  margin-top: 8px;
}

.login-form :deep(.el-form-item) {
  margin-bottom: 18px;
}

.login-form :deep(.el-input__wrapper) {
  padding: 4px 14px;
  height: 44px;
}

.login-form :deep(.el-input__prefix) {
  margin-right: 10px;
  color: var(--color-text-muted);
}

.submit-btn {
  width: 100%;
  margin-top: 8px;
  font-weight: 500;
  height: 44px !important;
}

.form-hint {
  margin-top: 24px;
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

@media (max-width: 900px) {
  .login-page {
    flex-direction: column;
  }
  .login-brand {
    position: static;
    padding: 48px 24px 32px;
  }
  .login-form-panel {
    width: 100%;
    border-left: 0;
    border-top: 1px solid var(--color-border);
    padding: 40px 24px;
  }
  .brand-capabilities {
    margin-top: 28px;
  }
  .brand-footer {
    position: static;
    margin-top: 24px;
  }
}
</style>
