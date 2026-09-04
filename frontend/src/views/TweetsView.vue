<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useDebounceFn } from '@vueuse/core'
import { useRouter } from 'vue-router'
import {
  AlertCircle,
  CalendarDays,
  ExternalLink,
  Filter,
  Heart,
  Image,
  MessageCircle,
  MessageSquareText,
  RefreshCw,
  Repeat2,
  Search,
  Sparkles,
  Send,
  Play,
} from 'lucide-vue-next'
import { aiApi, monitoredUsersApi, qqApi, tweetsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { MonitoredUser, Tweet } from '@/types'
import type { QQBotAccount, QQJoinedGroup } from '@/types'
import { formatDateTime, formatNumber, formatRelative, tweetTime } from '@/utils/format'
import EmptyState from '@/components/EmptyState.vue'
import PaginationBar from '@/components/PaginationBar.vue'

const tweets = ref<Tweet[]>([])
const router = useRouter()
const ui = useUiStore()
const accounts = ref<MonitoredUser[]>([])
const total = ref(0)
const loading = ref(true)
const error = ref('')
const filtersOpen = ref(false)
const generatingTweetId = ref<string | null>(null)
const selectedTweetIds = ref<number[]>([])
const qqDialogOpen = ref(false)
const qqSending = ref(false)
const qqBots = ref<QQBotAccount[]>([])
const qqGroups = ref<QQJoinedGroup[]>([])
const qqBotId = ref<number | null>(null)
const qqGroupOpenids = ref<string[]>([])
const filters = reactive({
  page: 1,
  page_size: 15,
  search: '',
  monitored_user_id: '',
  date_from: '',
  date_to: '',
})

const hasFilters = computed(() => Boolean(
  filters.search.trim() || filters.monitored_user_id || filters.date_from || filters.date_to,
))
const activeFilterCount = computed(() => [
  filters.monitored_user_id,
  filters.date_from,
  filters.date_to,
].filter(Boolean).length)

async function load() {
  loading.value = true
  error.value = ''
  try {
    const result = await tweetsApi.list({
      page: filters.page,
      page_size: filters.page_size,
      search: filters.search.trim() || undefined,
      monitored_user_id: filters.monitored_user_id || undefined,
      posted_after: filters.date_from ? `${filters.date_from}T00:00:00Z` : undefined,
      posted_before: filters.date_to ? `${filters.date_to}T23:59:59Z` : undefined,
    })
    tweets.value = result.items
    total.value = result.total
  } catch (requestError) {
    error.value = getErrorMessage(requestError, '无法加载内容流')
  } finally {
    loading.value = false
  }
}

const debouncedSearch = useDebounceFn(() => {
  filters.page = 1
  load()
}, 350)

watch(() => filters.search, debouncedSearch)
watch(
  () => [filters.monitored_user_id, filters.date_from, filters.date_to],
  () => {
    filters.page = 1
    load()
  },
)

function clearFilters() {
  filters.monitored_user_id = ''
  filters.date_from = ''
  filters.date_to = ''
}

function tweetKind(tweet: Tweet) {
  const types = (tweet.referenced_tweets || []).map((reference) => String(reference.type || ''))
  if (types.includes('retweeted')) return 'retweet'
  if (types.includes('replied_to')) return 'reply'
  return 'original'
}

function tweetUrl(tweet: Tweet) {
  return `https://x.com/${encodeURIComponent(tweet.username)}/status/${encodeURIComponent(tweet.tweet_id)}`
}

function mediaFor(tweet: Tweet) {
  const media = tweet.attachments?.media
  if (!Array.isArray(media)) return []
  return media
    .map((item) => item as Record<string, unknown>)
    .map((item) => ({
      type: String(item.type || 'media'),
      url: String(item.url || ''),
      poster: String(item.preview_image_url || ''),
    }))
}

function changePage(page: number) {
  filters.page = page
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
}

async function openQqPush() {
  try {
    qqBots.value = await qqApi.bots()
    qqBotId.value = qqBots.value.find((bot) => bot.is_enabled)?.id || null
    qqGroupOpenids.value = []
    qqGroups.value = qqBotId.value ? await qqApi.joinedGroups(qqBotId.value) : []
    qqDialogOpen.value = true
  } catch (requestError) { ui.toast('无法读取 QQ 账号', 'error', getErrorMessage(requestError)) }
}

async function changeQqBot(id: number) {
  qqGroupOpenids.value = []
  qqGroups.value = await qqApi.joinedGroups(id)
}

async function sendQqBatch() {
  if (!qqBotId.value || !qqGroupOpenids.value.length) return
  qqSending.value = true
  try {
    const result = await qqApi.batchPush({ bot_id: qqBotId.value, group_openids: qqGroupOpenids.value, tweet_ids: selectedTweetIds.value })
    ui.toast('QQ 推送已提交', 'success', result.message)
    qqDialogOpen.value = false
    selectedTweetIds.value = []
  } catch (requestError) { ui.toast('QQ 推送失败', 'error', getErrorMessage(requestError)) }
  finally { qqSending.value = false }
}

async function generateWithAi(tweet: Tweet) {
  generatingTweetId.value = String(tweet.id)
  try {
    const result = await aiApi.generateFromTweet(tweet.tweet_id, {
      idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${tweet.tweet_id}`,
    })
    ui.toast('AI 创作任务已提交', 'success', `正在基于 @${tweet.username} 的内容生成草稿`)
    await router.push({
      path: '/ai-writing',
      query: result.job_id != null || result.id != null ? { job: String(result.job_id ?? result.id) } : undefined,
    })
  } catch (requestError) {
    ui.toast('无法创建 AI 任务', 'error', getErrorMessage(requestError))
  } finally {
    generatingTweetId.value = null
  }
}

onMounted(async () => {
  load()
  try {
    accounts.value = (await monitoredUsersApi.list({ page: 1, page_size: 100 })).items
  } catch {
    accounts.value = []
  }
})
</script>

<template>
  <div class="tweets-view page-stack">
    <section class="summary-strip">
      <div class="summary-strip__metric"><span class="summary-strip__icon"><MessageSquareText :size="18" /></span><span><small>内容总数</small><strong>{{ total }}</strong></span></div>
      <i />
      <div class="summary-strip__metric"><span class="summary-strip__icon is-neutral"><CalendarDays :size="18" /></span><span><small>当前页</small><strong>{{ tweets.length }}</strong></span></div>
      <i />
      <div class="summary-strip__metric"><span class="summary-strip__icon is-success"><Send :size="18" /></span><span><small>已选择</small><strong>{{ selectedTweetIds.length }}</strong></span></div>
    </section>

    <section class="panel data-panel">
      <header class="data-toolbar data-toolbar--tweets">
        <div class="toolbar-heading"><strong>采集内容</strong><span>检索、筛选并将内容送往后续创作或推送流程</span></div>
        <div class="toolbar-controls">
          <el-input v-model="filters.search" class="element-search element-search--large" placeholder="搜索正文、用户名或关键词" clearable><template #prefix><Search :size="16" /></template></el-input>
          <el-button class="filter-trigger" :type="filtersOpen ? 'primary' : ''" plain @click="filtersOpen = !filtersOpen"><Filter :size="16" />筛选<span v-if="activeFilterCount" class="filter-trigger__count">{{ activeFilterCount }}</span></el-button>
          <el-tooltip content="刷新内容流"><el-button circle :loading="loading" @click="load"><RefreshCw v-if="!loading" :size="16" /></el-button></el-tooltip>
        </div>
      </header>

      <Transition name="filter-panel">
        <div v-if="filtersOpen" class="filter-panel">
          <label class="field field--compact"><span class="field__label">监听账号</span><el-select v-model="filters.monitored_user_id" placeholder="全部账号" clearable><el-option v-for="account in accounts" :key="account.id" :label="`@${account.username}`" :value="String(account.id)" /></el-select></label>
          <label class="field field--compact"><span class="field__label">起始日期</span><el-date-picker v-model="filters.date_from" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" :prefix-icon="CalendarDays" /></label>
          <label class="field field--compact"><span class="field__label">结束日期</span><el-date-picker v-model="filters.date_to" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" :prefix-icon="CalendarDays" /></label>
          <el-button class="filter-panel__clear" :disabled="!activeFilterCount" @click="clearFilters">清空筛选</el-button>
        </div>
      </Transition>

      <div class="content-result-bar" aria-live="polite">
        <span class="content-result-bar__count">共找到 <strong>{{ total }}</strong> 条内容</span>
        <span v-if="filters.search" class="content-result-bar__query">关键词 “{{ filters.search }}”</span>
      </div>

      <div v-if="error && !tweets.length" class="error-panel error-panel--embedded">
        <AlertCircle :size="21" /><div><strong>内容流加载失败</strong><span>{{ error }}</span></div><button class="button button--secondary" @click="load">重试</button>
      </div>

      <div v-if="loading && !tweets.length" class="tweet-list">
        <div v-for="index in 5" :key="index" class="tweet-card tweet-card--skeleton"><span class="skeleton-avatar" /><div><i /><i /><i /></div></div>
      </div>

      <div v-else-if="tweets.length" class="tweet-list">
        <article v-for="tweet in tweets" :key="tweet.id" class="tweet-card" :class="{ 'tweet-card--selected': selectedTweetIds.includes(Number(tweet.id)) }">
          <div class="tweet-select"><el-checkbox v-model="selectedTweetIds" :value="Number(tweet.id)" :aria-label="`选择 @${tweet.username || '未知用户'} 的内容`" /></div>
          <span class="avatar avatar--tweet">{{ (tweet.username || 'X').slice(0, 1).toUpperCase() }}</span>
          <div class="tweet-card__body">
            <header class="tweet-card__header">
              <div class="tweet-card__identity"><strong>@{{ tweet.username || '未知用户' }}</strong><span>{{ tweet.lang || '语言未知' }}</span><i class="meta-separator" /><time :title="formatDateTime(tweetTime(tweet))">{{ formatRelative(tweetTime(tweet)) }}</time></div>
              <a :href="tweetUrl(tweet)" target="_blank" rel="noopener noreferrer" class="icon-button" aria-label="在 X 查看"><ExternalLink :size="16" /></a>
            </header>
            <div v-if="tweetKind(tweet) !== 'original'" class="tweet-card__type"><Repeat2 v-if="tweetKind(tweet) === 'retweet'" :size="14" /><MessageCircle v-else :size="14" />{{ tweetKind(tweet) === 'retweet' ? '转推' : '回复' }}</div>
            <p class="tweet-card__text">{{ tweet.text }}</p>
            <div v-if="mediaFor(tweet).length" class="tweet-media" :class="{ 'tweet-media--grid': mediaFor(tweet).length > 1 }">
              <template v-for="(media, index) in mediaFor(tweet).slice(0, 4)" :key="index">
                <a v-if="media.type === 'video' && media.poster" :href="media.url || media.poster" target="_blank" rel="noopener noreferrer" class="tweet-media__video-poster"><img :src="media.poster" :alt="`推文视频 ${index + 1}`" loading="lazy" referrerpolicy="no-referrer" /><span><Play :size="20" fill="currentColor" />视频</span></a>
                <img v-else-if="media.url && media.type !== 'video'" :src="media.url" :alt="`推文媒体 ${index + 1}`" loading="lazy" referrerpolicy="no-referrer" />
                <span v-else><Image :size="22" />{{ media.type }}</span>
              </template>
            </div>
            <footer class="tweet-card__footer">
              <div class="tweet-card__engagement" aria-label="互动数据">
                <span title="回复"><MessageCircle :size="15" />{{ formatNumber(tweet.reply_count) }}</span>
                <span title="转推"><Repeat2 :size="15" />{{ formatNumber(tweet.retweet_count) }}</span>
                <span title="喜欢"><Heart :size="15" />{{ formatNumber(tweet.like_count) }}</span>
              </div>
              <div class="tweet-card__actions">
                <el-button class="tweet-ai-button" type="primary" link :loading="generatingTweetId === String(tweet.id)" @click="generateWithAi(tweet)"><Sparkles v-if="generatingTweetId !== String(tweet.id)" :size="14" />AI 生成</el-button>
                <span class="tweet-card__id">ID {{ tweet.tweet_id }}</span>
              </div>
            </footer>
          </div>
        </article>
      </div>

      <EmptyState v-else-if="!loading" title="没有找到相关内容" description="新推文会在下一次轮询完成后出现在这里">
        <template #icon><MessageSquareText :size="26" /></template>
        <el-button v-if="hasFilters" @click="filters.search = ''; clearFilters()">清除搜索和筛选</el-button>
      </EmptyState>

      <PaginationBar v-if="total > filters.page_size" :page="filters.page" :page-size="filters.page_size" :total="total" @change="changePage" />
      <div v-if="selectedTweetIds.length" class="batch-toolbar"><span>已选择 {{ selectedTweetIds.length }} 条</span><el-button type="primary" @click="openQqPush"><Send :size="15" />QQ 批量推送</el-button></div>
    </section>
    <el-dialog v-model="qqDialogOpen" title="发送到 QQ 群" width="460px">
      <el-form label-position="top">
        <el-form-item label="QQ 机器人账号"><el-select v-model="qqBotId" placeholder="选择机器人" @change="changeQqBot"><el-option v-for="bot in qqBots.filter((item) => item.is_enabled)" :key="bot.id" :label="bot.name" :value="bot.id" /></el-select></el-form-item>
        <el-form-item label="发送到群"><el-select v-model="qqGroupOpenids" multiple collapse-tags placeholder="选择已加入的群"><el-option v-for="group in qqGroups" :key="group.group_openid" :label="group.name || group.group_openid" :value="group.group_openid" /></el-select></el-form-item>
      </el-form>
      <template #footer><el-button @click="qqDialogOpen = false">取消</el-button><el-button type="primary" :loading="qqSending" :disabled="!qqBotId || !qqGroupOpenids.length" @click="sendQqBatch">确认发送</el-button></template>
    </el-dialog>
  </div>
</template>
