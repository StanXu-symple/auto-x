<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import {
  ExportOutlined as ExternalLinkOutlined,
  ExperimentOutlined as SparklesOutlined,
  EyeOutlined,
  FilterOutlined,
  ReloadOutlined,
  SendOutlined,
} from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { aiApi, monitoredUsersApi, qqApi, tweetsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type {
  MonitoredUser,
  QQBotAccount,
  QQJoinedGroup,
  Tweet,
  TweetScreenshot,
  TweetType,
} from '@/types'
import { formatDateTime, formatNumber, formatRelative, tweetTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
const tweets = ref<Tweet[]>([])
const accounts = ref<MonitoredUser[]>([])
const total = ref(0)
const loading = ref(false)
const selected = ref<number[]>([])
const generating = ref<string | null>(null)
const pushOpen = ref(false)
const sending = ref(false)
const bots = ref<QQBotAccount[]>([])
const groups = ref<QQJoinedGroup[]>([])
const pushForm = reactive({ bot_id: null as number | null, group_openids: [] as string[] })
const filters = reactive({
  page: 1,
  page_size: 15,
  search: '',
  monitored_user_id: '',
  date_from: '',
  date_to: '',
  tweet_type: undefined as TweetType | undefined,
})
const tweetTypeOptions = [
  { label: '原创', value: 'original' },
  { label: '回复', value: 'reply' },
  { label: '转推（含引用）', value: 'retweet' },
]
function tweetTypeLabel(type: TweetType) {
  return tweetTypeOptions.find((option) => option.value === type)?.label || '—'
}
const detailOpen = ref(false)
const detailLoading = ref(false)
const detail = ref<Tweet | null>(null)
const detailError = ref('')
const screenshotUrl = ref('')
const screenshotError = ref('')
const screenshotLoading = ref(false)
const screenshotQueuing = ref(false)
const screenshotLabels = {
  pending: '等待截图',
  running: '截图中',
  succeeded: '已保存',
  failed: '截图失败',
}
function screenshotLabel(item?: TweetScreenshot | null) {
  return item ? screenshotLabels[item.status] : '尚未截图'
}
function releaseScreenshot() {
  if (screenshotUrl.value) URL.revokeObjectURL(screenshotUrl.value)
  screenshotUrl.value = ''
}
let detailRequest = 0
async function loadScreenshot(tweet: Tweet, request: number) {
  if (tweet.screenshot?.status !== 'succeeded') return
  screenshotLoading.value = true
  try {
    const blob = await tweetsApi.screenshot(tweet.tweet_id)
    if (request === detailRequest && detailOpen.value)
      screenshotUrl.value = URL.createObjectURL(blob)
  } catch (e) {
    if (request === detailRequest) screenshotError.value = getErrorMessage(e, '读取截图失败')
  } finally {
    if (request === detailRequest) screenshotLoading.value = false
  }
}
async function showDetail(tweet: Tweet) {
  const request = ++detailRequest
  detailOpen.value = true
  detailLoading.value = true
  detail.value = null
  detailError.value = ''
  screenshotError.value = ''
  screenshotLoading.value = false
  releaseScreenshot()
  try {
    const result = await tweetsApi.detail(tweet.tweet_id)
    if (request === detailRequest) {
      detail.value = result
      void loadScreenshot(result, request)
    }
  } catch (e) {
    if (request === detailRequest)
      detailError.value = getErrorMessage(e, '读取内容详情失败，请关闭后重试')
  } finally {
    if (request === detailRequest) detailLoading.value = false
  }
}
async function queueScreenshot() {
  if (!detail.value) return
  const tweet = detail.value
  screenshotQueuing.value = true
  try {
    await tweetsApi.captureScreenshot(tweet.tweet_id)
    message.success('截图任务已提交')
    if (detail.value?.tweet_id === tweet.tweet_id) await showDetail(tweet)
    void load()
  } catch (e) {
    message.error(getErrorMessage(e, '无法提交截图任务'))
  } finally {
    screenshotQueuing.value = false
  }
}
watch(detailOpen, (open) => {
  if (!open) {
    detailRequest++
    releaseScreenshot()
  }
})
onUnmounted(() => {
  detailRequest++
  releaseScreenshot()
})
const hasFilters = computed(() =>
  Boolean(
    filters.search ||
    filters.monitored_user_id ||
    filters.date_from ||
    filters.date_to ||
    filters.tweet_type,
  ),
)
async function load() {
  loading.value = true
  try {
    const result = await tweetsApi.list({
      page: filters.page,
      page_size: filters.page_size,
      tweet_type: filters.tweet_type,
      search: filters.search || undefined,
      monitored_user_id: filters.monitored_user_id || undefined,
      posted_after: filters.date_from ? `${filters.date_from}T00:00:00Z` : undefined,
      posted_before: filters.date_to ? `${filters.date_to}T23:59:59Z` : undefined,
    })
    tweets.value = result.items
    total.value = result.total
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载内容流'))
  } finally {
    loading.value = false
  }
}
function clear() {
  filters.search = ''
  filters.monitored_user_id = ''
  filters.date_from = ''
  filters.date_to = ''
  filters.tweet_type = undefined
  filters.page = 1
  void load()
}
async function openPush() {
  try {
    bots.value = await qqApi.bots()
    pushForm.bot_id = bots.value.find((bot) => bot.is_enabled)?.id || null
    groups.value = pushForm.bot_id ? await qqApi.joinedGroups(pushForm.bot_id) : []
    pushForm.group_openids = []
    pushOpen.value = true
  } catch (e) {
    message.error(getErrorMessage(e, '无法读取 QQ 账号'))
  }
}
async function changeBot() {
  pushForm.group_openids = []
  groups.value = pushForm.bot_id ? await qqApi.joinedGroups(pushForm.bot_id) : []
}
async function sendBatch() {
  if (!pushForm.bot_id || !pushForm.group_openids.length)
    return message.warning('请选择机器人和发送群')
  sending.value = true
  try {
    const result = await qqApi.batchPush({
      bot_id: pushForm.bot_id,
      group_openids: pushForm.group_openids,
      tweet_ids: selected.value,
    })
    message.success(result.message)
    pushOpen.value = false
    selected.value = []
  } catch (e) {
    message.error(getErrorMessage(e, 'QQ 推送失败'))
  } finally {
    sending.value = false
  }
}
async function generate(tweet: Tweet) {
  generating.value = String(tweet.id)
  try {
    await aiApi.generateFromTweet(tweet.tweet_id, {
      idempotency_key: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${tweet.tweet_id}`,
    })
    message.success('AI 创作任务已提交')
  } catch (e) {
    message.error(getErrorMessage(e, '无法创建 AI 任务'))
  } finally {
    generating.value = null
  }
}
onMounted(async () => {
  void load()
  try {
    accounts.value = (await monitoredUsersApi.list({ page: 1, page_size: 100 })).items
  } catch {}
})
watch(
  () => [filters.monitored_user_id, filters.date_from, filters.date_to, filters.tweet_type],
  () => {
    filters.page = 1
    void load()
  },
)
</script>

<template>
  <div class="page-stack">
    <PageHeader
      eyebrow="MONITOR / 02"
      title="内容流"
      description="检索采集内容，并将内容送往创作或推送流程"
      ><template #actions
        ><a-button :disabled="!selected.length" type="primary" @click="openPush"
          ><SendOutlined /> QQ 批量推送（{{ selected.length }}）</a-button
        ></template
      ></PageHeader
    ><a-card :bordered="false"
      ><div class="toolbar">
        <div class="toolbar__controls">
          <a-input
            v-model:value="filters.search"
            placeholder="搜索正文、用户名或关键词"
            allow-clear
            style="width: 250px"
            @press-enter="load"
          /><a-select
            v-model:value="filters.monitored_user_id"
            allow-clear
            placeholder="全部账号"
            style="width: 160px"
            :options="
              accounts.map((item) => ({
                label: item.display_name?.trim()
                  ? `${item.display_name.trim()} (@${item.username})`
                  : `@${item.username}`,
                value: String(item.id),
              }))
            "
          /><a-select
            v-model:value="filters.tweet_type"
            allow-clear
            placeholder="全部类型"
            :options="tweetTypeOptions"
            style="width: 150px"
          /><a-input v-model:value="filters.date_from" type="date" style="width: 140px" /><a-input
            v-model:value="filters.date_to"
            type="date"
            style="width: 140px"
          /><a-button @click="clear"><FilterOutlined /> 清空</a-button
          ><a-button @click="load"><ReloadOutlined /></a-button>
        </div>
        <span class="toolbar__hint"
          >{{ hasFilters ? '已应用筛选' : '全部采集内容' }} · 共 {{ total }} 条</span
        >
      </div>
      <a-table
        :data-source="tweets"
        :loading="loading"
        :row-selection="{
          selectedRowKeys: selected,
          onChange: (keys: any[]) => (selected = keys as number[]),
        }"
        :pagination="{
          current: filters.page,
          pageSize: filters.page_size,
          total,
          showSizeChanger: false,
        }"
        row-key="id"
        @change="
          (page: any) => {
            filters.page = page.current
            load()
          }
        "
        ><a-table-column title="内容" key="text" :width="420"
          ><template #default="{ record }"
            ><div>
              <div class="tweet-author">
                <strong>{{ record.display_name?.trim() || `@${record.username}` }}</strong
                ><span v-if="record.display_name?.trim()" class="tweet-author__handle"
                  >@{{ record.username }}</span
                >
              </div>
              <p class="tweet-preview">{{ record.text }}</p>
              <span class="muted"
                >{{ formatRelative(tweetTime(record)) }} · {{ record.lang || '—' }}</span
              >
            </div></template
          ></a-table-column
        ><a-table-column title="类型" :width="140"
          ><template #default="{ record }"
            ><a-tag
              :color="
                record.tweet_type === 'reply'
                  ? 'orange'
                  : record.tweet_type === 'retweet'
                    ? 'blue'
                    : 'green'
              "
              >{{ tweetTypeLabel(record.tweet_type) }}</a-tag
            ></template
          ></a-table-column
        ><a-table-column title="互动"
          ><template #default="{ record }"
            ><span class="mono"
              >赞 {{ formatNumber(record.like_count) }} · 转
              {{ formatNumber(record.retweet_count) }}</span
            ></template
          ></a-table-column
        ><a-table-column title="截图" :width="100">
          <template #default="{ record }">
            <a-tooltip :title="record.screenshot?.last_error">
              <a-tag
                :color="
                  record.screenshot?.status === 'succeeded'
                    ? 'green'
                    : record.screenshot?.status === 'failed'
                      ? 'red'
                      : undefined
                "
              >
                {{ screenshotLabel(record.screenshot) }}
              </a-tag>
            </a-tooltip>
          </template> </a-table-column
        ><a-table-column title="采集时间"
          ><template #default="{ record }"
            ><span class="muted">{{ formatDateTime(record.fetched_at) }}</span></template
          ></a-table-column
        ><a-table-column title="操作" width="230"
          ><template #default="{ record }"
            ><a-space
              ><a-tooltip title="查看详情"
                ><a-button
                  type="text"
                  size="small"
                  aria-label="查看内容详情"
                  @click="showDetail(record)"
                  ><EyeOutlined /></a-button></a-tooltip
              ><a-button
                type="link"
                size="small"
                :loading="generating === String(record.id)"
                @click="generate(record)"
                ><SparklesOutlined /> AI 生成</a-button
              ><a
                :href="`https://x.com/${record.username}/status/${record.tweet_id}`"
                target="_blank"
                rel="noreferrer"
                ><a-button type="text" size="small"
                  ><ExternalLinkOutlined /></a-button></a></a-space></template></a-table-column></a-table></a-card
    ><a-modal v-model:open="detailOpen" title="内容详情" :footer="null" :width="760">
      <a-spin :spinning="detailLoading">
        <div class="tweet-detail">
          <a-alert v-if="detailError" type="error" show-icon :message="detailError" />
          <template v-if="detail">
            <div class="tweet-author">
              <strong>{{ detail.display_name?.trim() || `@${detail.username}` }}</strong
              ><span v-if="detail.display_name?.trim()" class="tweet-author__handle"
                >@{{ detail.username }}</span
              >
            </div>
            <p class="tweet-detail__text">{{ detail.text }}</p>
            <a-descriptions bordered size="small" :column="{ xs: 1, sm: 2 }">
              <a-descriptions-item label="内容类型">{{
                tweetTypeLabel(detail.tweet_type)
              }}</a-descriptions-item
              ><a-descriptions-item label="发布时间">{{
                formatDateTime(detail.posted_at)
              }}</a-descriptions-item>
              <a-descriptions-item label="采集时间">{{
                formatDateTime(detail.fetched_at)
              }}</a-descriptions-item>
              <a-descriptions-item label="推文 ID">{{ detail.tweet_id }}</a-descriptions-item>
              <a-descriptions-item label="语言">{{ detail.lang || '—' }}</a-descriptions-item>
              <a-descriptions-item label="点赞">{{
                formatNumber(detail.like_count)
              }}</a-descriptions-item>
              <a-descriptions-item label="转推">{{
                formatNumber(detail.retweet_count)
              }}</a-descriptions-item>
              <a-descriptions-item label="回复">{{
                formatNumber(detail.reply_count)
              }}</a-descriptions-item>
              <a-descriptions-item label="引用">{{
                formatNumber(detail.quote_count)
              }}</a-descriptions-item>
              <a-descriptions-item label="收藏">{{
                formatNumber(detail.bookmark_count)
              }}</a-descriptions-item>
              <a-descriptions-item label="浏览">{{
                formatNumber(detail.impression_count)
              }}</a-descriptions-item>
            </a-descriptions>
            <section class="tweet-screenshot" aria-label="帖子截图">
              <a-space wrap>
                <strong>帖子截图</strong>
                <a-tag>{{ screenshotLabel(detail.screenshot) }}</a-tag>
                <span v-if="detail.screenshot?.captured_at" class="muted">{{
                  formatDateTime(detail.screenshot.captured_at)
                }}</span>
                <a-button size="small" :loading="detailLoading" @click="showDetail(detail)"
                  ><ReloadOutlined /> 刷新</a-button
                >
                <a-button
                  v-if="
                    !detail.screenshot || detail.screenshot.status === 'failed' || screenshotError
                  "
                  size="small"
                  :loading="screenshotQueuing"
                  @click="queueScreenshot"
                >
                  {{ detail.screenshot ? '重试截图' : '生成截图' }}
                </a-button>
                <a v-if="screenshotUrl" :href="screenshotUrl" :download="`${detail.tweet_id}.png`"
                  >下载 PNG</a
                >
              </a-space>
              <a-alert
                v-if="detail.screenshot?.last_error || screenshotError"
                class="tweet-screenshot__error"
                type="warning"
                show-icon
                :message="screenshotError || detail.screenshot?.last_error"
              />
              <a-spin :spinning="screenshotLoading">
                <a-image
                  v-if="screenshotUrl"
                  :src="screenshotUrl"
                  :alt="`@${detail.username} 的帖子截图`"
                  :width="'100%'"
                />
              </a-spin>
            </section>
            <a
              class="tweet-detail__link"
              :href="`https://x.com/${detail.username}/status/${detail.tweet_id}`"
              target="_blank"
              rel="noopener noreferrer"
              >在 X 查看原文 <ExternalLinkOutlined
            /></a>
          </template>
        </div>
      </a-spin> </a-modal
    ><a-modal
      v-model:open="pushOpen"
      title="批量推送到 QQ"
      ok-text="提交推送"
      cancel-text="取消"
      :confirm-loading="sending"
      @ok="sendBatch"
      ><a-form layout="vertical"
        ><a-form-item label="QQ 机器人"
          ><a-select
            v-model:value="pushForm.bot_id"
            :options="bots.map((bot) => ({ label: bot.name, value: bot.id }))"
            @change="changeBot" /></a-form-item
        ><a-form-item label="发送群"
          ><a-select
            v-model:value="pushForm.group_openids"
            mode="multiple"
            :options="
              groups.map((group) => ({
                label: group.name || group.group_openid,
                value: group.group_openid,
              }))
            " /></a-form-item></a-form
    ></a-modal>
  </div>
</template>

<style scoped>
.tweet-author {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 4px 10px;
  overflow-wrap: anywhere;
}
.tweet-author strong {
  color: var(--ink);
  font-weight: 600;
}
.tweet-author__handle {
  color: var(--muted);
  font-size: 12px;
}
.tweet-preview {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
  line-clamp: 3;
  overflow: hidden;
  overflow-wrap: anywhere;
  white-space: pre-wrap;
  line-height: 1.6;
  max-height: 4.8em;
  margin: 5px 0;
  color: var(--ink);
}
.tweet-detail {
  min-height: 160px;
  max-height: 70vh;
  overflow-y: auto;
  padding: 4px 4px 4px 0;
}
.tweet-detail__text {
  margin: 18px 0 24px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  line-height: 1.8;
  color: var(--ink);
}
.tweet-detail__link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-top: 20px;
}
.tweet-screenshot {
  margin-top: 20px;
}
.tweet-screenshot__error {
  margin: 12px 0;
}
.tweet-screenshot :deep(.ant-image) {
  margin-top: 12px;
}
</style>
