<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AlertTriangle, ArrowUpRight, CheckCircle2, CircleDollarSign, Cookie, Database,
  KeyRound, RadioTower, RefreshCw, ShieldCheck, Trash2, Unplug,
} from 'lucide-vue-next'
import { xCredentialsApi, xSourcesApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { XCredentialAcquisitionMethod, XSourceProvider, XSourceStatus } from '@/types'
import { formatDateTime } from '@/utils/format'

const loading = ref(false)
const actionLoading = ref(false)
const selectedProvider = ref<XSourceProvider>('official_api')
const sourceStatus = ref<XSourceStatus | null>(null)
const acquisitionMethod = ref<XCredentialAcquisitionMethod>('developer_console')
const bearerToken = ref('')
const officialAcknowledged = ref(false)
const accountLabel = ref('x-reader')
const authToken = ref('')
const ct0 = ref('')
const twscrapeAcknowledged = ref(false)

const official = computed(() => sourceStatus.value?.official_api)
const twscrape = computed(() => sourceStatus.value?.twscrape)
const isActive = computed(() => sourceStatus.value?.active_provider === selectedProvider.value)
const selectedConfigured = computed(() => selectedProvider.value === 'official_api' ? !!official.value?.configured : !!twscrape.value?.configured)
const providerCards = [
  { id: 'official_api' as const, title: '官方 X API', badge: '生产推荐', description: 'App-only Bearer Token，稳定、合规，按返回资源消耗 X API Credits。', icon: ShieldCheck },
  { id: 'twscrape' as const, title: 'twscrape', badge: '实验模式', description: '使用登录 Cookie 读取网页 GraphQL，无官方 API 费用，但有失效与账号限制风险。', icon: Cookie },
]

function verificationText(value?: string | null) {
  return ({ valid: '校验通过', invalid: '凭据无效', error: '连接异常', unverified: '等待校验' } as Record<string, string>)[value || 'unverified'] || '尚未配置'
}

async function loadStatus() {
  loading.value = true
  try {
    sourceStatus.value = await xSourcesApi.status()
    selectedProvider.value = sourceStatus.value.active_provider
    if (sourceStatus.value.official_api.acquisition_method) acquisitionMethod.value = sourceStatus.value.official_api.acquisition_method
  } catch (error) { ElMessage.error(getErrorMessage(error, '读取 X 数据源状态失败')) }
  finally { loading.value = false }
}

async function activateProvider() {
  if (!selectedConfigured.value) return ElMessage.warning('请先保存并测试该数据源的凭据')
  actionLoading.value = true
  try {
    sourceStatus.value = await xSourcesApi.selectProvider(selectedProvider.value)
    ElMessage.success(`已切换到${selectedProvider.value === 'official_api' ? '官方 X API' : ' twscrape'}，轮询任务已重新排队`)
  } catch (error) { ElMessage.error(getErrorMessage(error, '切换数据源失败')) }
  finally { actionLoading.value = false }
}

async function saveOfficial() {
  if (!bearerToken.value.trim() || !officialAcknowledged.value) return ElMessage.warning('请填写 Token 并完成确认')
  actionLoading.value = true
  try {
    await xCredentialsApi.save({ bearer_token: bearerToken.value.trim(), acquisition_method: acquisitionMethod.value })
    bearerToken.value = ''; officialAcknowledged.value = false
    await loadStatus(); selectedProvider.value = 'official_api'
    ElMessage.success('Bearer Token 已加密保存')
  } catch (error) { ElMessage.error(getErrorMessage(error, '保存 Bearer Token 失败')) }
  finally { actionLoading.value = false }
}

async function testOfficial() {
  actionLoading.value = true
  try {
    const result = await xCredentialsApi.test()
    result.valid ? ElMessage.success(result.message) : ElMessage.warning(result.message)
    await loadStatus(); selectedProvider.value = 'official_api'
  } catch (error) { ElMessage.error(getErrorMessage(error, '官方 API 测试失败')) }
  finally { actionLoading.value = false }
}

async function saveTwscrape() {
  if (!accountLabel.value.trim() || !authToken.value.trim() || !ct0.value.trim()) return ElMessage.warning('请填写账号标识、auth_token 和 ct0')
  if (!twscrapeAcknowledged.value) return ElMessage.warning('请确认你已了解 twscrape 风险')
  actionLoading.value = true
  try {
    sourceStatus.value = await xSourcesApi.saveTwscrape({ account_label: accountLabel.value.trim(), auth_token: authToken.value.trim(), ct0: ct0.value.trim(), acknowledged_risk: true })
    authToken.value = ''; ct0.value = ''; twscrapeAcknowledged.value = false
    ElMessage.success('twscrape Cookies 已加密保存')
  } catch (error) { ElMessage.error(getErrorMessage(error, '保存 twscrape Cookies 失败')) }
  finally { actionLoading.value = false }
}

async function testTwscrape() {
  actionLoading.value = true
  try {
    const result = await xSourcesApi.testTwscrape()
    result.valid ? ElMessage.success(result.message) : ElMessage.warning(result.message)
    await loadStatus(); selectedProvider.value = 'twscrape'
  } catch (error) { ElMessage.error(getErrorMessage(error, 'twscrape 测试失败')) }
  finally { actionLoading.value = false }
}

async function removeTwscrape() {
  try { await ElMessageBox.confirm('删除后无法使用 twscrape。若它是当前数据源，请先切换到官方 API。', '删除 Cookies', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }) }
  catch { return }
  actionLoading.value = true
  try {
    await xSourcesApi.removeTwscrape(); await loadStatus(); selectedProvider.value = 'twscrape'
    ElMessage.success('twscrape Cookies 已删除')
  } catch (error) { ElMessage.error(getErrorMessage(error, '删除 Cookies 失败')) }
  finally { actionLoading.value = false }
}

