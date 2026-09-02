<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Activity, BrainCircuit, CheckCircle2, Database, KeyRound, Link2,
  RefreshCw, Save, Server, ShieldCheck, Trash2, Unplug,
} from 'lucide-vue-next'
import { aiDataSourceApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { AiDataSourceStatus } from '@/types'
import { formatDateTime } from '@/utils/format'

const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const status = ref<AiDataSourceStatus | null>(null)
const availableModels = ref<string[]>([])
const form = reactive({
  name: 'OpenAI',
  protocol: 'openai_responses' as const,
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-5.6-terra',
  api_key: '',
})

const ready = computed(() => status.value?.verification_status === 'valid')

function applyStatus(value: AiDataSourceStatus) {
  status.value = value
  if (value.configured) {
    form.name = value.name || 'OpenAI'
    form.base_url = value.base_url || 'https://api.openai.com/v1'
    form.model = value.model || ''
  }
}

async function loadStatus() {
  loading.value = true
  try { applyStatus(await aiDataSourceApi.status()) }
  catch (error) { ElMessage.error(getErrorMessage(error, '读取 AI 数据源失败')) }
  finally { loading.value = false }
}

async function saveSource() {
  if (!form.name.trim() || !form.base_url.trim() || !form.model.trim()) return ElMessage.warning('请填写名称、Base URL 和模型')
  if (!status.value?.configured && !form.api_key.trim()) return ElMessage.warning('首次配置必须填写 API Key')
  saving.value = true
  try {
    const value = await aiDataSourceApi.save({
      name: form.name.trim(),
      protocol: 'openai_responses',
      base_url: form.base_url.trim(),
      model: form.model.trim(),
      api_key: form.api_key.trim() || null,
    })
    form.api_key = ''
    availableModels.value = []
    applyStatus(value)
    ElMessage.success('AI 数据源已加密保存，所有 AI 任务将统一使用此账号')
  } catch (error) { ElMessage.error(getErrorMessage(error, '保存 AI 数据源失败')) }
  finally { saving.value = false }
}

async function testSource() {
  testing.value = true
  try {
    const result = await aiDataSourceApi.test()
    availableModels.value = result.models
    result.valid ? ElMessage.success(result.message) : ElMessage.warning(result.message)
    await loadStatus()
  } catch (error) { ElMessage.error(getErrorMessage(error, '测试 AI 数据源失败')) }
  finally { testing.value = false }
}

async function loadModels() {
  testing.value = true
  try {
    availableModels.value = (await aiDataSourceApi.models()).models
    ElMessage.success(`已读取 ${availableModels.value.length} 个模型`)
  } catch (error) { ElMessage.error(getErrorMessage(error, '读取模型列表失败')) }
  finally { testing.value = false }
}

async function removeSource() {
  try { await ElMessageBox.confirm('删除后 AI 创作会自动停用，已有任务和草稿仍会保留。', '删除 AI 数据源', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }) }
  catch { return }
  saving.value = true
  try { await aiDataSourceApi.remove(); availableModels.value = []; await loadStatus(); ElMessage.success('AI 数据源已删除') }
  catch (error) { ElMessage.error(getErrorMessage(error, '删除 AI 数据源失败')) }
  finally { saving.value = false }
}

onMounted(loadStatus)
</script>

