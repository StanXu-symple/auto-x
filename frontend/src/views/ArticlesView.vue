<script setup lang="ts">
import { usePagedTable } from '@/composables/usePagedTable'
import { useXhsVerification } from '@/composables/useXhsVerification'
import { computed, onMounted, reactive, ref, watch } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  ExportOutlined,
  HistoryOutlined,
  PlusOutlined,
  ReloadOutlined,
  SendOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { aiApi, articlesApi, qqApi, xhsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type {
  Article,
  ArticlePayload,
  ArticlePublishStatus,
  ArticlePublishReconcilePayload,
  AutoPublishDispatch,
  EntityId,
  QQBotAccount,
  QQJoinedGroup,
} from '@/types'
import { formatDateTime } from '@/utils/format'
import { reconciliationLabels, safeXhsPublishedUrl } from '@/utils/publishReconciliation'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import AutoPublishStatus from '@/components/AutoPublishStatus.vue'
import ArticleMediaGallery from '@/components/ArticleMediaGallery.vue'
import ArticlePreview from '@/components/ArticlePreview.vue'
import XhsVerificationModal from '@/components/XhsVerificationModal.vue'
const saving = ref(false)
const uploading = ref(false)
const open = ref(false)
const publishOpen = ref(false)
const previewOpen = ref(false)
const historyOpen = ref(false)
const reconcileOpen = ref(false)
const reconcileSubmitting = ref(false)
const reconciling = ref<Article | null>(null)
const reconcileDispatch = ref<AutoPublishDispatch | null>(null)
const reconcileForm = reactive<ArticlePublishReconcilePayload>({
  decision: 'uncertain',
  published_url: '',
  note: '',
})
const editing = ref<Article | null>(null)
const publishing = ref<Article | null>(null)
const previewing = ref<Article | null>(null)
const publishSubmitting = ref(false)
const {
  open: verifyOpen,
  image: verifyImage,
  start: startVerification,
  stop: stopVerification,
} = useXhsVerification(xhsApi.verification)
const publishChannelLoading = ref(false)
let channelRequest = 0
const bots = ref<QQBotAccount[]>([])
const groups = ref<QQJoinedGroup[]>([])
const historyArticle = ref<EntityId | null>(null)
const form = reactive<ArticlePayload>({ title: '', content: '', excerpt: '', images: [] })
const includeSourceScreenshot = ref(true)
const sourceScreenshot = computed(() =>
  includeSourceScreenshot.value ? editing.value?.source_screenshot : null,
)
const visibleMediaCount = computed(() => form.images.length + (sourceScreenshot.value ? 1 : 0))
// Reserve a slot even while a restored or pending source screenshot is not yet loaded.
const mediaCount = computed(
  () =>
    form.images.length + (includeSourceScreenshot.value && editing.value?.source_tweet_id ? 1 : 0),
)
const publishForm = reactive({
  channel: 'qq' as 'qq' | 'xhs',
  bot_id: null as number | null,
  group_openids: [] as string[],
})
const filters = reactive({ keyword: '', article_source: 'all', publish_status: 'all' })
const {
  rows,
  total,
  loading,
  pagination,
  load,
  reset,
  change: changePage,
} = usePagedTable(
  (query) =>
    articlesApi.list({
      ...query,
      keyword: filters.keyword || undefined,
      article_source:
        filters.article_source === 'all' ? undefined : (filters.article_source as 'ai' | 'user'),
      publish_status:
        filters.publish_status === 'all'
          ? undefined
          : (filters.publish_status as ArticlePublishStatus),
    }),
  '无法加载文章',
)
const {
  rows: history,
  loading: historyLoading,
  pagination: historyPagination,
  reset: resetHistory,
  change: changeHistoryPage,
} = usePagedTable(
  (query) => articlesApi.publishHistory(historyArticle.value!, query),
  '无法读取发布历史',
)
function edit(article?: Article) {
  if (saving.value || uploading.value) return
  editing.value = article || null
  includeSourceScreenshot.value = article?.include_source_screenshot !== false
  Object.assign(
    form,
    article
      ? {
          title: article.title,
          content: article.content,
          excerpt: article.excerpt || '',
          images: [...(article.images || [])],
        }
      : { title: '', content: '', excerpt: '', images: [] },
  )
  open.value = true
}
function view(article: Article) {
  previewing.value = article
  previewOpen.value = true
}
async function save() {
  if (saving.value || uploading.value) return
  if (!form.title.trim() || !form.content.trim()) return message.warning('请填写标题和正文')
  saving.value = true
  try {
    const payload = { ...form, images: [...form.images] }
    if (editing.value)
      await articlesApi.update(editing.value.id, {
        ...payload,
        include_source_screenshot: includeSourceScreenshot.value,
        revision: editing.value.revision,
      })
    else await articlesApi.create(payload)
    open.value = false
    await load()
    message.success('文章已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存失败'))
  } finally {
    saving.value = false
  }
}
async function upload(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || [])
  ;(event.target as HTMLInputElement).value = ''
  if (!open.value || !files.length || uploading.value || saving.value) return
  if (mediaCount.value + files.length > 18)
    return message.warning('每篇文章最多 18 张图片（含自动关联的原帖截图）')
  uploading.value = true
  try {
    const result = await articlesApi.upload(files)
    form.images.push(...result.files.map((item) => item.path))
    message.success(`已上传 ${files.length} 张媒体`)
  } catch (e) {
    message.error(getErrorMessage(e, '媒体上传失败'))
  } finally {
    uploading.value = false
  }
}
function removeImage(path: string) {
  if (saving.value || uploading.value) return
  form.images = form.images.filter((image) => image !== path)
}
function removeSourceScreenshot() {
  if (saving.value || uploading.value) return
  includeSourceScreenshot.value = false
}
function restoreSourceScreenshot() {
  if (saving.value || uploading.value) return
  if (form.images.length >= 18) return message.warning('请先删除一张图片，为原帖截图留出位置')
  includeSourceScreenshot.value = true
}
function remove(article: Article) {
  Modal.confirm({
    title: `删除「${article.title}」？`,
    okType: 'danger',
    okText: '删除',
    cancelText: '取消',
    onOk: async () => {
      await articlesApi.remove(article.id)
      await load()
      message.success('文章已删除')
    },
  })
}
async function preparePublish(article: Article) {
  if (publishSubmitting.value) return
  const request = ++channelRequest
  publishing.value = article
  publishForm.channel = 'qq'
  publishForm.bot_id = null
  publishForm.group_openids = []
  bots.value = []
  groups.value = []
  publishOpen.value = true
  publishChannelLoading.value = true
  try {
    const loadedBots = await qqApi.bots()
    if (request !== channelRequest || !publishOpen.value) return
    bots.value = loadedBots
    const botId = loadedBots.find((bot) => bot.is_enabled)?.id || null
    publishForm.bot_id = botId
    const loadedGroups = botId ? await qqApi.joinedGroups(botId) : []
    if (request !== channelRequest || !publishOpen.value) return
    groups.value = loadedGroups
  } catch (e) {
    if (request === channelRequest && publishOpen.value)
      message.error(getErrorMessage(e, '读取 QQ 发布渠道失败'))
  } finally {
    if (request === channelRequest) publishChannelLoading.value = false
  }
}
async function changeBot() {
  const request = ++channelRequest
  const botId = publishForm.bot_id
  publishForm.group_openids = []
  groups.value = []
  publishChannelLoading.value = true
  try {
    const loadedGroups = botId ? await qqApi.joinedGroups(botId) : []
    if (request === channelRequest && publishOpen.value) groups.value = loadedGroups
  } catch (e) {
    if (request === channelRequest && publishOpen.value)
      message.error(getErrorMessage(e, '读取机器人加入的群失败'))
  } finally {
    if (request === channelRequest) publishChannelLoading.value = false
  }
}
async function submitPublish() {
  if (!publishing.value || publishSubmitting.value) return
  if (publishForm.channel === 'qq' && (!publishForm.bot_id || !publishForm.group_openids.length))
    return message.warning('请选择 QQ 机器人和发送群')
  publishSubmitting.value = true
  const channel = publishForm.channel
  if (channel === 'xhs') startVerification()
  try {
    await articlesApi.publish(publishing.value.id, {
      channel,
      bot_id: channel === 'qq' ? publishForm.bot_id || undefined : undefined,
      group_openids: channel === 'qq' ? [...publishForm.group_openids] : undefined,
    })
    publishOpen.value = false
    message.success(channel === 'xhs' ? '小红书发布成功' : 'QQ 发布任务已提交')
  } catch (e) {
    message.error(getErrorMessage(e, '发布失败'))
  } finally {
    stopVerification()
    await load()
    publishSubmitting.value = false
  }
}
async function showHistory(article: Article) {
  historyArticle.value = article.id
  history.value = []
  historyOpen.value = true
  await resetHistory()
}
function uncertainXhsDispatch(article: Article) {
  return article.auto_publish_dispatches?.find(
    (dispatch) => dispatch.channel === 'xhs' && dispatch.status === 'uncertain',
  )
}
function hasManualPublish(article: Article) {
  return Boolean(article.publish_channel && article.publish_status !== 'unpublished')
}
function visibleAutoDispatches(article: Article) {
  return (article.auto_publish_dispatches || []).filter(
    (dispatch) =>
      !(
        hasManualPublish(article) &&
        dispatch.channel === article.publish_channel &&
        dispatch.reconciliation_result === 'not_published'
      ),
  )
}
function manualPublishLabel(article: Article) {
  const channel = article.publish_channel === 'xhs' ? '小红书' : 'QQ'
  const status = {
    unpublished: '未发布',
    queued: '发布中',
    published: '已发布',
    failed: '发布失败',
  }[article.publish_status]
  return `${channel}（手动）：${status}`
}
function reconciliationLabel(result: keyof typeof reconciliationLabels) {
  return reconciliationLabels[result]
}
function prepareReconcile(article: Article) {
  if (reconcileSubmitting.value) return
  const dispatch = uncertainXhsDispatch(article)
  if (!dispatch) return
  reconciling.value = article
  reconcileDispatch.value = dispatch
  Object.assign(reconcileForm, { decision: 'uncertain', published_url: '', note: '' })
  reconcileOpen.value = true
}
async function submitReconcile() {
  if (!reconciling.value || !reconcileDispatch.value || reconcileSubmitting.value) return
  const note = reconcileForm.note?.trim() || ''
  const publishedUrl = reconcileForm.published_url?.trim() || ''
  if (reconcileForm.decision === 'published' && !publishedUrl)
    return message.warning('请填写已发布的小红书笔记链接')
  if (reconcileForm.decision === 'published' && !safeXhsPublishedUrl(publishedUrl))
    return message.warning('请填写有效的 HTTPS 小红书笔记链接或 xhslink.com 分享链接')
  reconcileSubmitting.value = true
  try {
    await aiApi.reconcilePublishDispatch(reconcileDispatch.value.id, {
      decision: reconcileForm.decision,
      published_url: reconcileForm.decision === 'published' ? publishedUrl : null,
      note: note || null,
    })
    reconcileOpen.value = false
    message.success(
      reconcileForm.decision === 'published'
        ? '已记录为已推送'
        : reconcileForm.decision === 'not_published'
          ? '已记录为未发布，可以重新发布'
          : '已保存核对说明，继续保留待核对状态',
    )
    await load()
  } catch (e) {
    message.error(getErrorMessage(e, '保存核对结果失败'))
  } finally {
    reconcileSubmitting.value = false
  }
}
onMounted(load)
watch(
  () => [filters.article_source, filters.publish_status],
  () => {
    void reset()
  },
)
</script>
<template>
  <div class="page-stack">
    <PageHeader
      eyebrow="CONTENT / 02"
      title="文章管理"
      description="统一管理 AI 草稿与手动创建的文章"
      ><template #actions
        ><a-button type="primary" @click="edit()"><PlusOutlined /> 新建文章</a-button></template
      ></PageHeader
    >
    <a-card :bordered="false"
      ><div class="toolbar">
        <div class="toolbar__controls">
          <a-input
            v-model:value="filters.keyword"
            placeholder="搜索标题或正文"
            allow-clear
            style="width: 230px"
            @press-enter="reset"
          /><a-select
            v-model:value="filters.article_source"
            style="width: 130px"
            :options="[
              { label: '全部来源', value: 'all' },
              { label: 'AI 生成', value: 'ai' },
              { label: '手动创建', value: 'user' },
            ]"
          /><a-select
            v-model:value="filters.publish_status"
            style="width: 140px"
            :options="[
              { label: '全部状态', value: 'all' },
              { label: '未发布', value: 'unpublished' },
              { label: '发布中', value: 'queued' },
              { label: '已发布', value: 'published' },
              { label: '失败', value: 'failed' },
            ]"
          /><a-button :loading="loading" @click="load"><ReloadOutlined /> 刷新</a-button>
        </div>
        <span class="toolbar__hint">共 {{ total }} 篇</span>
      </div>
      <a-table
        :data-source="rows"
        :loading="loading"
        row-key="id"
        :pagination="pagination"
        @change="changePage"
        ><a-table-column title="文章"
          ><template #default="{ record }"
            ><strong>{{ record.title }}</strong>
            <p
              class="muted"
              style="
                max-width: 460px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
              "
            >
              {{ record.excerpt || record.content }}
            </p></template
          ></a-table-column
        ><a-table-column title="来源"
          ><template #default="{ record }">{{
            record.article_source === 'ai' ? 'AI 生成' : '手动创建'
          }}</template></a-table-column
        ><a-table-column title="状态"
          ><template #default="{ record }"
            ><a-space direction="vertical" size="small">
              <AutoPublishStatus
                v-if="visibleAutoDispatches(record).length"
                :dispatches="visibleAutoDispatches(record)"
              />
              <a-tooltip
                v-if="hasManualPublish(record)"
                :title="record.publish_error || '手动发布结果'"
              >
                <StatusPill :value="record.publish_status" :label="manualPublishLabel(record)" />
              </a-tooltip>
              <StatusPill
                v-if="!record.auto_publish_dispatches?.length && !hasManualPublish(record)"
                :value="record.publish_status"
              /> </a-space></template></a-table-column
        ><a-table-column title="更新时间"
          ><template #default="{ record }">{{
            formatDateTime(record.updated_at)
          }}</template></a-table-column
        ><a-table-column title="操作" :width="310"
          ><template #default="{ record }"
            ><a-space style="flex-wrap: wrap"
              ><a-tooltip title="查看文章"
                ><a-button type="link" aria-label="查看文章" @click="view(record)"
                  ><EyeOutlined /></a-button></a-tooltip
              ><a-button type="link" aria-label="编辑文章" @click="edit(record)"
                ><EditOutlined /></a-button
              ><a-button type="link" aria-label="发布文章" @click="preparePublish(record)"
                ><SendOutlined /></a-button
              ><a-button type="link" aria-label="查看发布历史" @click="showHistory(record)"
                ><HistoryOutlined /></a-button
              ><a-button
                v-if="uncertainXhsDispatch(record)"
                type="link"
                @click="prepareReconcile(record)"
                >核对发布结果</a-button
              ><a-button type="link" danger aria-label="删除文章" @click="remove(record)"
                ><DeleteOutlined /></a-button></a-space></template></a-table-column
      ></a-table>
    </a-card>
    <a-modal
      v-model:open="open"
      :title="editing ? '编辑文章' : '新建文章'"
      ok-text="保存"
      cancel-text="取消"
      width="760px"
      :confirm-loading="saving"
      :ok-button-props="{ disabled: uploading }"
      :closable="!saving && !uploading"
      :mask-closable="!saving && !uploading"
      :keyboard="!saving && !uploading"
      :cancel-button-props="{ disabled: saving || uploading }"
      @ok="save"
      ><a-form layout="vertical" :disabled="saving"
        ><a-form-item label="标题" required><a-input v-model:value="form.title" /></a-form-item
        ><a-form-item label="摘要"
          ><a-textarea v-model:value="form.excerpt" :rows="2" /></a-form-item
        ><a-form-item label="正文" required
          ><a-textarea v-model:value="form.content" :rows="14" /></a-form-item
        ><a-form-item
          label="媒体"
          :extra="
            includeSourceScreenshot && editing?.source_tweet_id
              ? '每篇文章最多 18 张图片（为原帖截图保留 1 张）。删除图片在保存后生效，取消编辑会保留原图片。'
              : '每篇文章最多 18 张图片。删除图片在保存后生效，取消编辑会保留原图片。'
          "
          ><label class="upload-zone"
            ><input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              :disabled="saving || uploading || mediaCount >= 18"
              @change="upload"
            /><strong><UploadOutlined /> {{ uploading ? '上传中…' : '上传媒体' }}</strong
            ><span>支持 JPG、PNG、WebP 图片，随文章保存</span></label
          >
          <div v-if="visibleMediaCount" class="muted" style="margin-top: 10px">
            已添加 {{ visibleMediaCount }} 张图片
          </div>
          <ArticleMediaGallery
            v-if="open && visibleMediaCount"
            :images="form.images"
            :source-screenshot="sourceScreenshot"
            removable
            :disabled="saving || uploading"
            @remove-image="removeImage"
            @remove-source-screenshot="removeSourceScreenshot"
            style="margin-top: 12px"
          />
          <div v-if="editing?.source_tweet_id && !includeSourceScreenshot" style="margin-top: 10px">
            <span class="muted">当前文章已移除原帖截图。</span>
            <a-button
              type="link"
              size="small"
              :disabled="saving || uploading"
              @click="restoreSourceScreenshot"
              >恢复自动关联</a-button
            >
          </div>
          <p
            v-else-if="editing?.source_tweet_id && !sourceScreenshot"
            class="muted"
            style="margin-top: 10px"
          >
            保存后将自动关联来源帖子的可用截图。
          </p>
        </a-form-item></a-form
      ></a-modal
    >
    <a-modal
      v-model:open="previewOpen"
      title="查看文章"
      width="760px"
      :body-style="{ maxHeight: '70vh', overflowY: 'auto' }"
    >
      <ArticlePreview v-if="previewOpen && previewing" :article="previewing" />
      <template #footer>
        <a-button @click="previewOpen = false">关闭</a-button>
        <a-tooltip :title="previewing?.source_url ? '在新标签页打开原文' : '该文章未关联原文'">
          <a-button
            type="primary"
            :href="previewing?.source_url || undefined"
            target="_blank"
            rel="noopener noreferrer"
            :disabled="!previewing?.source_url"
          >
            <ExportOutlined /> 查看原文
          </a-button>
        </a-tooltip>
      </template>
    </a-modal>
    <a-modal
      v-model:open="publishOpen"
      title="发布文章"
      ok-text="提交发布"
      cancel-text="取消"
      width="760px"
      :body-style="{ maxHeight: '70vh', overflowY: 'auto' }"
      :confirm-loading="publishSubmitting"
      :closable="!publishSubmitting"
      :mask-closable="!publishSubmitting"
      :keyboard="!publishSubmitting"
      :cancel-button-props="{ disabled: publishSubmitting }"
      @ok="submitPublish"
      ><ArticlePreview
        v-if="publishOpen && publishing"
        :article="publishing"
        :channel="publishForm.channel" />
      <a-form layout="vertical"
        ><a-form-item label="发布渠道"
          ><a-radio-group v-model:value="publishForm.channel" :disabled="publishSubmitting"
            ><a-radio value="qq">QQ</a-radio><a-radio value="xhs">小红书</a-radio></a-radio-group
          ></a-form-item
        ><template v-if="publishForm.channel === 'qq'"
          ><a-form-item label="机器人"
            ><a-select
              v-model:value="publishForm.bot_id"
              :disabled="publishSubmitting"
              :loading="publishChannelLoading"
              :options="bots.map((bot) => ({ label: bot.name, value: bot.id }))"
              @change="changeBot" /></a-form-item
          ><a-form-item label="发送群"
            ><a-select
              v-model:value="publishForm.group_openids"
              :disabled="publishSubmitting"
              :loading="publishChannelLoading"
              mode="multiple"
              :options="
                groups.map((group) => ({
                  label: group.name || group.group_openid,
                  value: group.group_openid,
                }))
              " /></a-form-item></template></a-form
    ></a-modal>
    <a-modal
      v-model:open="reconcileOpen"
      title="核对小红书发布结果"
      ok-text="保存核对结果"
      cancel-text="取消"
      width="620px"
      :confirm-loading="reconcileSubmitting"
      :closable="!reconcileSubmitting"
      :mask-closable="!reconcileSubmitting"
      :keyboard="!reconcileSubmitting"
      :cancel-button-props="{ disabled: reconcileSubmitting }"
      @ok="submitReconcile"
    >
      <a-alert
        type="warning"
        show-icon
        message="请先到小红书 App 或创作中心核对该笔记"
        description="结果待核对表示笔记可能已经发布。请确认实际结果后再保存，避免重复发布。"
        style="margin-bottom: 20px"
      />
      <p>
        <strong>{{ reconciling?.title }}</strong>
      </p>
      <p v-if="reconcileDispatch?.last_error" class="muted">
        待核对原因：{{ reconcileDispatch.last_error }}
      </p>
      <a-form layout="vertical" :disabled="reconcileSubmitting">
        <a-form-item label="核对结论" required>
          <a-radio-group v-model:value="reconcileForm.decision">
            <a-radio value="published">确认已发布</a-radio>
            <a-radio value="not_published">确认未发布</a-radio>
            <a-radio value="uncertain">暂不确定</a-radio>
          </a-radio-group>
        </a-form-item>
        <a-form-item
          v-if="reconcileForm.decision === 'published'"
          label="小红书笔记链接"
          required
          extra="填写在平台上核对到的笔记链接，保存后更新为已推送。"
        >
          <a-input
            v-model:value="reconcileForm.published_url"
            placeholder="https://www.xiaohongshu.com/explore/…"
            :maxlength="2048"
          />
        </a-form-item>
        <a-form-item
          label="核对说明"
          :extra="
            reconcileForm.decision === 'not_published'
              ? '可说明核对方式和未发布的依据。保存后可手动重新发布，不会立即重发。'
              : reconcileForm.decision === 'uncertain'
                ? '保留待核对状态，之后可以继续核对。'
                : '可补充核对方式、账号和时间。'
          "
        >
          <a-textarea
            v-model:value="reconcileForm.note"
            :rows="3"
            :maxlength="2000"
            show-count
            placeholder="例如：已在当前账号的笔记管理中核对"
          />
        </a-form-item>
      </a-form>
    </a-modal>
    <XhsVerificationModal :open="verifyOpen" :image="verifyImage" />
    <a-modal v-model:open="historyOpen" title="发布历史" :footer="null"
      ><a-list
        :data-source="history"
        :loading="historyLoading"
        :pagination="{
          ...historyPagination,
          onChange: (current: number, pageSize: number) => changeHistoryPage({ current, pageSize }),
        }"
        ><template #renderItem="{ item }"
          ><a-list-item
            ><div>
              <strong>{{ item.channel }}</strong
              ><span class="muted">
                · {{ item.target_summary }} · {{ formatDateTime(item.created_at) }}</span
              >
              <div v-if="item.reconciliation_result" style="margin-top: 8px">
                {{ reconciliationLabel(item.reconciliation_result) }}
                <span v-if="item.reconciled_at" class="muted">
                  · {{ formatDateTime(item.reconciled_at) }}
                </span>
                <p v-if="item.reconciliation_note" class="muted">
                  核对说明：{{ item.reconciliation_note }}
                </p>
                <a
                  v-if="safeXhsPublishedUrl(item.published_url)"
                  :href="safeXhsPublishedUrl(item.published_url)"
                  target="_blank"
                  rel="noopener noreferrer"
                  >查看笔记</a
                >
              </div>
            </div>
            <StatusPill
              :value="item.status"
              :label="
                item.reconciliation_result === 'not_published' ? '已核对未发布' : undefined
              " /></a-list-item></template></a-list
    ></a-modal>
  </div>
</template>
