<template>
  <div class="forbidden-state" role="alert">
    <div class="forbidden-icon">403</div>
    <div class="forbidden-copy">
      <h4>无权访问</h4>
      <p>当前账号没有访问该页面的权限{{ required ? `（需要 ${required}）` : '' }}。如认为配置有误，请联系管理员。</p>
    </div>
    <el-button type="primary" @click="$router.push('/legal-workspace')">返回工作台</el-button>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import 'element-plus/es/components/button/style/css'

defineProps({
  required: {
    type: String,
    default: '',
  },
})
</script>

<style scoped>
.forbidden-state {
  display: grid;
  justify-items: start;
  gap: var(--space-4);
  padding: var(--space-8);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}
.forbidden-icon {
  width: 56px;
  height: 56px;
  border-radius: var(--radius-full);
  background: var(--color-danger-light);
  color: var(--el-color-danger);
  font-weight: 600;
  font-size: var(--text-base);
  display: grid;
  place-items: center;
}
.forbidden-copy h4 {
  margin: 0 0 var(--space-1);
  color: var(--color-text);
  font-size: var(--text-xl);
}
.forbidden-copy p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.7;
  max-width: 560px;
}
</style>