<template>
  <div class="ai-source-page" v-loading="loading">
    <section class="source-hero">
      <span class="hero-icon"><BrainCircuit :size="27" /></span>
      <div><span class="eyebrow">UNIFIED AI ACCOUNT</span><h2>统一 AI 数据源</h2><p>一个 OpenAI 兼容账号，服务自动创作、手动生成和全部 AI 能力。</p></div>
      <div class="hero-status" :class="{ ready }"><small>ACCOUNT STATUS</small><strong>{{ ready ? '连接可用' : status?.configured ? '等待测试' : '尚未配置' }}</strong><span><i /> SINGLE ACCOUNT ROUTING</span></div>
    </section>

    <section class="architecture-strip">
      <span><KeyRound :size="17" /><b>OpenAI API Key</b><small>统一凭据</small></span><i>→</i>
      <span><Server :size="17" /><b>AI Worker</b><small>动态读取</small></span><i>→</i>
      <span><BrainCircuit :size="17" /><b>全部 AI 能力</b><small>同一账号</small></span>
    </section>

    <div class="source-grid">
      <section class="panel">
        <header><span><Link2 :size="19" /></span><div><h3>账号与服务地址</h3><p>兼容 OpenAI Responses API 的官方或自建服务</p></div></header>
        <el-form label-position="top">
          <el-form-item label="配置名称"><el-input v-model="form.name" maxlength="100" placeholder="例如：OpenAI 主账号" /></el-form-item>
          <el-form-item label="API 协议"><el-select v-model="form.protocol" disabled><el-option label="OpenAI Responses API" value="openai_responses" /></el-select></el-form-item>
          <el-form-item label="Base URL"><el-input v-model="form.base_url" placeholder="https://api.openai.com/v1"><template #prefix><Link2 :size="15" /></template></el-input><small class="field-help">填写到 /v1，系统调用时自动追加 /responses 或 /models。</small></el-form-item>
          <el-form-item label="模型"><el-select v-model="form.model" filterable allow-create default-first-option placeholder="输入或选择模型"><el-option v-for="model in availableModels" :key="model" :label="model" :value="model" /></el-select></el-form-item>
        </el-form>
      </section>

      <section class="panel credential-panel">
        <header><span><ShieldCheck :size="19" /></span><div><h3>API Key</h3><p>只保存密文，不会返回明文或进入任务快照</p></div></header>
        <div class="status-grid">
          <span><Database :size="16" /><small>MySQL 密文</small><b>{{ status?.configured ? status.key_hint : '未保存' }}</b></span>
          <span><Unplug :size="16" /><small>Redis 密文缓存</small><b>{{ status?.cache_active ? `${status.cache_ttl_seconds}s` : '未缓存' }}</b></span>
          <span><Activity :size="16" /><small>连接状态</small><b>{{ ready ? '已验证' : status?.verification_status === 'invalid' ? 'Key 无效' : '待验证' }}</b></span>
        </div>
        <el-input v-model="form.api_key" type="password" show-password clearable size="large" autocomplete="new-password" :placeholder="status?.configured ? `留空则保留 ${status.key_hint}` : 'sk-...'" />
        <p class="security-note"><ShieldCheck :size="15" />API Key 使用服务端密钥加密后写入 MySQL，Redis 只缓存密文。</p>
        <el-button type="primary" size="large" :loading="saving" @click="saveSource"><Save v-if="!saving" :size="16" />{{ status?.configured ? '保存并更新统一账号' : '创建统一账号' }}</el-button>
        <div v-if="status?.configured" class="actions"><el-button :loading="testing" @click="testSource"><CheckCircle2 :size="15" />连通测试</el-button><el-button :loading="testing" @click="loadModels"><RefreshCw :size="15" />读取模型</el-button><el-button type="danger" plain @click="removeSource"><Trash2 :size="15" />删除</el-button></div>
        <p v-if="status?.last_error" class="error-copy">{{ status.last_error }}</p>
        <footer v-if="status?.configured">版本 v{{ status.version }} · {{ status.updated_at ? formatDateTime(status.updated_at) : '—' }}</footer>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ai-source-page{display:flex;flex-direction:column;gap:16px;max-width:1180px}.source-hero,.panel,.architecture-strip{border:1px solid var(--border-color);border-radius:14px;background:var(--surface-primary)}.source-hero{display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:center;padding:22px;background:linear-gradient(120deg,rgba(91,72,213,.2),rgba(13,18,28,.97) 48%)}.hero-icon,.panel header>span{display:grid;place-items:center;color:#aa9cff;background:rgba(112,91,230,.16);border:1px solid rgba(133,113,245,.25)}.hero-icon{width:52px;height:52px;border-radius:13px}.eyebrow{font-size:10px;letter-spacing:.16em;color:#9d8fff}.source-hero h2{margin:4px 0;font-size:23px}.source-hero p,.panel header p,.field-help,.security-note{margin:0;color:var(--text-secondary);font-size:12px;line-height:1.6}.hero-status{min-width:180px;padding:11px 14px;border:1px solid rgba(235,173,74,.24);border-radius:10px;background:rgba(8,12,19,.5)}.hero-status small,.hero-status strong{display:block}.hero-status strong{margin:3px 0 6px}.hero-status span{display:flex;align-items:center;gap:6px;color:#e6ae58;font-size:9px}.hero-status i{width:6px;height:6px;border-radius:50%;background:#e6ae58}.hero-status.ready{border-color:rgba(79,227,167,.25)}.hero-status.ready span{color:#59dba7}.hero-status.ready i{background:#59dba7}.architecture-strip{display:flex;align-items:center;justify-content:center;gap:24px;padding:13px}.architecture-strip>span{display:grid;grid-template-columns:auto auto;gap:1px 7px;align-items:center}.architecture-strip svg{grid-row:1/3;color:#9d8fff}.architecture-strip b{font-size:12px}.architecture-strip small{color:var(--text-secondary);font-size:10px}.architecture-strip>i{color:#6259a2}.source-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.panel{padding:20px}.panel header{display:flex;align-items:center;gap:10px;margin-bottom:18px}.panel header>span{width:35px;height:35px;border-radius:9px}.panel h3{margin:0 0 2px;font-size:15px}.panel :deep(.el-select){width:100%}.field-help{display:block;margin-top:5px}.credential-panel{display:flex;flex-direction:column;gap:13px}.status-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.status-grid>span{display:grid;grid-template-columns:auto 1fr;gap:2px 6px;padding:9px;border:1px solid var(--border-color);border-radius:9px;background:var(--surface-secondary)}.status-grid svg{grid-row:1/3;color:#9283ed}.status-grid small{color:var(--text-secondary);font-size:9px}.status-grid b{overflow:hidden;text-overflow:ellipsis;font-size:11px}.security-note{display:flex;align-items:center;gap:6px}.credential-panel>.el-button{width:100%}.actions{display:flex;gap:7px;padding-top:10px;border-top:1px solid var(--border-color)}.actions .el-button{margin:0}.error-copy{margin:0;padding:9px;border-radius:7px;background:rgba(230,76,76,.09);color:#ff8989;font-size:11px}.credential-panel footer{color:var(--text-secondary);font-size:10px;text-align:right}@media(max-width:900px){.source-hero{grid-template-columns:auto 1fr}.hero-status{grid-column:1/-1}.source-grid{grid-template-columns:1fr}}@media(max-width:600px){.architecture-strip{align-items:flex-start;flex-direction:column}.architecture-strip>i{display:none}.status-grid{grid-template-columns:1fr}}
</style>
