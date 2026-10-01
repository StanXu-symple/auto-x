<script setup lang="ts">
import { usePagedTable } from '@/composables/usePagedTable'
import { onMounted, reactive, ref, watch } from 'vue'
import {
  DeleteOutlined,
  EditOutlined,
  EyeOutlined,
  ExportOutlined,
  HistoryOutlined,
  PlusOutlined,
  SendOutlined,
  UploadOutlined,
} from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { articlesApi, qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type {
  Article,
  ArticlePayload,
  ArticlePublishStatus,
  EntityId,
  QQBotAccount,
  QQJoinedGroup,
} from '@/types'
import { formatDateTime } from '@/utils/format'
import PageHeader from '@/components/PageHeader.vue'
import StatusPill from '@/components/StatusPill.vue'
import ArticleMediaGallery from '@/components/ArticleMediaGallery.vue'
import ArticlePreview from '@/components/ArticlePreview.vue'
const saving = ref(false)
const uploading = ref(false)
const open = ref(false)
const publishOpen = ref(false)
const previewOpen = ref(false)
const historyOpen = ref(false)
const editing = ref<Article | null>(null)
const publishing = ref<Article | null>(null)
const previewing = ref<Article | null>(null)
const publishSubmitting = ref(false)
const publishChannelLoading = ref(false)
let channelRequest = 0
const bots = ref<QQBotAccount[]>([])
const groups = ref<QQJoinedGroup[]>([])
const historyArticle = ref<EntityId | null>(null)
const form = reactive<ArticlePayload>({ title: '', content: '', excerpt: '', images: [] })
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
  editing.value = article || null
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
  if (!form.title.trim() || !form.content.trim()) return message.warning('请填写标题和正文')
  saving.value = true
  try {
    if (editing.value)
      await articlesApi.update(editing.value.id, { ...form, revision: editing.value.revision })
    else await articlesApi.create(form)
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
  if (!files.length) return
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
  try {
    await articlesApi.publish(publishing.value.id, {
      channel: publishForm.channel,
      bot_id: publishForm.channel === 'qq' ? publishForm.bot_id || undefined : undefined,
      group_openids: publishForm.channel === 'qq' ? publishForm.group_openids : undefined,
    })
    publishOpen.value = false
    await load()
    message.success('发布任务已提交')
  } catch (e) {
    message.error(getErrorMessage(e, '发布失败'))
  } finally {
    publishSubmitting.value = false
  }
}
async function showHistory(article: Article) {
  historyArticle.value = article.id
  history.value = []
  historyOpen.value = true
  await resetHistory()
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
          />
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
            ><StatusPill :value="record.publish_status" /></template></a-table-column
        ><a-table-column title="更新时间"
          ><template #default="{ record }">{{
            formatDateTime(record.updated_at)
          }}</template></a-table-column
        ><a-table-column title="操作"
          ><template #default="{ record }"
            ><a-space
              ><a-tooltip title="查看文章"
                ><a-button type="link" aria-label="查看文章" @click="view(record)"
                  ><EyeOutlined /></a-button></a-tooltip
              ><a-button type="link" aria-label="编辑文章" @click="edit(record)"
                ><EditOutlined /></a-button
              ><a-button type="link" aria-label="发布文章" @click="preparePublish(record)"
                ><SendOutlined /></a-button
              ><a-button type="link" aria-label="查看发布历史" @click="showHistory(record)"
                ><HistoryOutlined /></a-button
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
      @ok="save"
      ><a-form layout="vertical"
        ><a-form-item label="标题" required><a-input v-model:value="form.title" /></a-form-item
        ><a-form-item label="摘要"
          ><a-textarea v-model:value="form.excerpt" :rows="2" /></a-form-item
        ><a-form-item label="正文" required
          ><a-textarea v-model:value="form.content" :rows="14" /></a-form-item
        ><a-form-item label="媒体"
          ><label class="upload-zone"
            ><input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              multiple
              @change="upload"
            /><strong><UploadOutlined /> {{ uploading ? '上传中…' : '上传媒体' }}</strong
            ><span>支持 JPG、PNG、WebP 图片，随文章保存</span></label
          >
          <div v-if="form.images.length" class="muted" style="margin-top: 10px">
            已添加 {{ form.images.length }} 个媒体文件
          </div>
          <ArticleMediaGallery
            v-if="open && form.images.length"
            :images="form.images"
            style="margin-top: 12px"
          /> </a-form-item></a-form
    ></a-modal>
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
      @ok="submitPublish"
      ><ArticlePreview
        v-if="publishOpen && publishing"
        :article="publishing"
        :channel="publishForm.channel" />
      <a-form layout="vertical"
        ><a-form-item label="发布渠道"
          ><a-radio-group v-model:value="publishForm.channel"
            ><a-radio value="qq">QQ</a-radio><a-radio value="xhs">小红书</a-radio></a-radio-group
          ></a-form-item
        ><template v-if="publishForm.channel === 'qq'"
          ><a-form-item label="机器人"
            ><a-select
              v-model:value="publishForm.bot_id"
              :loading="publishChannelLoading"
              :options="bots.map((bot) => ({ label: bot.name, value: bot.id }))"
              @change="changeBot" /></a-form-item
          ><a-form-item label="发送群"
            ><a-select
              v-model:value="publishForm.group_openids"
              :loading="publishChannelLoading"
              mode="multiple"
              :options="
                groups.map((group) => ({
                  label: group.name || group.group_openid,
                  value: group.group_openid,
                }))
              " /></a-form-item></template></a-form
    ></a-modal>
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
            </div>
            <StatusPill :value="item.status" /></a-list-item></template></a-list
    ></a-modal>
  </div>
</template>
