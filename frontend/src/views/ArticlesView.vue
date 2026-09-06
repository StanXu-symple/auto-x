<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import {
  Bot, CheckCircle2, Clock3, Edit3, FilePlus2, FileText, History, ImagePlus, Plus,
  RefreshCw, RotateCcw, Search, Send, Trash2, UploadCloud, UserRound, XCircle,
} from 'lucide-vue-next'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import { articlesApi, qqApi, xhsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type {
  Article, ArticlePayload, ArticlePublishChannel, ArticlePublishHistory,
  ArticlePublishStatus, ArticleSource, QQBotAccount, QQJoinedGroup,
} from '@/types'
import { formatDateTime } from '@/utils/format'

interface ImagePreview { path: string; url: string }

const ui = useUiStore()
const articles = ref<Article[]>([])
const total = ref(0)
const loading = ref(true)
const saving = ref(false)
const uploading = ref(false)
const publishing = ref(false)
const error = ref('')
const dialogError = ref('')
const dialogOpen = ref(false)
const publishOpen = ref(false)
const historyOpen = ref(false)
const historyLoading = ref(false)
const historyArticle = ref<Article | null>(null)
const publishHistory = ref<ArticlePublishHistory[]>([])
const editingArticle = ref<Article | null>(null)
const publishingArticle = ref<Article | null>(null)
const formPreviews = ref<ImagePreview[]>([])
const publishPreviews = ref<ImagePreview[]>([])
const qqBots = ref<QQBotAccount[]>([])
const qqGroups = ref<QQJoinedGroup[]>([])
const verificationVisible = ref(false)
const verificationImage = ref('')
const verificationVersion = ref('')
let verificationTimer: ReturnType<typeof setInterval> | undefined
let articleRefreshTimer: ReturnType<typeof setInterval> | undefined

const filters = reactive({ keyword: '', article_source: 'all', publish_status: 'all' })
const appliedFilters = reactive({ keyword: '', article_source: 'all', publish_status: 'all' })
const pagination = reactive({ page: 1, page_size: 15 })
const form = reactive<ArticlePayload>({ title: '', content: '', excerpt: '', images: [] })
const publishForm = reactive({ channel: 'qq' as ArticlePublishChannel, bot_id: null as number | null, group_openids: [] as string[] })

const currentAiCount = computed(() => articles.value.filter((article) => article.article_source === 'ai').length)
const currentUserCount = computed(() => articles.value.filter((article) => article.article_source === 'user').length)
const hasFilters = computed(() => Boolean(filters.keyword.trim() || filters.article_source !== 'all' || filters.publish_status !== 'all'))
const qqPreview = computed(() => publishingArticle.value ? `标题:${publishingArticle.value.title}\n摘要:${publishingArticle.value.excerpt || ''}\n正文:${publishingArticle.value.content}` : '')
const qqCharCount = computed(() => Array.from(qqPreview.value).length)
const qqMessageCount = computed(() => Math.max(1, Math.ceil(qqCharCount.value / 2000)))

const sourceMeta: Record<ArticleSource, { label: string; icon: typeof Bot; type: 'primary' | 'success' }> = {
  ai: { label: 'AI 生成', icon: Bot, type: 'primary' }, user: { label: '用户生成', icon: UserRound, type: 'success' },
}
const publishStatusMeta: Record<ArticlePublishStatus, { label: string; type: 'info' | 'warning' | 'success' | 'danger'; icon: typeof Clock3 }> = {
  unpublished: { label: '未推送', type: 'info', icon: Clock3 }, queued: { label: '推送中', type: 'warning', icon: Clock3 },
  published: { label: '已推送', type: 'success', icon: CheckCircle2 }, failed: { label: '推送失败', type: 'danger', icon: XCircle },
}

function formatGmt8DateTime(value?: string | null) {
  if (!value) return '暂无'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '暂无'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    second: '2-digit', hour12: false, timeZone: 'Asia/Shanghai',
  }).format(date)
}

function publishActionLabel(article: Article) {
  if (article.publish_status === 'failed') return '重试'
  if (article.publish_status === 'published') return '再次推送'
  return '推送'
}

