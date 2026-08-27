<template>
  <div class="legal-contracts">
    <div class="toolbar">
      <el-button type="primary" @click="createVisible = true">新建合同</el-button>
      <el-button :loading="loading" @click="loadContracts">刷新</el-button>
    </div>

    <el-table :data="contracts" stripe size="small" v-loading="loading">
      <el-table-column prop="contract_no" label="合同编号" width="150" />
      <el-table-column prop="title" label="合同名称" min-width="180" show-overflow-tooltip />
      <el-table-column prop="counterparty" label="相对方" min-width="140" show-overflow-tooltip />
      <el-table-column prop="status" label="状态" width="100" />
      <el-table-column label="操作" width="230">
        <template #default="{ row }">
          <el-button size="small" text type="primary" @click="openVersions(row)">版本</el-button>
          <el-button size="small" text @click="openMilestones(row)">关键节点</el-button>
          <el-button size="small" text @click="openDiff(row)">版本对比</el-button>
          <el-tooltip v-if="!signingEnabled" content="电子签署服务未配置（试点暂不开放）" placement="top">
            <span><el-button size="small" text type="info" disabled>签署</el-button></span>
          </el-tooltip>
          <el-button v-else size="small" text type="success" @click="openSigning(row)">签署</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="createVisible" title="新建合同" width="560px">
      <el-form :model="createForm" label-width="86px">
        <el-form-item label="合同名称"><el-input v-model="createForm.title" maxlength="256" /></el-form-item>
        <el-form-item label="相对方"><el-input v-model="createForm.counterparty" maxlength="256" /></el-form-item>
        <el-form-item label="合同类型"><el-input v-model="createForm.contract_type" /></el-form-item>
        <el-form-item label="合同编号"><el-input v-model="createForm.contract_no" placeholder="留空自动生成" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="createVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="createContract">创建</el-button></template>
    </el-dialog>

    <el-dialog v-model="versionsVisible" :title="`${selected?.title || ''} - 版本`" width="760px">
      <div class="version-create">
        <el-input v-model="versionText" type="textarea" :rows="4" placeholder="粘贴本次合同文本快照" />
        <el-button type="primary" :disabled="!versionText.trim()" :loading="saving" @click="createVersion">保存新版本</el-button>
      </div>
      <el-table :data="versions" size="small" style="margin-top:12px">
        <el-table-column prop="version_no" label="版本" width="80" />
        <el-table-column prop="parse_status" label="解析状态" width="130" />
        <el-table-column prop="version_note" label="说明" show-overflow-tooltip />
        <el-table-column prop="created_at" label="创建时间" width="170" />
      </el-table>
    </el-dialog>

    <el-dialog v-model="diffVisible" :title="`${selected?.title || ''} - 版本对比`" width="760px">
      <div class="diff-controls">
        <el-select v-model="diffBase" placeholder="基准版本"><el-option v-for="v in versions" :key="v.id" :label="`V${v.version_no}`" :value="v.version_no" /></el-select>
        <el-select v-model="diffTarget" placeholder="目标版本"><el-option v-for="v in versions" :key="v.id" :label="`V${v.version_no}`" :value="v.version_no" /></el-select>
        <el-button type="primary" :disabled="!diffBase || !diffTarget || diffBase === diffTarget" @click="loadDiff">对比</el-button>
      </div>
      <el-empty v-if="!diffResult" description="请选择两个版本" />
      <pre v-else class="diff-result">{{ JSON.stringify(diffResult, null, 2) }}</pre>
    </el-dialog>

    <el-dialog v-model="milestonesVisible" :title="`${selected?.title || ''} - 关键节点`" width="680px">
      <el-table :data="milestones" size="small"><el-table-column prop="milestone_type" label="类型" /><el-table-column prop="standard_date" label="日期" /><el-table-column prop="status" label="状态" /></el-table>
    </el-dialog>

    <el-dialog v-model="signingVisible" :title="`${selected?.title || ''} - 电子签署`" width="680px">
      <el-form :model="signForm" label-width="120px">
        <el-form-item label="合同版本"><el-select v-model="signForm.contract_version_id" style="width:100%"><el-option v-for="v in versions" :key="v.id" :label="`V${v.version_no}`" :value="v.id" /></el-select></el-form-item>
        <el-alert title="签署服务由组织配置的法大大沙箱提供" type="info" :closable="false" show-icon style="margin-bottom:16px" />
        <el-form-item label="签署方姓名"><el-input v-model="signForm.party_name" maxlength="128" /></el-form-item>
        <el-form-item label="已核验手机号"><el-input v-model="signForm.phone_masked" placeholder="例如 138****0000" maxlength="32" /></el-form-item>
      </el-form>
      <el-table :data="signRequests" size="small" style="margin-top:12px"><el-table-column prop="provider" label="服务商" /><el-table-column prop="provider_request_id" label="签署单号" /><el-table-column prop="status" label="状态" /></el-table>
      <template #footer><el-button @click="signingVisible = false">关闭</el-button><el-button type="primary" :loading="saving" @click="createAndSendSignRequest">创建并发送</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { ElSelect, ElOption } from 'element-plus/es/components/select/index'
