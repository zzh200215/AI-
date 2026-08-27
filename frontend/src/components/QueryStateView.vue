<template>
  <div class="query-state-view" :style="{ minHeight }">
    <!-- loading：骨架占位，避免 loading 导致页面跳动 -->
    <div v-if="status === 'loading'" class="qs-loading" role="status" aria-label="加载中">
      <div class="qs-skeleton-line w-70"></div>
      <div class="qs-skeleton-line w-90"></div>
      <div class="qs-skeleton-line w-55"></div>
    </div>

    <!-- error：明确原因 + 重试路径 -->
    <div v-else-if="status === 'error'" class="qs-error" role="alert">
      <div class="qs-error-icon">!</div>
      <div class="qs-error-body">
        <strong>{{ error?.message || '加载失败' }}</strong>
        <span v-if="error?.detail" class="qs-error-detail">{{ error.detail }}</span>
        <span v-if="error?.requestId" class="qs-error-meta">request_id: {{ error.requestId }}</span>
      </div>
      <el-button v-if="retryable" size="small" @click="$emit('retry')">重试</el-button>
    </div>

    <!-- offline：展示缓存提示 + 恢复指引 -->
    <div v-else-if="status === 'offline'" class="qs-offline" role="status">
      <span class="qs-offline-dot"></span>
      <span>网络不可用，正在展示缓存内容。恢复网络后自动刷新。</span>
    </div>

    <!-- empty：空数据 + 可执行动作 -->
    <div v-else-if="status === 'empty'" class="qs-empty">
      <el-empty :description="emptyText || '暂无数据'" :image-size="56">
        <slot name="empty-action"></slot>
      </el-empty>
    </div>

    <!-- 数据 + 可选 stale 标记 -->
    <div v-else class="qs-content">
      <slot></slot>
      <div v-if="status === 'stale'" class="qs-stale-tip">数据可能已过期，正在刷新…</div>
    </div>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/empty/style/css'

defineProps({
  status: {
    type: String,
    default: 'loading', // loading | error | empty | offline | stale | success
  },
  error: {
    type: Object,
    default: null,
  },
  emptyText: {
    type: String,
    default: '',
  },
  retryable: {
    type: Boolean,
    default: true,
  },
  minHeight: {
    type: String,
    default: '120px',
  },
})
defineEmits(['retry'])
</script>

<style scoped>
.query-state-view {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  width: 100%;
  min-width: 0;
}
.qs-loading {
  display: grid;
  gap: var(--space-3);
  padding: var(--space-2) 0;
}
.qs-skeleton-line {
  height: 14px;
  border-radius: var(--radius-sm);
  background: linear-gradient(90deg, var(--color-bg-alt) 25%, var(--color-border-light) 50%, var(--color-bg-alt) 75%);
  background-size: 200% 100%;
  animation: qs-shimmer 1.2s ease-in-out infinite;
}
.w-70 { width: 70%; }
.w-90 { width: 90%; }
.w-55 { width: 55%; }
@keyframes qs-shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}
.qs-error {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--el-color-danger-light-7);
  background: var(--el-color-danger-light-9);
}
.qs-error-icon {
  flex: 0 0 auto;
  width: 24px;
  height: 24px;
  border-radius: var(--radius-full);
  background: var(--el-color-danger);
  color: #fff;
  font-weight: 600;
  display: grid;
  place-items: center;
  font-size: var(--text-sm);
}
.qs-error-body {
  display: grid;
  gap: 4px;
  flex: 1;
  min-width: 0;
}
.qs-error-body strong {
  color: var(--el-color-danger);
  font-size: var(--text-sm);
}
.qs-error-detail {
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  word-break: break-all;
}
.qs-error-meta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-family: monospace;
}
.qs-offline {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-warning-light);
  background: var(--color-warning-light);
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
}
.qs-offline-dot {
  width: 8px;
  height: 8px;
  border-radius: var(--radius-full);
  background: var(--color-warning);
  flex: 0 0 auto;
}
.qs-empty {
  padding: var(--space-4) 0;
}
.qs-content {
  display: contents;
}
.qs-stale-tip {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  background: var(--color-bg-alt);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
</style>
