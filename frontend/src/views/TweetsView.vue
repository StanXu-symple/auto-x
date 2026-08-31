<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
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
} from 'lucide-vue-next'
import { aiApi, monitoredUsersApi, tweetsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { MonitoredUser, Tweet } from '@/types'
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
const filters = reactive({
  page: 1,
  page_size: 15,
  search: '',
  monitored_user_id: '',
  date_from: '',
  date_to: '',
})

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
    .map((item) => ({ type: String(item.type || 'media'), url: String(item.url || item.preview_image_url || '') }))
}

function changePage(page: number) {
  filters.page = page
  load()
  window.scrollTo({ top: 0, behavior: 'smooth' })
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
    <section class="panel data-panel">
      <header class="data-toolbar data-toolbar--tweets">
        <el-input v-model="filters.search" class="element-search element-search--large" placeholder="搜索推文正文、用户名或关键词…" clearable><template #prefix><Search :size="16" /></template></el-input>
        <el-button :type="filtersOpen ? 'primary' : ''" plain @click="filtersOpen = !filtersOpen"><Filter :size="16" />筛选条件</el-button>
        <el-tooltip content="刷新内容流"><el-button circle :loading="loading" @click="load"><RefreshCw v-if="!loading" :size="16" /></el-button></el-tooltip>
      </header>

      <Transition name="filter-panel">
        <div v-if="filtersOpen" class="filter-panel">
          <label class="field field--compact"><span class="field__label">监听账号</span><el-select v-model="filters.monitored_user_id" placeholder="全部账号" clearable><el-option v-for="account in accounts" :key="account.id" :label="`@${account.username}`" :value="String(account.id)" /></el-select></label>
          <label class="field field--compact"><span class="field__label">起始日期</span><el-date-picker v-model="filters.date_from" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" :prefix-icon="CalendarDays" /></label>
          <label class="field field--compact"><span class="field__label">结束日期</span><el-date-picker v-model="filters.date_to" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" :prefix-icon="CalendarDays" /></label>
          <el-button class="filter-panel__clear" @click="clearFilters">清空筛选</el-button>
        </div>
      </Transition>

      <div class="content-result-bar">
        <span>共找到 <strong>{{ total }}</strong> 条内容</span>
        <span v-if="filters.search">关键词 “{{ filters.search }}”</span>
      </div>

      <div v-if="error && !tweets.length" class="error-panel error-panel--embedded">
        <AlertCircle :size="21" /><div><strong>内容流加载失败</strong><span>{{ error }}</span></div><button class="button button--secondary" @click="load">重试</button>
      </div>

      <div v-if="loading && !tweets.length" class="tweet-list">
        <div v-for="index in 5" :key="index" class="tweet-card tweet-card--skeleton"><span class="skeleton-avatar" /><div><i /><i /><i /></div></div>
      </div>

      <div v-else-if="tweets.length" class="tweet-list">
        <article v-for="tweet in tweets" :key="tweet.id" class="tweet-card">
          <span class="avatar avatar--tweet">{{ (tweet.username || 'X').slice(0, 1).toUpperCase() }}</span>
          <div class="tweet-card__body">
            <header>
              <div><strong>@{{ tweet.username || '未知用户' }}</strong><span>{{ tweet.lang || '语言未知' }}</span><i>·</i><time :title="formatDateTime(tweetTime(tweet))">{{ formatRelative(tweetTime(tweet)) }}</time></div>
              <a :href="tweetUrl(tweet)" target="_blank" rel="noopener noreferrer" class="icon-button" aria-label="在 X 查看"><ExternalLink :size="16" /></a>
            </header>
            <div v-if="tweetKind(tweet) !== 'original'" class="tweet-card__type"><Repeat2 v-if="tweetKind(tweet) === 'retweet'" :size="14" /><MessageCircle v-else :size="14" />{{ tweetKind(tweet) === 'retweet' ? '转推' : '回复' }}</div>
            <p class="tweet-card__text">{{ tweet.text }}</p>
            <div v-if="mediaFor(tweet).length" class="tweet-media" :class="{ 'tweet-media--grid': mediaFor(tweet).length > 1 }">
              <template v-for="(media, index) in mediaFor(tweet).slice(0, 4)" :key="index">
                <img v-if="media.url" :src="media.url" :alt="`推文媒体 ${index + 1}`" loading="lazy" />
                <span v-else><Image :size="22" />{{ media.type }}</span>
              </template>
            </div>
            <footer>
              <span><MessageCircle :size="15" />{{ formatNumber(tweet.reply_count) }}</span>
              <span><Repeat2 :size="15" />{{ formatNumber(tweet.retweet_count) }}</span>
              <span><Heart :size="15" />{{ formatNumber(tweet.like_count) }}</span>
              <el-button class="tweet-ai-button" type="primary" link :loading="generatingTweetId === String(tweet.id)" @click="generateWithAi(tweet)"><Sparkles v-if="generatingTweetId !== String(tweet.id)" :size="14" />AI 生成</el-button>
              <span class="tweet-card__id">ID {{ tweet.tweet_id }}</span>
            </footer>
          </div>
        </article>
      </div>

      <EmptyState v-else-if="!loading" title="没有找到相关内容" description="新推文会在下一次轮询完成后出现在这里">
        <template #icon><MessageSquareText :size="26" /></template>
        <el-button v-if="filters.search || filters.monitored_user_id || filters.date_from || filters.date_to" @click="filters.search = ''; clearFilters()">清除搜索和筛选</el-button>
      </EmptyState>

      <PaginationBar v-if="total > filters.page_size" :page="filters.page" :page-size="filters.page_size" :total="total" @change="changePage" />
    </section>
  </div>
</template>

<style scoped>
.tweet-ai-button { margin-left: 2px; }
</style>