import { ElAlert } from 'element-plus/es/components/alert/index'
import { ElDialog } from 'element-plus/es/components/dialog/index'
import { ElEmpty } from 'element-plus/es/components/empty/index'
import { ElForm, ElFormItem } from 'element-plus/es/components/form/index'
import { ElInput } from 'element-plus/es/components/input/index'
import { ElTable, ElTableColumn } from 'element-plus/es/components/table/index'
import { ElTooltip } from 'element-plus/es/components/tooltip/index'
import 'element-plus/es/components/select/style/css'
import 'element-plus/es/components/alert/style/css'
import 'element-plus/es/components/dialog/style/css'
import 'element-plus/es/components/empty/style/css'
import 'element-plus/es/components/form/style/css'
import 'element-plus/es/components/input/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import 'element-plus/es/components/tooltip/style/css'
import api from '../api'

const props = defineProps({ orgId: { type: Number, required: true }, caseId: { type: Number, required: false, default: null } })
const contracts = ref([]); const loading = ref(false); const saving = ref(false)
const signingEnabled = ref(false)
const createVisible = ref(false); const versionsVisible = ref(false); const diffVisible = ref(false); const milestonesVisible = ref(false); const signingVisible = ref(false)
const selected = ref(null); const versions = ref([]); const milestones = ref([]); const versionText = ref(''); const diffBase = ref(null); const diffTarget = ref(null); const diffResult = ref(null)
const createForm = ref({ title: '', counterparty: '', contract_type: '', contract_no: '' })
const signRequests = ref([]); const signForm = ref({ contract_version_id: null, party_name: '', phone_masked: '' })

const loadContracts = async () => { if (!props.orgId) return; loading.value = true; try { const { data } = await api.listLegalContracts(props.orgId, props.caseId); contracts.value = data.items || [] } catch (e) { ElMessage.error(e.response?.data?.detail || '合同加载失败') } finally { loading.value = false } }
const createContract = async () => { if (!createForm.value.title.trim()) return ElMessage.warning('请输入合同名称'); saving.value = true; try { await api.createLegalContract(props.orgId, { ...createForm.value, case_id: props.caseId || null, contract_no: createForm.value.contract_no || null }); createVisible.value = false; createForm.value = { title: '', counterparty: '', contract_type: '', contract_no: '' }; await loadContracts() } catch (e) { ElMessage.error(e.response?.data?.detail || '合同创建失败') } finally { saving.value = false } }
const loadVersions = async (contract) => { selected.value = contract; const { data } = await api.listLegalContractVersions(contract.id); versions.value = data || [] }
const openVersions = async (contract) => { try { await loadVersions(contract); versionsVisible.value = true } catch { ElMessage.error('版本加载失败') } }
const createVersion = async () => { saving.value = true; try { await api.createLegalContractVersion(selected.value.id, { source_type: 'text_snapshot', text_snapshot: versionText.value, version_note: '工作台新增版本' }); versionText.value = ''; await loadVersions(selected.value) } catch (e) { ElMessage.error(e.response?.data?.detail || '版本保存失败') } finally { saving.value = false } }
const openDiff = async (contract) => { try { await loadVersions(contract); diffBase.value = null; diffTarget.value = null; diffResult.value = null; diffVisible.value = true } catch { ElMessage.error('版本加载失败') } }
const loadDiff = async () => { try { const { data } = await api.diffLegalContractVersions(selected.value.id, diffBase.value, diffTarget.value); diffResult.value = data } catch (e) { ElMessage.error(e.response?.data?.detail || 'Diff 生成失败') } }
const openMilestones = async (contract) => { selected.value = contract; try { const { data } = await api.listContractMilestones(contract.id); milestones.value = data || []; milestonesVisible.value = true } catch { ElMessage.error('关键节点加载失败') } }
const openSigning = async (contract) => { try { await loadVersions(contract); const { data } = await api.listSignRequests(contract.id); signRequests.value = data || []; signForm.value = { contract_version_id: versions.value[0]?.id || null, party_name: '', phone_masked: '' }; signingVisible.value = true } catch { ElMessage.error('签署记录加载失败') } }
const createAndSendSignRequest = async () => { if (!signForm.value.contract_version_id || !signForm.value.party_name.trim() || !signForm.value.phone_masked.trim()) return ElMessage.warning('请填写版本、签署方和已核验手机号'); saving.value = true; try { const { data } = await api.createSignRequest(selected.value.id, { contract_version_id: signForm.value.contract_version_id, parties: [{ name: signForm.value.party_name.trim(), phone_masked: signForm.value.phone_masked.trim(), sign_order: 1 }] }); await api.sendSignRequest(data.id); const result = await api.listSignRequests(selected.value.id); signRequests.value = result.data || []; ElMessage.success('签署请求已提交给服务商') } catch (e) { ElMessage.error(e.response?.data?.detail || '签署请求创建失败') } finally { saving.value = false } }
const loadFeatures = async () => { try { const { data } = await api.getFeatureFlags(); signingEnabled.value = !!data.signing_enabled } catch { /* 特性开关不可用时保持默认关闭 */ } }
watch(() => [props.orgId, props.caseId], loadContracts, { immediate: true })
onMounted(loadFeatures)
</script>

<style scoped>
.toolbar,.diff-controls,.version-create { display:flex; gap:8px; align-items:center; margin-bottom:12px; }
.version-create { align-items:flex-start; }.version-create .el-textarea { flex:1; }
.diff-result { max-height:360px; overflow:auto; padding:12px; background:var(--color-surface-subtle); white-space:pre-wrap; }
</style>
