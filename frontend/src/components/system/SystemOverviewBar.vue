<template>
  <div class="system-overview">
    <div class="overview-tile">
      <span>运行任务</span>
      <strong>{{ runningTaskCount }}</strong>
    </div>
    <div class="overview-tile">
      <span>失败任务</span>
      <strong>{{ failedTaskCount }}</strong>
    </div>
    <div class="overview-tile">
      <span>待重试</span>
      <strong>{{ retryableTaskCount }}</strong>
    </div>
    <div class="overview-tile">
      <span>Agent 成功率</span>
      <strong>{{ Math.round(agentSuccessRate * 100) }}%</strong>
    </div>
  </div>

  <div class="system-command-bar">
    <div class="command-chips">
      <span class="command-chip">当前标签：{{ activeTab }}</span>
      <span class="command-chip">失败任务：{{ failedTaskCount }}</span>
      <span class="command-chip">待审批：{{ approvalStats.pending || 0 }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import api from '../../api'
import { useSystemTaskMonitor } from '../../composables/useSystemTaskMonitor'
import { useSystemApprovals } from '../../composables/useSystemApprovals'
import { useAuthStore } from '../../stores/auth'

defineProps({
  activeTab: { type: String, default: 'health' },
})

const router = useRouter()
const authStore = useAuthStore()
const isAdmin = computed(() => authStore.isAdmin)

const { runningTaskCount, failedTaskCount, retryableTaskCount, agentSuccessRate } = useSystemTaskMonitor({ client: api, message: ElMessage, router, isAdmin })
const { approvalStats } = useSystemApprovals({ client: api, message: ElMessage })
</script>

<style scoped>
.system-overview {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-4);
}

.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.overview-tile {
  padding: var(--space-5) var(--space-5);
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border-light);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
  display: grid;
  gap: var(--space-1);
  transition: all var(--transition-fast);
}
.overview-tile:hover {
  box-shadow: var(--shadow-card-hover);
  border-color: var(--color-border-hover);
}
.overview-tile span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.overview-tile strong {
  font-size: var(--text-3xl);
  line-height: var(--text-3xl-lh);
  color: var(--color-text);
  font-weight: 600;
}

.system-command-bar {
  display: flex;
  justify-content: flex-start;
  gap: var(--space-4);
  align-items: center;
  flex-wrap: wrap;
  padding: var(--space-4) var(--space-5);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}

.command-chips {
  display: flex;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.command-chip {
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-full);
  background: var(--color-primary-light);
  border: 1px solid var(--color-border);
  color: var(--color-primary);
  font-size: var(--text-xs);
  font-weight: 600;
}

@media (max-width: 1100px) {
  .system-overview {
    grid-template-columns: 1fr 1fr;
  }
  .system-command-bar {
    align-items: flex-start;
  }
}
</style>
