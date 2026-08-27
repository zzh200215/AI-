<template>
  <div class="pricing-page">
    <div v-if="myPlan" class="pricing-header">
      <el-tag size="small" type="success" effect="plain">当前方案：{{ myPlan.name }}（{{ myPlan.status }}）</el-tag>
    </div>

    <div class="plan-grid" v-if="plans.length">
      <div v-for="plan in plans" :key="plan.id" class="plan-card" :class="{ current: isCurrent(plan) }">
        <div class="plan-card-head">
          <span class="plan-name">{{ plan.name }}</span>
          <el-tag v-if="isCurrent(plan)" size="small" type="success">当前</el-tag>
        </div>
        <div class="plan-price">
          <span class="price-num">¥{{ plan.price_monthly }}</span>
          <span class="price-unit">/月</span>
        </div>
        <p class="plan-desc">{{ plan.description }}</p>
        <ul class="plan-quotas">
          <li>咨询 {{ plan.quota_consultation }} 次/月</li>
          <li>合同审查 {{ plan.quota_review }} 次/月</li>
          <li>文书草稿 {{ plan.quota_draft }} 次/月</li>
        </ul>
        <div class="plan-actions">
          <el-button v-if="isCurrent(plan)" size="small" disabled>当前方案</el-button>
          <el-button v-else size="small" type="primary" :loading="buyingTier === plan.tier" @click="buy(plan)">
            {{ plan.price_monthly > 0 ? '升级' : '使用免费版' }}
          </el-button>
        </div>
      </div>
    </div>
    <el-empty v-else description="暂无方案" />

    <el-alert
      v-if="checkoutMessage"
      :title="checkoutMessage"
      type="warning"
      show-icon
      :closable="false"
      style="margin-top: 20px"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { ElTag } from 'element-plus/es/components/tag/index'
import { ElButton } from 'element-plus/es/components/button/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElMessage } from 'element-plus/es/components/message/index'
import 'element-plus/es/components/tag/style/css'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/message/style/css'
import subscriptionApi from '../api/subscription'

const plans = ref([])
const myPlan = ref(null)
const buyingTier = ref('')
const checkoutMessage = ref('')

const isCurrent = (plan) => myPlan.value?.tier === plan.tier

const load = async () => {
  try {
    const { data } = await subscriptionApi.listPlans()
    plans.value = data || []
  } catch { plans.value = [] }
  try {
    const { data: me } = await subscriptionApi.mySubscription()
    myPlan.value = me?.plan || null
  } catch { myPlan.value = null }
}

const buy = async (plan) => {
  buyingTier.value = plan.tier
  checkoutMessage.value = ''
  try {
    const { data: res } = await subscriptionApi.checkout(plan.tier)
    if (res?.configured && res.checkout_url) {
      window.location.href = res.checkout_url
    } else {
      checkoutMessage.value = res?.message || '支付网关尚未配置，请联系管理员开通后购买'
    }
  } catch {
    ElMessage.error('操作失败，请稍后重试')
  } finally {
    buyingTier.value = ''
  }
}

onMounted(load)
</script>

<style scoped>
.pricing-page {
  display: grid;
  gap: var(--space-4);
}
.pricing-header {
  margin: 0;
}
.plan-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  gap: 16px;
}
.plan-card {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-5);
  display: flex;
  flex-direction: column;
}
.plan-card.current {
  border-color: var(--color-success);
  box-shadow: 0 0 0 1px var(--color-success);
}
.plan-card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.plan-name {
  font-weight: 600;
  font-size: 15px;
}
.plan-price {
  margin: 12px 0 4px;
}
.price-num {
  font-size: 26px;
  font-weight: 700;
}
.price-unit {
  color: var(--color-text-muted);
  margin-left: 2px;
}
.plan-desc {
  color: var(--color-text-secondary);
  font-size: 12px;
  min-height: 32px;
}
.plan-quotas {
  padding-left: 18px;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.9;
  flex: 1;
}
.plan-actions {
  margin-top: 12px;
}
</style>