onMounted(loadStatus)
</script>

<template>
<section class="x-authorization-page source-page" v-loading="loading">
    <header class="hero">
      <div class="hero__icon"><RadioTower :size="28" /></div>
      <div><span class="eyebrow">X DATA SOURCE CONTROL</span><h2>统一管理 X 数据源</h2><p>所有定时任务动态读取当前选择，切换后无需重启 Worker。</p></div>
      <div class="active-source"><small>当前生效</small><strong>{{ sourceStatus?.active_provider === 'twscrape' ? 'twscrape' : '官方 X API' }}</strong><span><i /> TASK ROUTING ACTIVE</span></div>
    </header>

    <div class="provider-grid">
      <button v-for="provider in providerCards" :key="provider.id" type="button" class="provider-card" :class="{ 'is-selected': selectedProvider === provider.id }" @click="selectedProvider = provider.id">
        <span class="provider-card__icon"><component :is="provider.icon" :size="22" /></span>
        <span class="provider-card__body"><strong>{{ provider.title }} <em>{{ provider.badge }}</em></strong><small>{{ provider.description }}</small></span>
        <span v-if="sourceStatus?.active_provider === provider.id" class="active-pill"><CheckCircle2 :size="14" />使用中</span>
      </button>
    </div>

    <div class="control-bar">
      <div><strong>{{ isActive ? '该数据源正在处理所有监听任务' : '配置完成后启用该数据源' }}</strong><span>{{ isActive ? 'Worker 会在每次读取前确认当前选择' : '切换后会重新排队当前活跃任务' }}</span></div>
      <button class="activate-button" type="button" :disabled="isActive || actionLoading || !selectedConfigured" @click="activateProvider"><RefreshCw :class="{ spin: actionLoading }" :size="17" />{{ isActive ? '当前已启用' : '启用此数据源' }}</button>
    </div>

    <div v-if="selectedProvider === 'official_api'" class="workspace-grid">
      <section class="panel">
        <div class="panel__head"><div><span class="step">01</span><h3>官方认证方式</h3></div><CircleDollarSign :size="20" /></div>
        <div class="method-tabs"><button type="button" :class="{ active: acquisitionMethod === 'developer_console' }" @click="acquisitionMethod = 'developer_console'">Developer Console</button><button type="button" :class="{ active: acquisitionMethod === 'api_exchange' }" @click="acquisitionMethod = 'api_exchange'">API Key / Secret</button></div>
        <div class="guide" v-if="acquisitionMethod === 'developer_console'"><strong>App-Only Authentication → Bearer Token</strong><ol><li>进入 X Developer Console</li><li>选择 App 的 Keys & Tokens</li><li>在 Bearer Token 一栏 Generate</li><li>立即复制并保存到右侧</li></ol><a href="https://console.x.com/" target="_blank" rel="noopener noreferrer">打开 Developer Console <ArrowUpRight :size="14" /></a></div>
        <div class="guide" v-else><strong>Client Credentials Grant</strong><ol><li>准备 Consumer Key 与 Secret</li><li>构造 Basic Authorization</li><li>POST /oauth2/token</li><li>复制 access_token</li></ol><a href="https://docs.x.com/fundamentals/authentication/oauth-2-0/application-only" target="_blank" rel="noopener noreferrer">查看官方流程 <ArrowUpRight :size="14" /></a></div>
      </section>

      <section class="panel credential-panel">
        <div class="panel__head"><div><span class="step">02</span><h3>Bearer Token</h3></div><KeyRound :size="20" /></div>
        <div class="status-strip"><span><Database :size="16" /><small>MySQL</small><strong>{{ official?.configured ? official.token_hint : '未保存' }}</strong></span><span><Unplug :size="16" /><small>Redis</small><strong>{{ official?.cache_active ? `${official.cache_ttl_seconds}s` : '未缓存' }}</strong></span><span><CheckCircle2 :size="16" /><small>状态</small><strong>{{ verificationText(official?.verification_status) }}</strong></span></div>
        <el-input v-model="bearerToken" type="password" show-password clearable size="large" autocomplete="new-password" :placeholder="official?.configured ? `•••••••• ${official.token_hint}（留空保留现有 Token）` : '粘贴 App-only Bearer Token'" />
        <el-checkbox v-model="officialAcknowledged">确认这是 App-only Bearer Token，保存后旧版本立即失效</el-checkbox>
        <button class="primary-button" type="button" :disabled="actionLoading || !bearerToken.trim()" @click="saveOfficial"><ShieldCheck :size="17" />{{ official?.configured ? '轮换并保存 Token' : '加密保存 Token' }}</button>
        <div v-if="official?.configured" class="manage-actions"><button type="button" :disabled="actionLoading" @click="testOfficial"><RefreshCw :size="16" />测试官方 API</button><span>版本 v{{ official.version }} · {{ official.updated_at ? formatDateTime(official.updated_at) : '未记录' }}</span></div>
        <p v-if="official?.last_error" class="last-error">{{ official.last_error }}</p>
      </section>
    </div>

    <div v-else class="workspace-grid">
      <section class="panel">
        <div class="panel__head"><div><span class="step warning">01</span><h3>获取登录 Cookies</h3></div><Cookie :size="20" /></div>
        <div class="risk-banner"><AlertTriangle :size="20" /><div><strong>实验性非官方方式</strong><p>可能因 X 页面更新失效，也可能触发验证码、登录限制或账号停用。建议只使用专门的低权限账号。</p></div></div>
        <ol class="cookie-steps"><li>在浏览器登录专用 X 账号</li><li>打开开发者工具 → Application</li><li>选择 Cookies → https://x.com</li><li>复制 <code>auth_token</code> 与 <code>ct0</code> 的 Value</li></ol>
        <a class="terms-link" href="https://x.com/en/tos" target="_blank" rel="noopener noreferrer">阅读 X 服务条款 <ArrowUpRight :size="14" /></a>
      </section>

      <section class="panel credential-panel">
        <div class="panel__head"><div><span class="step warning">02</span><h3>twscrape 凭据</h3></div><ShieldCheck :size="20" /></div>
        <div class="status-strip"><span><Database :size="16" /><small>MySQL</small><strong>{{ twscrape?.configured ? twscrape.account_hint : '未保存' }}</strong></span><span><Unplug :size="16" /><small>Redis</small><strong>{{ twscrape?.cache_active ? `${twscrape.cache_ttl_seconds}s` : '未缓存' }}</strong></span><span><CheckCircle2 :size="16" /><small>状态</small><strong>{{ verificationText(twscrape?.verification_status) }}</strong></span></div>
        <el-input v-model="accountLabel" size="large" maxlength="64" placeholder="账号标识，例如 x-reader" />
        <el-input v-model="authToken" type="password" show-password clearable size="large" autocomplete="new-password" :placeholder="twscrape?.configured ? '••••••••（已配置，填写新值可轮换）' : 'auth_token Cookie Value'" />
        <el-input v-model="ct0" type="password" show-password clearable size="large" autocomplete="new-password" :placeholder="twscrape?.configured ? '••••••••（已配置，填写新值可轮换）' : 'ct0 Cookie Value'" />
        <p class="security-copy"><ShieldCheck :size="16" />Cookie 加密持久化到 MySQL；Redis 仅缓存密文。Worker 临时 SQLite 权限为 600，退出即删除。</p>
        <el-checkbox v-model="twscrapeAcknowledged">我了解非官方抓取可能违反 X 条款并导致账号受限</el-checkbox>
        <button class="primary-button experimental" type="button" :disabled="actionLoading || !authToken.trim() || !ct0.trim()" @click="saveTwscrape"><Cookie :size="17" />{{ twscrape?.configured ? '轮换并保存 Cookies' : '加密保存 Cookies' }}</button>
        <div v-if="twscrape?.configured" class="manage-actions"><button type="button" :disabled="actionLoading" @click="testTwscrape"><RefreshCw :size="16" />测试 twscrape</button><button class="danger" type="button" :disabled="actionLoading" @click="removeTwscrape"><Trash2 :size="16" />删除</button><span>版本 v{{ twscrape.version }}</span></div>
        <p v-if="twscrape?.last_error" class="last-error">{{ twscrape.last_error }}</p>
      </section>
    </div>
</section>
</template>