async function showPublishHistory(article: Article) {
  historyArticle.value = article
  publishHistory.value = []
  historyOpen.value = true
  historyLoading.value = true
  try {
    publishHistory.value = (await articlesApi.publishHistory(article.id, { page: 1, page_size: 100 })).items
  } catch (requestError) {
    ui.toast('推送历史加载失败', 'error', getErrorMessage(requestError))
  } finally {
    historyLoading.value = false
  }
}

function revokePreviews(previews: ImagePreview[]) { previews.forEach((preview) => URL.revokeObjectURL(preview.url)) }
async function resolvePreviews(paths: string[]): Promise<ImagePreview[]> {
  const previews: ImagePreview[] = []
  try {
    for (const path of paths) previews.push({ path, url: URL.createObjectURL(await articlesApi.image(path)) })
    return previews
  } catch (error) {
    revokePreviews(previews)
    throw error
  }
}

async function loadArticles() {
  loading.value = true
  error.value = ''
  try {
    const result = await articlesApi.list({
      page: pagination.page, page_size: pagination.page_size, keyword: appliedFilters.keyword || undefined,
      article_source: appliedFilters.article_source === 'all' ? undefined : appliedFilters.article_source as ArticleSource,
      publish_status: appliedFilters.publish_status === 'all' ? undefined : appliedFilters.publish_status as ArticlePublishStatus,
    })
    articles.value = result.items
    total.value = result.total
  } catch (requestError) { error.value = getErrorMessage(requestError, '文章列表加载失败') }
  finally { loading.value = false }
}

function runQuery() {
  Object.assign(appliedFilters, { keyword: filters.keyword.trim(), article_source: filters.article_source, publish_status: filters.publish_status })
  pagination.page = 1
  loadArticles()
}
function resetQuery() { Object.assign(filters, { keyword: '', article_source: 'all', publish_status: 'all' }); runQuery() }
function resetEditorImages() { revokePreviews(formPreviews.value); formPreviews.value = []; form.images = [] }

function openCreate() {
  editingArticle.value = null
  resetEditorImages()
  Object.assign(form, { title: '', content: '', excerpt: '', images: [] })
  dialogError.value = ''
  dialogOpen.value = true
}

async function openEdit(article: Article) {
  editingArticle.value = article
  resetEditorImages()
  Object.assign(form, { title: article.title, content: article.content, excerpt: article.excerpt || '', images: [...article.images] })
  dialogError.value = ''
  dialogOpen.value = true
  try { formPreviews.value = await resolvePreviews(article.images) }
  catch (requestError) { dialogError.value = getErrorMessage(requestError, '部分文章图片加载失败') }
}

async function uploadFiles(files: File[]) {
  if (!files.length) return
  if (form.images.length + files.length > 18) return ui.toast('最多上传 18 张图片', 'warning')
  uploading.value = true
  try {
    const result = await articlesApi.upload(files)
    result.files.forEach((item, index) => {
      const file = files[index]
      if (!file) return
      form.images.push(item.path)
      formPreviews.value.push({ path: item.path, url: URL.createObjectURL(file) })
    })
    ui.toast(`已上传 ${result.files.length} 张图片`, 'success')
  } catch (requestError) { ui.toast('图片上传失败', 'error', getErrorMessage(requestError)) }
  finally { uploading.value = false }
}

async function chooseFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  await uploadFiles(files)
}

async function pasteFiles(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.items || []).filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item, index) => {
    const file = item.getAsFile()
    if (!file) return null
    const extension = { 'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp' }[file.type]
    return extension ? new File([file], `clipboard-${Date.now()}-${index}.${extension}`, { type: file.type }) : file
  }).filter((file): file is File => file !== null)
  if (!files.length) return
  event.preventDefault()
  await uploadFiles(files)
}

function removeImage(index: number) {
  const preview = formPreviews.value[index]
  if (!preview) return
  URL.revokeObjectURL(preview.url)
  formPreviews.value.splice(index, 1)
  form.images.splice(index, 1)
}

async function saveArticle() {
  dialogError.value = ''
  if (!form.title.trim() || !form.content.trim()) return void (dialogError.value = '请填写文章标题和正文')
  saving.value = true
  try {
    const payload: ArticlePayload = { title: form.title.trim(), content: form.content.trim(), excerpt: form.excerpt?.trim() || null, images: [...form.images] }
    const wasEditing = Boolean(editingArticle.value)
    if (editingArticle.value) await articlesApi.update(editingArticle.value.id, { ...payload, revision: editingArticle.value.revision })
    else await articlesApi.create(payload)
    dialogOpen.value = false
    pagination.page = 1
    await loadArticles()
    ui.toast(wasEditing ? '文章已更新' : '文章已创建', 'success')
  } catch (requestError) { dialogError.value = getErrorMessage(requestError, '文章保存失败') }
  finally { saving.value = false }
}

