<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  AlertTriangle, CheckCircle2, ExternalLink, KeyRound,
  LogIn, Play, Plus, RefreshCw, Save, Send, Server, Settings2, Trash2,
} from 'lucide-vue-next'
import { xiaohongshuApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { XhsConnectionStatus, XhsPublishJob, XhsPublishSettings, XhsPublishStrategy, XhsVisibility } from '@/types'
import { formatDateTime } from '@/utils/format'

const activeTab = ref('connection')
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const connection = ref<XhsConnectionStatus | null>(null)
const settings = ref<XhsPublishSettings | null>(null)
const jobs = ref<XhsPublishJob[]>([])
const jobsTotal = ref(0)
const page = ref(1)
const statusFilter = ref('')
const qrVisible = ref(false)
const qrDataUrl = ref('')

const connectionForm = reactive({
  name: '小红书 MCP', connector: 'xiaohongshu_mcp' as const,
  mcp_url: 'http://127.0.0.1:18060/mcp', auth_token: '', risk_acknowledged: false,
})
const settingsForm = reactive({
  enabled: false, default_strategy: 'manual' as XhsPublishStrategy,
  default_delay_minutes: 60, max_attempts: 3, daily_publish_limit: 10,
  default_visibility: '公开可见' as XhsVisibility, declare_original: false,
})
const articleForm = reactive({
  source_ai_draft_id: undefined as number | undefined,
  title: '', content: '', imagesText: '', tagsText: '', productsText: '',
  visibility: '公开可见' as XhsVisibility, is_original: false,
  strategy: 'manual' as XhsPublishStrategy, scheduled_at: '' as string | Date,
})

const loggedIn = computed(() => connection.value?.login_status === 'logged_in')

function applyConnection(value: XhsConnectionStatus) {
  connection.value = value
  if (value.configured) {
    connectionForm.name = value.name || '小红书 MCP'
    connectionForm.mcp_url = value.mcp_url || 'http://127.0.0.1:18060/mcp'
    connectionForm.risk_acknowledged = value.risk_acknowledged
  }
}
function applySettings(value: XhsPublishSettings) {
  settings.value = value
  Object.assign(settingsForm, {
    enabled: value.enabled, default_strategy: value.default_strategy,
    default_delay_minutes: value.default_delay_minutes, max_attempts: value.max_attempts,
    daily_publish_limit: value.daily_publish_limit, default_visibility: value.default_visibility,
    declare_original: value.declare_original,
  })
  articleForm.strategy = value.default_strategy
  articleForm.visibility = value.default_visibility
  articleForm.is_original = value.declare_original
}

async function loadAll() {
  loading.value = true
  try {
    const [connectionValue, settingsValue] = await Promise.all([xiaohongshuApi.connection(), xiaohongshuApi.settings()])
    applyConnection(connectionValue); applySettings(settingsValue); await loadJobs()
  } catch (error) { ElMessage.error(getErrorMessage(error, '读取小红书配置失败')) }
  finally { loading.value = false }
}
async function loadJobs() {
  const result = await xiaohongshuApi.jobs({ page: page.value, page_size: 20, status: statusFilter.value || undefined })
  jobs.value = result.items; jobsTotal.value = result.total
}
async function saveConnection() {
  if (!connectionForm.name.trim() || !connectionForm.mcp_url.trim()) return ElMessage.warning('请填写连接名称和 MCP 地址')
  if (!connectionForm.risk_acknowledged) return ElMessage.warning('请先确认非官方接入风险')
  saving.value = true
  try {
    applyConnection(await xiaohongshuApi.saveConnection({
      ...connectionForm, name: connectionForm.name.trim(), mcp_url: connectionForm.mcp_url.trim(),
      auth_token: connectionForm.auth_token.trim() || null,
    }))
    connectionForm.auth_token = ''
    ElMessage.success('小红书连接已加密保存')
  } catch (error) { ElMessage.error(getErrorMessage(error, '保存连接失败')) }
  finally { saving.value = false }
}
async function testConnection() {
  testing.value = true
  try {
    const result = await xiaohongshuApi.testConnection()
    result.logged_in ? ElMessage.success('MCP 已连接且小红书已登录') : ElMessage.warning(`MCP 可访问，但账号未登录：${result.message}`)
    applyConnection(await xiaohongshuApi.connection())
  } catch (error) { ElMessage.error(getErrorMessage(error, '连接测试失败')) }
  finally { testing.value = false }
}
async function showLoginQr() {
  testing.value = true
  try {
    const result = await xiaohongshuApi.loginQr()
    qrDataUrl.value = `data:${result.mime_type};base64,${result.image_data}`
    qrVisible.value = true
  } catch (error) { ElMessage.error(getErrorMessage(error, '获取登录二维码失败')) }
  finally { testing.value = false }
}
async function removeConnection() {
  try { await ElMessageBox.confirm('删除连接会同步停用自动发布，但不会删除历史任务。', '删除小红书连接', { type: 'warning' }) } catch { return }
  await xiaohongshuApi.removeConnection(); await loadAll(); ElMessage.success('连接已删除')
}
async function saveSettings() {
  saving.value = true
  try { applySettings(await xiaohongshuApi.saveSettings({ ...settingsForm })); ElMessage.success('发布策略已保存') }
  catch (error) { ElMessage.error(getErrorMessage(error, '保存发布策略失败')) }
  finally { saving.value = false }
}
function valuesOf(text: string) { return text.split(/[\n,，]/).map((v) => v.trim()).filter(Boolean) }
async function createArticle() {
  if (!articleForm.title.trim() || !articleForm.content.trim()) return ElMessage.warning('请填写标题和正文')
  const images = valuesOf(articleForm.imagesText)
  if (!images.length) return ElMessage.warning('至少填写一张 HTTP 图片地址或服务器绝对路径')
  if (articleForm.strategy === 'delayed' && !articleForm.scheduled_at) return ElMessage.warning('请选择延迟发布时间')
  saving.value = true
  try {
    await xiaohongshuApi.createJob({
      source_ai_draft_id: articleForm.source_ai_draft_id || null,
      title: articleForm.title.trim(), content: articleForm.content.trim(), images,
      tags: valuesOf(articleForm.tagsText), products: valuesOf(articleForm.productsText),
      visibility: articleForm.visibility, is_original: articleForm.is_original,
      strategy: articleForm.strategy,
      scheduled_at: articleForm.strategy === 'delayed' ? new Date(articleForm.scheduled_at).toISOString() : null,
    })
    Object.assign(articleForm, { source_ai_draft_id: undefined, title: '', content: '', imagesText: '', tagsText: '', productsText: '', scheduled_at: '' })
    page.value = 1; await loadJobs(); activeTab.value = 'jobs'
    ElMessage.success(articleForm.strategy === 'manual' ? '文章已保存为草稿' : '发布任务已进入队列')
  } catch (error) { ElMessage.error(getErrorMessage(error, '创建文章失败')) }
  finally { saving.value = false }
}
async function jobAction(job: XhsPublishJob, action: 'publish' | 'cancel' | 'retry') {
  try {
    if (action === 'publish') await xiaohongshuApi.publishNow(job.id)
    if (action === 'cancel') await xiaohongshuApi.cancel(job.id)
    if (action === 'retry') await xiaohongshuApi.retry(job.id)
    await loadJobs(); ElMessage.success('任务状态已更新')
  } catch (error) { ElMessage.error(getErrorMessage(error, '更新任务失败')) }
}
function statusText(value: string) {
  return ({ draft: '草稿', queued: '等待发布', publishing: '发布中', retry_wait: '等待重试', published: '已发布', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] || value
}
function statusType(value: string) {
  return ({ published: 'success', failed: 'danger', publishing: 'warning', queued: 'primary', retry_wait: 'warning', cancelled: 'info' } as Record<string, string>)[value] || 'info'
}

onMounted(loadAll)
</script>

<template>
  <div class="xhs-page" v-loading="loading">
    <section class="xhs-hero">
      <div class="hero-mark">RED</div>
      <div><span>OPEN-SOURCE PUBLISHING BRIDGE</span><h2>小红书内容发布中心</h2><p>通过独立 MCP 浏览器服务扫码登录，统一管理图文、审核与发布队列。</p></div>
      <div class="hero-state" :class="{ ready: loggedIn }"><small>ACCOUNT</small><strong>{{ loggedIn ? '已登录' : connection?.configured ? '待扫码登录' : '未配置' }}</strong><em><i /> {{ settings?.worker_status === 'online' ? 'WORKER ONLINE' : 'WORKER OFFLINE' }}</em></div>
    </section>

    <el-alert type="warning" :closable="false" show-icon title="非官方接入提示：发布能力依赖小红书网页与开源浏览器自动化，页面变化、验证码或平台风控都可能导致失败；请只发布原创或已授权内容。" />

    <section class="main-panel">
      <el-tabs v-model="activeTab">
        <el-tab-pane label="连接与登录" name="connection">
          <div class="two-columns">
            <div class="card">
              <header><Server :size="18" /><div><h3>MCP 执行端</h3><p>推荐 xpzouying/xiaohongshu-mcp，默认端口 18060</p></div></header>
              <el-form label-position="top">
                <el-form-item label="连接名称"><el-input v-model="connectionForm.name" maxlength="100" /></el-form-item>
                <el-form-item label="接入类型"><el-select v-model="connectionForm.connector" disabled><el-option label="xiaohongshu-mcp · Streamable HTTP" value="xiaohongshu_mcp" /></el-select></el-form-item>
                <el-form-item label="MCP URL"><el-input v-model="connectionForm.mcp_url" placeholder="http://127.0.0.1:18060/mcp" /></el-form-item>
                <el-form-item label="访问令牌（可选）"><el-input v-model="connectionForm.auth_token" type="password" show-password :placeholder="connection?.token_configured ? `留空保留 ${connection.token_hint}` : '与 MCP 的 AUTH_TOKEN 保持一致'" /></el-form-item>
                <el-checkbox v-model="connectionForm.risk_acknowledged">我已理解这是非官方网页自动化接入，并自行承担账号与内容合规风险</el-checkbox>
                <el-button type="primary" :loading="saving" @click="saveConnection"><Save :size="15" />保存连接</el-button>
              </el-form>
            </div>
            <div class="card login-card">
              <header><LogIn :size="18" /><div><h3>扫码登录</h3><p>Cookie 只保存在 MCP 服务目录，不进入本系统数据库</p></div></header>
              <div class="login-status"><span :class="{ online: loggedIn }"><i />{{ loggedIn ? '小红书已登录' : '尚未登录' }}</span><small>MCP {{ connection?.verification_status === 'valid' ? '可访问' : '未验证' }}</small></div>
              <div class="secure-list"><span><KeyRound :size="15" />{{ connection?.token_configured ? `Token ${connection.token_hint}` : 'MCP 未启用 Token' }}</span><span><Server :size="15" />{{ connection?.mcp_url || '尚未保存服务地址' }}</span></div>
              <el-button :loading="testing" @click="testConnection"><CheckCircle2 :size="15" />测试连接与登录</el-button>
              <el-button type="primary" plain :disabled="!connection?.configured" :loading="testing" @click="showLoginQr"><LogIn :size="15" />获取登录二维码</el-button>
              <el-button v-if="connection?.configured" type="danger" plain @click="removeConnection"><Trash2 :size="15" />删除连接</el-button>
              <p v-if="connection?.last_error" class="error-copy">{{ connection.last_error }}</p>
            </div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="发布策略" name="settings">
          <div class="settings-grid">
            <div class="card switch-card"><header><Settings2 :size="18" /><div><h3>自动发布总开关</h3><p>关闭后任务仍保留，但 Worker 不会执行</p></div></header><el-switch v-model="settingsForm.enabled" size="large" active-text="允许发布" inactive-text="暂停发布" /></div>
            <div class="card"><el-form label-position="top"><el-form-item label="默认策略"><el-radio-group v-model="settingsForm.default_strategy"><el-radio-button value="manual">手动审核</el-radio-button><el-radio-button value="automatic">立即自动</el-radio-button><el-radio-button value="delayed">延迟发布</el-radio-button></el-radio-group></el-form-item><el-form-item label="默认延迟（分钟）"><el-input-number v-model="settingsForm.default_delay_minutes" :min="1" :max="20160" /></el-form-item><el-form-item label="每日发布上限"><el-input-number v-model="settingsForm.daily_publish_limit" :min="1" :max="50" /></el-form-item><el-form-item label="失败重试次数"><el-input-number v-model="settingsForm.max_attempts" :min="1" :max="10" /></el-form-item></el-form></div>
            <div class="card"><el-form label-position="top"><el-form-item label="默认可见范围"><el-select v-model="settingsForm.default_visibility"><el-option label="公开可见" value="公开可见" /><el-option label="仅自己可见" value="仅自己可见" /><el-option label="仅互关好友可见" value="仅互关好友可见" /></el-select></el-form-item><el-form-item><el-checkbox v-model="settingsForm.declare_original">默认声明为原创内容</el-checkbox></el-form-item><p class="hint"><AlertTriangle :size="15" />“自动”代表创建后立即进入队列，不绕过平台验证码或风控。</p><el-button type="primary" :loading="saving" @click="saveSettings"><Save :size="15" />保存发布策略</el-button></el-form></div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="新建文章" name="compose">
          <div class="compose-grid">
            <div class="card editor-card"><el-form label-position="top"><div class="form-row"><el-form-item label="标题（最多20字）"><el-input v-model="articleForm.title" maxlength="20" show-word-limit /></el-form-item><el-form-item label="关联 AI 草稿 ID（可选）"><el-input-number v-model="articleForm.source_ai_draft_id" :min="1" controls-position="right" /></el-form-item></div><el-form-item label="正文（最多1000字）"><el-input v-model="articleForm.content" type="textarea" :rows="9" maxlength="1000" show-word-limit /></el-form-item><el-form-item label="图片（每行一个 HTTP URL 或服务器绝对路径）"><el-input v-model="articleForm.imagesText" type="textarea" :rows="4" placeholder="https://example.com/cover.jpg" /></el-form-item><div class="form-row"><el-form-item label="话题标签"><el-input v-model="articleForm.tagsText" placeholder="AI工具, 效率提升" /></el-form-item><el-form-item label="商品关键词（可选）"><el-input v-model="articleForm.productsText" placeholder="需账号开通商品能力" /></el-form-item></div></el-form></div>
            <div class="card publish-card"><header><Send :size="18" /><div><h3>发布配置</h3><p>每篇文章可覆盖默认策略</p></div></header><el-form label-position="top"><el-form-item label="执行策略"><el-select v-model="articleForm.strategy"><el-option label="手动：先保存草稿" value="manual" /><el-option label="自动：保存后立即排队" value="automatic" /><el-option label="延迟：指定时间排队" value="delayed" /></el-select></el-form-item><el-form-item v-if="articleForm.strategy === 'delayed'" label="发布时间"><el-date-picker v-model="articleForm.scheduled_at" type="datetime" placeholder="选择日期时间" style="width:100%" /></el-form-item><el-form-item label="可见范围"><el-select v-model="articleForm.visibility"><el-option label="公开可见" value="公开可见" /><el-option label="仅自己可见" value="仅自己可见" /><el-option label="仅互关好友可见" value="仅互关好友可见" /></el-select></el-form-item><el-checkbox v-model="articleForm.is_original">声明原创</el-checkbox><el-button type="primary" size="large" :loading="saving" @click="createArticle"><Plus :size="16" />创建文章任务</el-button></el-form></div>
          </div>
        </el-tab-pane>

        <el-tab-pane label="发布任务" name="jobs">
          <div class="job-toolbar"><el-select v-model="statusFilter" clearable placeholder="全部状态" @change="page=1; loadJobs()"><el-option v-for="item in ['draft','queued','publishing','retry_wait','published','failed','cancelled']" :key="item" :label="statusText(item)" :value="item" /></el-select><el-button @click="loadJobs"><RefreshCw :size="15" />刷新</el-button></div>
          <el-table :data="jobs" stripe><el-table-column label="文章" min-width="280"><template #default="{ row }"><strong>{{ row.title }}</strong><p class="content-preview">{{ row.content }}</p></template></el-table-column><el-table-column label="策略" width="110"><template #default="{ row }">{{ row.strategy === 'manual' ? '手动' : row.strategy === 'automatic' ? '自动' : '延迟' }}</template></el-table-column><el-table-column label="状态" width="115"><template #default="{ row }"><el-tag :type="statusType(row.status)">{{ statusText(row.status) }}</el-tag></template></el-table-column><el-table-column label="计划/完成时间" width="180"><template #default="{ row }"><span>{{ formatDateTime(row.published_at || row.scheduled_at || row.created_at) }}</span></template></el-table-column><el-table-column label="尝试" width="75"><template #default="{ row }">{{ row.attempts }}/{{ row.max_attempts }}</template></el-table-column><el-table-column label="操作" width="210" fixed="right"><template #default="{ row }"><el-button v-if="['draft','cancelled'].includes(row.status)" size="small" type="primary" @click="jobAction(row,'publish')"><Play :size="13" />发布</el-button><el-button v-if="['queued','retry_wait'].includes(row.status)" size="small" @click="jobAction(row,'cancel')">取消</el-button><el-button v-if="row.status === 'failed'" size="small" type="warning" @click="jobAction(row,'retry')">重试</el-button><a v-if="row.platform_url" :href="row.platform_url" target="_blank"><ExternalLink :size="15" /></a></template></el-table-column></el-table>
          <el-pagination v-if="jobsTotal > 20" v-model:current-page="page" :page-size="20" :total="jobsTotal" layout="prev, pager, next, total" @current-change="loadJobs" />
        </el-tab-pane>
      </el-tabs>
    </section>

    <el-dialog v-model="qrVisible" title="使用小红书 App 扫码登录" width="390px" align-center><div class="qr-dialog"><img v-if="qrDataUrl" :src="qrDataUrl" alt="小红书登录二维码" /><p>扫码完成后关闭窗口，再点击“测试连接与登录”。不要同时在其他网页端登录同一账号。</p><el-button type="primary" @click="qrVisible=false; testConnection()"><CheckCircle2 :size="15" />我已扫码，检查状态</el-button></div></el-dialog>
  </div>
</template>

<style scoped>
.xhs-page{display:flex;flex-direction:column;gap:14px;max-width:1250px}.xhs-hero,.main-panel,.card{border:1px solid var(--border-color);border-radius:14px;background:var(--surface-primary)}.xhs-hero{display:grid;grid-template-columns:auto 1fr auto;gap:16px;align-items:center;padding:22px;background:linear-gradient(120deg,rgba(240,70,72,.18),rgba(12,17,26,.97) 55%)}.hero-mark{display:grid;place-items:center;width:54px;height:54px;border-radius:15px;background:#ef3d42;color:white;font-size:12px;font-weight:800;letter-spacing:.12em}.xhs-hero span{color:#ff7779;font-size:10px;letter-spacing:.15em}.xhs-hero h2{margin:4px 0;font-size:23px}.xhs-hero p,.card header p,.hint{margin:0;color:var(--text-secondary);font-size:12px}.hero-state{min-width:168px;padding:11px 14px;border:1px solid rgba(255,125,125,.25);border-radius:10px;background:rgba(4,8,14,.45)}.hero-state small,.hero-state strong,.hero-state em{display:block}.hero-state strong{margin:3px 0 7px}.hero-state em{color:#e3a04e;font-size:9px;font-style:normal}.hero-state i{display:inline-block;width:6px;height:6px;border-radius:50%;background:#e3a04e}.hero-state.ready{border-color:rgba(67,218,157,.28)}.hero-state.ready em{color:#49d59b}.hero-state.ready i{background:#49d59b}.main-panel{padding:0 18px 18px}.two-columns,.compose-grid{display:grid;grid-template-columns:1.25fr .75fr;gap:14px}.settings-grid{display:grid;grid-template-columns:.7fr 1fr 1fr;gap:14px}.card{padding:19px}.card header{display:flex;gap:9px;align-items:center;margin-bottom:17px}.card header svg{color:#ff6f72}.card h3{margin:0 0 2px;font-size:15px}.card :deep(.el-select){width:100%}.card .el-button{margin-top:13px}.login-card{display:flex;flex-direction:column}.login-card>.el-button{margin-left:0}.login-status{display:flex;align-items:center;justify-content:space-between;padding:14px;border-radius:10px;background:var(--surface-secondary)}.login-status span{font-weight:650}.login-status i{display:inline-block;width:8px;height:8px;margin-right:7px;border-radius:50%;background:#e4a047}.login-status span.online i{background:#47d59c;box-shadow:0 0 12px #47d59c}.login-status small{color:var(--text-secondary)}.secure-list{display:flex;flex-direction:column;gap:8px;margin:14px 0}.secure-list span{display:flex;gap:7px;align-items:center;overflow:hidden;color:var(--text-secondary);font-size:11px}.switch-card{display:flex;flex-direction:column;justify-content:space-between;min-height:175px}.hint{display:flex;gap:6px;line-height:1.55}.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px}.publish-card .el-button{width:100%}.job-toolbar{display:flex;justify-content:flex-end;gap:8px;margin-bottom:12px}.job-toolbar .el-select{width:150px}.content-preview{overflow:hidden;max-width:420px;margin:3px 0 0;color:var(--text-secondary);font-size:11px;text-overflow:ellipsis;white-space:nowrap}.error-copy{padding:9px;border-radius:8px;background:rgba(235,75,75,.1);color:#ff8587;font-size:11px}.qr-dialog{text-align:center}.qr-dialog img{width:260px;height:260px;border-radius:10px;background:white;object-fit:contain}.qr-dialog p{color:var(--text-secondary);font-size:12px;line-height:1.6}.el-pagination{justify-content:flex-end;margin-top:14px}@media(max-width:980px){.two-columns,.compose-grid,.settings-grid{grid-template-columns:1fr}.xhs-hero{grid-template-columns:auto 1fr}.hero-state{grid-column:1/-1}}@media(max-width:600px){.form-row{grid-template-columns:1fr}}
</style>
