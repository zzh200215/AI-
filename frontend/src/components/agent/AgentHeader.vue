<template>
  <div>
    <div class="page-header">
      <div class="header-tips">
        <span>推荐示例：</span>
        <el-button text @click="applyExample('总结文档 1，并提取其中的风险点')">文档风险</el-button>
        <el-button text @click="applyExample('审查这份合同并提示风险条款')">合同审查</el-button>
        <el-button text @click="applyExample('生成一份劳动争议仲裁申请书草稿')">文书草稿</el-button>
      </div>
    </div>

    <div class="overview-strip">
      <div class="overview-tile">
        <span>待审批动作</span>
        <strong>{{ approvals.length }}</strong>
        <p>高风险工具调用会在这里进入人工确认。</p>
      </div>
      <div class="overview-tile">
        <span>运行历史</span>
        <strong>{{ historyTotal }}</strong>
        <p>可回看执行目标、最终结果和完整步骤链路。</p>
      </div>
      <div class="overview-tile">
        <span>当前模式</span>
        <strong>{{ demoPreset ? '演示链路' : '自由执行' }}</strong>
        <p>{{ demoPreset ? demoPreset.title : '按一句话目标自动规划并执行。' }}</p>
      </div>
    </div>

    <section class="expert-directory" aria-label="专家角色目录">
      <div class="directory-heading">
        <div>
          <strong>企业专家协作网络</strong>
        </div>
        <span>总管负责编排，专家负责结论，执行层只处理已确认动作。</span>
      </div>
      <div class="expert-role-grid">
        <article v-for="role in expertRoles" :key="role.agent_type" class="expert-role">
          <div class="expert-role-topline">
            <strong>{{ role.label }}</strong>
            <el-tag size="small" :type="role.execution_mode === 'controlled_side_effect' ? 'warning' : role.execution_mode === 'orchestration_only' ? 'primary' : 'success'">
              {{ executionModeLabel(role.execution_mode) }}
            </el-tag>
          </div>
          <p>{{ role.description }}</p>
          <div class="expert-role-contract">
            <span>交付</span>
            <strong>{{ role.output_contract }}</strong>
          </div>
          <div class="expert-role-tools">
            <span v-for="tool in role.allowed_tools || []" :key="`${role.agent_type}-${tool}`">{{ toolLabel(tool) }}</span>
          </div>
        </article>
      </div>
    </section>

    <div class="agent-command-bar">
      <div class="command-copy">
        <strong>低风险步骤自动执行，敏感动作按需确认</strong>
        <span>适合处理文档风险、合同审查、法律咨询和跨模块串联动作。</span>
      </div>
      <div class="command-chips">
        <span class="command-chip">最大步数 {{ maxSteps }}</span>
        <span class="command-chip">审批 {{ approvals.length }}</span>
        <span class="command-chip">历史 {{ historyTotal }}</span>
        <span class="command-chip">成功率 {{ agentMetrics.success_rate == null ? '-' : `${Math.round(agentMetrics.success_rate * 100)}%` }}</span>
        <span class="command-chip">工具成功 {{ agentMetrics.reliability?.tool_success_rate == null ? '-' : `${Math.round(agentMetrics.reliability.tool_success_rate * 100)}%` }}</span>
        <span class="command-chip">重试任务 {{ agentMetrics.reliability?.retrying_run_rate == null ? '-' : `${Math.round(agentMetrics.reliability.retrying_run_rate * 100)}%` }}</span>
        <span class="command-chip">人工介入 {{ agentMetrics.reliability?.human_intervention_rate == null ? '-' : `${Math.round(agentMetrics.reliability.human_intervention_rate * 100)}%` }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ElButton } from 'element-plus/es/components/button/index'
import { ElTag } from 'element-plus/es/components/tag/index'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/tag/style/css'
import { executionModeLabel, toolLabel } from '../../utils/workspacePresentation'
import { useAgentWorkbench } from '../../composables/useAgentWorkbench'

const {
  applyExample, approvals, historyTotal, demoPreset, maxSteps, agentMetrics, expertRoles,
} = useAgentWorkbench()
</script>

<style scoped>
.section-eyebrow {
  margin-bottom: 4px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}
.page-header {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-3) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}
.page-header h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-3xl);
  color: var(--color-text);
  font-weight: 600;
}
.page-header p,
.header-tips {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
  font-size: var(--text-sm);
}
.header-tips {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.overview-strip {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-4);
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
  color: var(--color-text);
  font-size: var(--text-2xl);
  line-height: var(--text-2xl-lh);
  font-weight: 600;
}
.overview-tile p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.expert-directory {
  padding: var(--space-5) var(--space-6) var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}
.directory-heading {
  display: flex;
  justify-content: space-between;
  gap: var(--space-5);
  align-items: end;
  margin-bottom: var(--space-4);
}
.directory-heading strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.directory-heading > span {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
  line-height: 1.6;
}
.expert-role-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}
.expert-role {
  display: grid;
  align-content: start;
  gap: var(--space-3);
  min-height: 206px;
  padding: var(--space-4);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-md);
  background: #fff;
}
.expert-role-topline {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  align-items: center;
}
.expert-role-topline strong {
  color: var(--color-text);
  font-size: var(--text-sm);
}
.expert-role p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: var(--text-xs);
  line-height: 1.65;
}
.expert-role-contract {
  display: grid;
  gap: 2px;
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border-light);
}
.expert-role-contract span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.expert-role-contract strong {
  color: var(--color-text);
  font-size: var(--text-xs);
  font-weight: 600;
  line-height: 1.55;
}
.expert-role-tools {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
}
.expert-role-tools span {
  padding: 3px 6px;
  border-radius: 4px;
  background: var(--color-primary-light);
  color: var(--color-primary);
  font-size: 11px;
  line-height: 1.3;
}
.agent-command-bar {
  display: flex;
  justify-content: space-between;
  gap: var(--space-5);
  align-items: center;
  flex-wrap: wrap;
  padding: var(--space-5) var(--space-6);
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-xl);
  background: var(--color-surface);
  box-shadow: var(--shadow-xs);
}
.command-copy {
  display: grid;
  gap: 4px;
}
.command-copy strong {
  color: var(--color-text);
  font-size: var(--text-lg);
}
.command-copy span {
  color: var(--color-text-secondary);
  font-size: var(--text-sm);
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
@media (max-width: 1024px) {
  .page-header,
  .overview-strip,
  .agent-command-bar {
    grid-template-columns: 1fr;
    display: grid;
  }
  .expert-role-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .directory-heading {
    align-items: flex-start;
    flex-direction: column;
  }
}
@media (max-width: 640px) {
  .expert-directory {
    padding: var(--space-4);
  }
  .expert-role-grid {
    grid-template-columns: 1fr;
  }
  .expert-role {
    min-height: 0;
  }
}
</style>
