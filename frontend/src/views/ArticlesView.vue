<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessageBox } from 'element-plus'
import {
  Bot,
  Edit3,
  FilePlus2,
  FileText,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  UserRound,
} from 'lucide-vue-next'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'
import { articlesApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { Article, ArticlePayload, ArticleSource, AiDraftStatus } from '@/types'
import { formatDateTime } from '@/utils/format'

const ui = useUiStore()
const articles = ref<Article[]>([])
const total = ref(0)
const loading = ref(true)
const saving = ref(false)
const error = ref('')
const dialogError = ref('')
const dialogOpen = ref(false)
const editingArticle = ref<Article | null>(null)

const filters = reactive({ keyword: '', article_source: 'all', status: 'all' })
const appliedFilters = reactive({ keyword: '', article_source: 'all', status: 'all' })
const pagination = reactive({ page: 1, page_size: 15 })
const form = reactive<ArticlePayload>({ title: '', content: '', excerpt: '', status: 'draft' })

const currentAiCount = computed(() => articles.value.filter((article) => article.article_source === 'ai').length)
const currentUserCount = computed(() => articles.value.filter((article) => article.article_source === 'user').length)
const hasFilters = computed(() => Boolean(filters.keyword.trim() || filters.article_source !== 'all' || filters.status !== 'all'))

const sourceMeta: Record<ArticleSource, { label: string; icon: typeof Bot; type: 'primary' | 'success' }> = {
  ai: { label: 'AI 生成', icon: Bot, type: 'primary' },
  user: { label: '用户生成', icon: UserRound, type: 'success' },
}

const statusMeta: Record<AiDraftStatus, { label: string; type: 'info' | 'success' | 'danger' }> = {
  draft: { label: '草稿', type: 'info' },
  approved: { label: '已通过', type: 'success' },
  rejected: { label: '已拒绝', type: 'danger' },
}

async function loadArticles() {
  loading.value = true
  error.value = ''
  try {
    const result = await articlesApi.list({
      page: pagination.page,
      page_size: pagination.page_size,
      keyword: appliedFilters.keyword || undefined,
      article_source: appliedFilters.article_source === 'all' ? undefined : appliedFilters.article_source as ArticleSource,
      status: appliedFilters.status === 'all' ? undefined : appliedFilters.status as AiDraftStatus,
    })
    articles.value = result.items
    total.value = result.total
  } catch (requestError) {
    error.value = getErrorMessage(requestError, '文章列表加载失败')
  } finally {
    loading.value = false
  }
}

function runQuery() {
  appliedFilters.keyword = filters.keyword.trim()
  appliedFilters.article_source = filters.article_source
  appliedFilters.status = filters.status
  pagination.page = 1
  loadArticles()
}

function resetQuery() {
  filters.keyword = ''
  filters.article_source = 'all'
  filters.status = 'all'
  runQuery()
}

function changePage(page: number) {
  pagination.page = page
  loadArticles()
}

function openCreate() {
  editingArticle.value = null
  form.title = ''
  form.content = ''
  form.excerpt = ''
  form.status = 'draft'
  dialogError.value = ''
  dialogOpen.value = true
}

function openEdit(article: Article) {
  editingArticle.value = article
  form.title = article.title
  form.content = article.content
  form.excerpt = article.excerpt || ''
  form.status = article.status
  dialogError.value = ''
  dialogOpen.value = true
}

async function saveArticle() {
  dialogError.value = ''
  if (!form.title.trim() || !form.content.trim()) {
    dialogError.value = '请填写文章标题和正文'
    return
  }
  saving.value = true
  try {
    const payload: ArticlePayload = {
      title: form.title.trim(),
      content: form.content.trim(),
      excerpt: form.excerpt?.trim() || null,
      status: form.status,
    }
    if (editingArticle.value) {
      await articlesApi.update(editingArticle.value.id, {
        ...payload,
        revision: editingArticle.value.revision,
      })
    } else {
      await articlesApi.create(payload)
    }
    dialogOpen.value = false
    pagination.page = 1
    await loadArticles()
    ui.toast(editingArticle.value ? '文章已更新' : '文章已创建', 'success')
  } catch (requestError) {
    dialogError.value = getErrorMessage(requestError, '文章保存失败')
  } finally {
    saving.value = false
  }
}

async function removeArticle(article: Article) {
  try {
    await ElMessageBox.confirm(`删除文章「${article.title}」？此操作无法撤销。`, '删除文章', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    await articlesApi.remove(article.id)
    if (articles.value.length === 1 && pagination.page > 1) pagination.page -= 1
    await loadArticles()
    ui.toast('文章已删除', 'success')
  } catch (requestError) {
    ui.toast('删除文章失败', 'error', getErrorMessage(requestError))
  }
}

function articlePreview(article: Article) {
  return article.excerpt || article.content
}

onMounted(loadArticles)
</script>

<template>
  <div class="articles-page page-stack">
    <section class="articles-hero">
      <div class="articles-hero__copy">
        <span class="articles-hero__icon"><FileText :size="24" /></span>
        <div><span class="eyebrow">CONTENT LIBRARY</span><h2>文章管理</h2><p>在一个列表中维护 AI 草稿与手动创建的文章。</p></div>
      </div>
      <div class="articles-hero__metrics">
        <span><small>文章总数</small><strong>{{ total }}</strong></span>
        <span><small>当前页 AI 生成</small><strong>{{ currentAiCount }}</strong></span>
        <span><small>当前页用户生成</small><strong>{{ currentUserCount }}</strong></span>
      </div>
      <el-button type="primary" @click="openCreate"><Plus :size="16" />新增文章</el-button>
    </section>

    <section class="articles-panel">
      <header class="articles-toolbar">
        <div><h3>文章库</h3><p>按标题、正文、来源或状态定位文章</p></div>
        <el-tooltip content="刷新文章列表"><el-button circle :loading="loading" aria-label="刷新文章列表" @click="loadArticles"><RefreshCw v-if="!loading" :size="16" /></el-button></el-tooltip>
      </header>

      <div class="articles-query">
        <label class="articles-query__keyword"><span>关键词</span><el-input v-model="filters.keyword" clearable placeholder="搜索标题、摘要或正文" @keyup.enter="runQuery"><template #prefix><Search :size="15" /></template></el-input></label>
        <label><span>文章来源</span><el-select v-model="filters.article_source"><el-option label="全部来源" value="all" /><el-option label="AI 生成" value="ai" /><el-option label="用户生成" value="user" /></el-select></label>
        <label><span>文章状态</span><el-select v-model="filters.status"><el-option label="全部状态" value="all" /><el-option label="草稿" value="draft" /><el-option label="已通过" value="approved" /><el-option label="已拒绝" value="rejected" /></el-select></label>
        <div class="articles-query__actions"><el-button :disabled="!hasFilters && !appliedFilters.keyword && appliedFilters.article_source === 'all' && appliedFilters.status === 'all'" @click="resetQuery">重置</el-button><el-button type="primary" :loading="loading" @click="runQuery"><Search v-if="!loading" :size="15" />查询</el-button></div>
      </div>

      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-table v-loading="loading" :data="articles" row-key="id" class="article-table">
        <el-table-column label="文章" min-width="340">
          <template #default="{ row }"><div class="article-title-cell"><span><FileText :size="17" /></span><div><strong>{{ row.title }}</strong><p>{{ articlePreview(row) }}</p></div></div></template>
        </el-table-column>
        <el-table-column label="文章来源" width="132">
          <template #default="{ row }"><el-tag :type="sourceMeta[row.article_source as ArticleSource].type" effect="plain"><component :is="sourceMeta[row.article_source as ArticleSource].icon" :size="13" />{{ sourceMeta[row.article_source as ArticleSource].label }}</el-tag></template>
        </el-table-column>
        <el-table-column label="状态" width="105"><template #default="{ row }"><el-tag :type="statusMeta[row.status as AiDraftStatus].type" effect="plain">{{ statusMeta[row.status as AiDraftStatus].label }}</el-tag></template></el-table-column>
        <el-table-column label="关联记录" width="135"><template #default="{ row }"><span class="article-relation">{{ row.job_id ? `AI 任务 #${row.job_id}` : '手动创建' }}</span></template></el-table-column>
        <el-table-column label="更新时间" width="175"><template #default="{ row }"><span class="article-date"><strong>{{ formatDateTime(row.updated_at) }}</strong><small>v{{ row.revision }}</small></span></template></el-table-column>
        <el-table-column label="操作" width="154" fixed="right"><template #default="{ row }"><div class="article-actions"><el-button size="small" @click="openEdit(row)"><Edit3 :size="14" />编辑</el-button><el-tooltip content="删除文章"><el-button circle size="small" type="danger" plain aria-label="删除文章" @click="removeArticle(row)"><Trash2 :size="14" /></el-button></el-tooltip></div></template></el-table-column>
        <template #empty><EmptyState compact title="暂无文章" description="调整查询条件，或创建第一篇文章"><template #icon><FilePlus2 :size="25" /></template><el-button type="primary" @click="openCreate">新增文章</el-button></EmptyState></template>
      </el-table>
      <PaginationBar v-if="total > pagination.page_size" :page="pagination.page" :page-size="pagination.page_size" :total="total" @change="changePage" />
    </section>

    <el-dialog v-model="dialogOpen" class="article-dialog" :title="editingArticle ? '编辑文章' : '新增文章'" width="min(760px, 94vw)" destroy-on-close>
      <el-alert v-if="dialogError" :title="dialogError" type="error" :closable="false" show-icon />
      <div v-if="editingArticle" class="article-dialog__source"><span>文章来源</span><el-tag :type="sourceMeta[editingArticle.article_source].type" effect="plain"><component :is="sourceMeta[editingArticle.article_source].icon" :size="13" />{{ sourceMeta[editingArticle.article_source].label }}</el-tag></div>
      <el-form label-position="top" class="article-form">
        <el-form-item label="文章标题" required><el-input v-model="form.title" maxlength="300" show-word-limit placeholder="输入文章标题" /></el-form-item>
        <el-form-item label="文章摘要"><el-input v-model="form.excerpt" type="textarea" :rows="2" maxlength="1000" show-word-limit placeholder="可选，用于列表和发布摘要" /></el-form-item>
        <el-form-item label="文章正文" required><el-input v-model="form.content" type="textarea" :rows="12" maxlength="50000" show-word-limit placeholder="输入文章正文" /></el-form-item>
        <el-form-item label="文章状态"><el-select v-model="form.status"><el-option label="草稿" value="draft" /><el-option label="已通过" value="approved" /><el-option label="已拒绝" value="rejected" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialogOpen = false">取消</el-button><el-button type="primary" :loading="saving" @click="saveArticle">{{ editingArticle ? '保存修改' : '创建文章' }}</el-button></template>
    </el-dialog>
  </div>
</template>