async function removeArticle(article: Article) {
  try { await ElMessageBox.confirm(`删除文章「${article.title}」？此操作无法撤销。`, '删除文章', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }) }
  catch { return }
  try {
    await articlesApi.remove(article.id)
    if (articles.value.length === 1 && pagination.page > 1) pagination.page -= 1
    await loadArticles()
    ui.toast('文章已删除', 'success')
  } catch (requestError) { ui.toast('删除文章失败', 'error', getErrorMessage(requestError)) }
}

async function openPublish(article: Article) {
  publishingArticle.value = { ...article, images: [...article.images] }
  Object.assign(publishForm, { channel: 'qq', bot_id: null, group_openids: [] })
  revokePreviews(publishPreviews.value)
  publishPreviews.value = []
  publishOpen.value = true
  try {
    const [bots, previews] = await Promise.all([qqApi.bots(), resolvePreviews(article.images)])
    qqBots.value = bots.filter((bot) => bot.is_enabled)
    publishPreviews.value = previews
    publishForm.bot_id = Number(qqBots.value[0]?.id) || null
    if (publishForm.bot_id) await changeQqBot(publishForm.bot_id)
  } catch (requestError) { ui.toast('推送配置加载失败', 'error', getErrorMessage(requestError)) }
}

async function changeQqBot(id: number | null) {
  publishForm.group_openids = []
  qqGroups.value = id ? await qqApi.joinedGroups(id) : []
}

async function pollVerification() {
  try {
    const result = await xhsApi.verification(verificationVersion.value || undefined)
    if (!result.required) {
      verificationVisible.value = false
      verificationImage.value = ''
      verificationVersion.value = ''
      return
    }
    verificationVisible.value = true
    if (result.image) verificationImage.value = result.image
    if (result.version) verificationVersion.value = result.version
  } catch {
    // The article publish request remains authoritative while polling recovers.
  }
}

function stopVerificationPolling() {
  if (verificationTimer) clearInterval(verificationTimer)
  verificationTimer = undefined
}

function startVerificationPolling() {
  stopVerificationPolling()
  verificationImage.value = ''
  verificationVersion.value = ''
  void pollVerification()
  verificationTimer = setInterval(() => void pollVerification(), 1000)
}

async function publishArticle() {
  const article = publishingArticle.value
  if (!article) return
  if (publishForm.channel === 'qq' && (!publishForm.bot_id || !publishForm.group_openids.length)) return ui.toast('请选择 QQ 机器人和发送群', 'warning')
  if (publishForm.channel === 'xhs' && !article.images.length) return ui.toast('小红书推送至少需要一张图片', 'warning')
  publishing.value = true
  if (publishForm.channel === 'xhs') startVerificationPolling()
  try {
    const result = await articlesApi.publish(article.id, {
      channel: publishForm.channel,
      bot_id: publishForm.channel === 'qq' ? publishForm.bot_id || undefined : undefined,
      group_openids: publishForm.channel === 'qq' ? publishForm.group_openids : undefined,
    })
    publishOpen.value = false
    await loadArticles()
    ui.toast(result.message, 'success')
  } catch (requestError) { await loadArticles(); ui.toast('文章推送失败', 'error', getErrorMessage(requestError)) }
  finally {
    stopVerificationPolling()
    verificationVisible.value = false
    publishing.value = false
  }
}

function articlePreview(article: Article) { return article.excerpt || article.content }
function closePublishPreview() { revokePreviews(publishPreviews.value); publishPreviews.value = [] }
onMounted(() => {
  void loadArticles()
  articleRefreshTimer = setInterval(() => {
    if (!loading.value && articles.value.some((article) => article.publish_status === 'queued')) void loadArticles()
  }, 5000)
})
onBeforeUnmount(() => {
  stopVerificationPolling()
  if (articleRefreshTimer) clearInterval(articleRefreshTimer)
  revokePreviews(formPreviews.value)
  revokePreviews(publishPreviews.value)
})
</script>

