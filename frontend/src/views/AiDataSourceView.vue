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
  <div class="ai-data-source-page ai-source-page" v-loading="loading">
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
        <el-input v-model="form.api_key" type="password" show-password clearable size="large" autocomplete="new-password" :placeholder="status?.configured ? `•••••••• ${status.key_hint}（留空保留）` : 'sk-...'" />
        <p class="security-note"><ShieldCheck :size="15" />API Key 使用服务端密钥加密后写入 MySQL，Redis 只缓存密文。</p>
        <el-button type="primary" size="large" :loading="saving" @click="saveSource"><Save v-if="!saving" :size="16" />{{ status?.configured ? '保存并更新统一账号' : '创建统一账号' }}</el-button>
        <div v-if="status?.configured" class="actions"><el-button :loading="testing" @click="testSource"><CheckCircle2 :size="15" />连通测试</el-button><el-button :loading="testing" @click="loadModels"><RefreshCw :size="15" />读取模型</el-button><el-button type="danger" plain @click="removeSource"><Trash2 :size="15" />删除</el-button></div>
        <p v-if="status?.last_error" class="error-copy">{{ status.last_error }}</p>
        <footer v-if="status?.configured">版本 v{{ status.version }} · {{ status.updated_at ? formatDateTime(status.updated_at) : '未记录' }}</footer>
      </section>
    </div>
  </div>
</template>