<template>
  <div class="articles-page page-stack">
    <section class="articles-hero">
      <div class="articles-hero__copy"><span class="articles-hero__icon"><FileText :size="24" /></span><div><span class="eyebrow">CONTENT LIBRARY</span><h2>文章管理</h2><p>集中维护文章、图片和内容推送状态。</p></div></div>
      <div class="articles-hero__metrics"><span><small>文章总数</small><strong>{{ total }}</strong></span><span><small>当前页 AI 生成</small><strong>{{ currentAiCount }}</strong></span><span><small>当前页用户生成</small><strong>{{ currentUserCount }}</strong></span></div>
      <el-button type="primary" @click="openCreate"><Plus :size="16" />新增文章</el-button>
    </section>
    <section class="articles-panel">
      <header class="articles-toolbar"><div><h3>文章库</h3><p>按标题、正文、来源或状态定位文章</p></div><el-tooltip content="刷新文章列表"><el-button circle :loading="loading" aria-label="刷新文章列表" @click="loadArticles"><RefreshCw v-if="!loading" :size="16" /></el-button></el-tooltip></header>
      <div class="articles-query">
        <label class="articles-query__keyword"><span>关键词</span><el-input v-model="filters.keyword" clearable placeholder="搜索标题、摘要或正文" @keyup.enter="runQuery"><template #prefix><Search :size="15" /></template></el-input></label>
        <label><span>文章来源</span><el-select v-model="filters.article_source"><el-option label="全部来源" value="all" /><el-option label="AI 生成" value="ai" /><el-option label="用户生成" value="user" /></el-select></label>
        <label><span>推送状态</span><el-select v-model="filters.publish_status"><el-option label="全部状态" value="all" /><el-option label="未推送" value="unpublished" /><el-option label="推送中" value="queued" /><el-option label="已推送" value="published" /><el-option label="推送失败" value="failed" /></el-select></label>
        <div class="articles-query__actions"><el-button :disabled="!hasFilters && !appliedFilters.keyword && appliedFilters.article_source === 'all' && appliedFilters.publish_status === 'all'" @click="resetQuery">重置</el-button><el-button type="primary" :loading="loading" @click="runQuery"><Search v-if="!loading" :size="15" />查询</el-button></div>
      </div>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-table v-loading="loading" :data="articles" row-key="id" class="article-table">
        <el-table-column label="文章" min-width="320"><template #default="{ row }"><div class="article-title-cell"><span><FileText :size="17" /></span><div><strong>{{ row.title }}</strong><p>{{ articlePreview(row) }}</p><small v-if="row.images.length"><ImagePlus :size="12" />{{ row.images.length }} 张图片</small></div></div></template></el-table-column>
        <el-table-column label="文章来源" width="122"><template #default="{ row }"><el-tag :type="sourceMeta[row.article_source as ArticleSource].type" effect="plain"><component :is="sourceMeta[row.article_source as ArticleSource].icon" :size="13" />{{ sourceMeta[row.article_source as ArticleSource].label }}</el-tag></template></el-table-column>
        <el-table-column label="推送状态" width="125"><template #default="{ row }"><el-tooltip :disabled="!row.publish_error" :content="row.publish_error"><el-tag :type="publishStatusMeta[row.publish_status as ArticlePublishStatus].type" effect="plain"><component :is="publishStatusMeta[row.publish_status as ArticlePublishStatus].icon" :size="13" />{{ publishStatusMeta[row.publish_status as ArticlePublishStatus].label }}</el-tag></el-tooltip><small v-if="row.publish_channel" class="publish-channel">{{ row.publish_channel === 'qq' ? 'QQ' : '小红书' }}</small></template></el-table-column>
        <el-table-column label="更新时间" width="165"><template #default="{ row }"><span class="article-date"><strong>{{ formatDateTime(row.updated_at) }}</strong><small>v{{ row.revision }}</small></span></template></el-table-column>
        <el-table-column label="操作" width="320" fixed="right"><template #default="{ row }"><div class="article-actions"><el-button v-if="row.publish_status !== 'queued'" size="small" type="primary" plain @click="openPublish(row)"><RotateCcw v-if="row.publish_status === 'failed' || row.publish_status === 'published'" :size="14" /><Send v-else :size="14" />{{ publishActionLabel(row) }}</el-button><el-button size="small" @click="showPublishHistory(row)"><History :size="14" />历史</el-button><el-button size="small" :disabled="row.publish_status === 'queued'" @click="openEdit(row)"><Edit3 :size="14" />编辑</el-button><el-tooltip :content="row.publish_status === 'queued' ? '推送完成后才能删除' : '删除文章'"><el-button circle size="small" type="danger" plain :disabled="row.publish_status === 'queued'" aria-label="删除文章" @click="removeArticle(row)"><Trash2 :size="14" /></el-button></el-tooltip></div></template></el-table-column>
        <template #empty><EmptyState compact title="暂无文章" description="调整查询条件，或创建第一篇文章"><template #icon><FilePlus2 :size="25" /></template><el-button type="primary" @click="openCreate">新增文章</el-button></EmptyState></template>
      </el-table>
      <PaginationBar v-if="total > pagination.page_size" :page="pagination.page" :page-size="pagination.page_size" :total="total" @change="(page) => { pagination.page = page; loadArticles() }" />
    </section>

    <el-dialog v-model="dialogOpen" class="article-dialog" :title="editingArticle ? '编辑文章' : '新增文章'" width="min(800px, 94vw)" destroy-on-close @closed="resetEditorImages">
      <el-alert v-if="dialogError" :title="dialogError" type="error" :closable="false" show-icon />
      <div v-if="editingArticle" class="article-dialog__source"><span>文章来源</span><el-tag :type="sourceMeta[editingArticle.article_source].type" effect="plain"><component :is="sourceMeta[editingArticle.article_source].icon" :size="13" />{{ sourceMeta[editingArticle.article_source].label }}</el-tag></div>
      <div @paste="pasteFiles"><el-form label-position="top" class="article-form">
        <el-form-item label="文章标题" required><el-input v-model="form.title" maxlength="300" show-word-limit placeholder="输入文章标题" /></el-form-item>
        <el-form-item label="文章摘要"><el-input v-model="form.excerpt" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="可选，用于列表和推送摘要" /></el-form-item>
        <el-form-item label="文章正文" required><el-input v-model="form.content" type="textarea" :rows="10" maxlength="50000" show-word-limit placeholder="输入文章正文" /></el-form-item>
        <el-form-item label="文章图片"><label class="article-upload-zone" tabindex="0"><input type="file" accept="image/jpeg,image/png,image/webp" multiple @change="chooseFiles" /><UploadCloud :size="21" /><strong>{{ uploading ? '正在上传...' : '点击或粘贴图片' }}</strong><span>JPG、PNG、WebP，单张不超过 10 MB，最多 18 张</span></label><div v-if="formPreviews.length" class="article-photo-grid"><div v-for="(photo, index) in formPreviews" :key="photo.path" class="article-photo"><button type="button" aria-label="移除图片" @click="removeImage(index)">×</button><el-image :src="photo.url" :preview-src-list="formPreviews.map((item) => item.url)" :initial-index="index" preview-teleported hide-on-click-modal fit="cover" /></div></div></el-form-item>
      </el-form></div>
      <template #footer><el-button @click="dialogOpen = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="uploading" @click="saveArticle">{{ editingArticle ? '保存修改' : '创建文章' }}</el-button></template>
    </el-dialog>

    <el-dialog v-model="publishOpen" class="article-dialog article-publish-dialog" title="推送文章" width="min(780px, 94vw)" :close-on-click-modal="false" @closed="closePublishPreview">
      <div class="publish-lock"><CheckCircle2 :size="16" /><span>以下内容为本次推送快照，不可在此修改</span></div>
      <el-form label-position="top" class="article-form">
        <el-form-item label="推送方式"><el-select v-model="publishForm.channel"><el-option label="QQ 推送" value="qq" /><el-option label="小红书推送" value="xhs" /></el-select></el-form-item>
        <template v-if="publishForm.channel === 'qq'">
          <div class="publish-targets"><el-form-item label="QQ 机器人"><el-select v-model="publishForm.bot_id" placeholder="选择机器人" @change="changeQqBot"><el-option v-for="bot in qqBots" :key="bot.id" :label="bot.name" :value="bot.id" /></el-select></el-form-item><el-form-item label="发送到群"><el-select v-model="publishForm.group_openids" multiple collapse-tags placeholder="选择已加入的群"><el-option v-for="group in qqGroups" :key="group.group_openid" :label="group.name || group.group_openid" :value="group.group_openid" /></el-select></el-form-item></div>
          <el-form-item label="QQ 消息内容"><el-input :model-value="qqPreview" type="textarea" :rows="12" readonly resize="none" /><small class="publish-count" :class="{ 'is-split': qqMessageCount > 1 }">{{ qqCharCount }} 字符，推送时将拆分为 {{ qqMessageCount }} 条文本消息</small></el-form-item>
        </template>
        <template v-else>
          <el-alert v-if="!publishingArticle?.images.length" title="小红书图文推送至少需要一张图片" type="warning" :closable="false" show-icon />
          <el-form-item label="笔记标题"><el-input :model-value="publishingArticle?.title" readonly /></el-form-item>
          <el-form-item label="笔记正文"><el-input :model-value="publishingArticle?.content" type="textarea" :rows="12" readonly resize="none" /></el-form-item>
        </template>
        <el-form-item label="文章图片"><div v-if="publishPreviews.length" class="article-photo-grid is-readonly"><div v-for="(photo, index) in publishPreviews" :key="photo.path" class="article-photo"><el-image :src="photo.url" :preview-src-list="publishPreviews.map((item) => item.url)" :initial-index="index" preview-teleported hide-on-click-modal fit="cover" /></div></div><span v-else class="publish-no-images">未添加图片</span><small v-if="publishForm.channel === 'qq' && publishPreviews.length" class="publish-count">图片将按当前顺序逐张调用 QQ 推送接口</small></el-form-item>
      </el-form>
      <template #footer><el-button :disabled="publishing" @click="publishOpen = false">取消</el-button><el-button type="primary" :loading="publishing" @click="publishArticle"><Send :size="15" />确认推送</el-button></template>
    </el-dialog>

    <el-dialog v-model="historyOpen" class="article-dialog article-history-dialog" :title="`推送历史${historyArticle ? ` · ${historyArticle.title}` : ''}`" width="min(1020px, 94vw)">
      <div v-loading="historyLoading">
        <el-table v-if="publishHistory.length" :data="publishHistory" max-height="460" table-layout="fixed">
          <el-table-column label="时间（GMT+8）" width="190"><template #default="{ row }"><div class="article-history-time"><strong>{{ formatGmt8DateTime(row.created_at) }}</strong><small v-if="row.completed_at">完成 {{ formatGmt8DateTime(row.completed_at) }}</small></div></template></el-table-column>
          <el-table-column label="方式" width="95"><template #default="{ row }"><el-tag effect="plain">{{ row.channel === 'qq' ? 'QQ' : '小红书' }}</el-tag></template></el-table-column>
          <el-table-column label="目标" min-width="180" prop="target_summary" show-overflow-tooltip />
          <el-table-column label="消息数" width="80" align="center" prop="delivery_count" />
          <el-table-column label="状态" width="105"><template #default="{ row }"><el-tooltip :disabled="!row.error" :content="row.error"><el-tag :type="publishStatusMeta[row.status as ArticlePublishStatus].type">{{ publishStatusMeta[row.status as ArticlePublishStatus].label }}</el-tag></el-tooltip></template></el-table-column>
          <el-table-column label="失败原因" min-width="220" prop="error" show-overflow-tooltip><template #default="{ row }">{{ row.error || '-' }}</template></el-table-column>
        </el-table>
        <el-empty v-else-if="!historyLoading" description="暂无推送历史" />
      </div>
      <template #footer><el-button @click="historyOpen = false">关闭</el-button></template>
    </el-dialog>
    <el-dialog v-model="verificationVisible" title="完成小红书安全验证" width="min(94vw, 760px)" :close-on-click-modal="false" append-to-body class="xhs-verification-dialog">
      <div class="xhs-verification"><p>请使用已登录当前账号的小红书 App 扫描二维码。验证完成后会自动继续推送。</p><div class="xhs-verification__image"><el-image v-if="verificationImage" :src="verificationImage" :preview-src-list="[verificationImage]" preview-teleported hide-on-click-modal fit="contain" alt="小红书安全验证二维码" /><span v-else>正在获取验证二维码...</span></div></div>
    </el-dialog>
  </div>
</template>
