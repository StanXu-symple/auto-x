<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { message } from 'ant-design-vue'
import { xhsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { XHS_NOTE_CONTENT_MAX_LENGTH, XHS_NOTE_TITLE_MAX_LENGTH } from '@/constants/xhs'
import { useXhsVerification } from '@/composables/useXhsVerification'
import PageHeader from '@/components/PageHeader.vue'
import XhsVerificationModal from '@/components/XhsVerificationModal.vue'
const loading = ref(false)
const statusLoading = ref(true)
const status = ref<{
  saved: boolean
  connected: boolean
  installed: boolean
  worker_status?: string
} | null>(null)
const {
  open: verifyOpen,
  image: verifyImage,
  start: startVerification,
  stop: stopVerification,
} = useXhsVerification(xhsApi.verification)
const form = reactive({
  a1: '',
  web_session: '',
  title: '',
  content: '',
  images: [] as string[],
  previews: [] as string[],
})
async function refresh() {
  statusLoading.value = true
  try {
    status.value = await xhsApi.status()
  } catch (e) {
    message.error(getErrorMessage(e, '读取小红书状态失败'))
  } finally {
    statusLoading.value = false
  }
}
async function login() {
  if (loading.value) return
  if (!form.a1.trim() || !form.web_session.trim())
    return message.warning(
      status.value?.saved ? '更新登录态请同时填写 a1 与 web_session' : '请填写 a1 与 web_session',
    )
  loading.value = true
  try {
    await xhsApi.login({ a1: form.a1, web_session: form.web_session })
    form.a1 = ''
    form.web_session = ''
    await refresh()
    message.success('登录态已保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存登录态失败'))
  } finally {
    loading.value = false
  }
}
async function pasteImages(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files || []).filter((file) =>
    file.type.startsWith('image/'),
  )
  if (files.length) await upload({ target: { files, value: '' } })
}
async function upload(event: any) {
  if (loading.value) return
  const files = Array.from(event.target.files || []) as File[]
  event.target.value = ''
  if (!files.length) return
  loading.value = true
  try {
    const result = await xhsApi.upload(files)
    form.images.push(...result.files.map((item: any) => item.path))
    form.previews.push(...files.map((file) => URL.createObjectURL(file)))
    message.success(`已上传 ${files.length} 张图片`)
  } catch (e) {
    message.error(getErrorMessage(e, '上传失败'))
  } finally {
    loading.value = false
  }
}
function remove(index: number) {
  const url = form.previews[index]
  if (url) URL.revokeObjectURL(url)
  form.previews.splice(index, 1)
  form.images.splice(index, 1)
}
async function publish() {
  if (loading.value) return
  if (!form.title || !form.content || !form.images.length)
    return message.warning('请填写标题、正文并上传图片')
  if (
    [...form.title].length > XHS_NOTE_TITLE_MAX_LENGTH ||
    [...form.content].length > XHS_NOTE_CONTENT_MAX_LENGTH
  )
    return message.warning('标题或正文超出长度限制')
  loading.value = true
  startVerification()
  try {
    await xhsApi.post({ title: form.title, content: form.content, images: form.images })
    message.success('发布成功')
  } catch (e) {
    message.error(getErrorMessage(e, '发布失败'))
  } finally {
    stopVerification()
    loading.value = false
  }
}
onMounted(refresh)
onBeforeUnmount(() => {
  form.previews.forEach((url) => URL.revokeObjectURL(url))
})
</script>

<template>
  <div class="page-stack">
    <PageHeader eyebrow="CHANNEL / 04" title="小红书管理" description="登录并发布小红书图文笔记"
      ><template #actions
        ><a-tag :color="status?.saved ? 'green' : 'default'">{{
          status?.saved ? '登录态已保存' : '等待连接'
        }}</a-tag></template
      ></PageHeader
    ><a-spin :spinning="statusLoading"
      ><div class="two-column">
        <a-card title="连接账号" :bordered="false"
          ><a-alert
            type="info"
            show-icon
            message="Cookie 仅在后端使用；已保存时以密码占位符提示，不显示真实值。"
          /><a-form layout="vertical" style="margin-top: 20px"
            ><a-form-item label="a1"
              ><a-input-password
                v-model:value="form.a1"
                :placeholder="
                  status?.saved ? '••••••••（a1 已保存）' : '请输入 a1 Cookie 值'
                " /></a-form-item
            ><a-form-item label="web_session"
              ><a-input-password
                v-model:value="form.web_session"
                :placeholder="
                  status?.saved ? '••••••••（web_session 已保存）' : '请输入 web_session Cookie 值'
                "
            /></a-form-item>
            <div v-if="status?.saved" class="muted" style="margin-bottom: 16px">
              登录态已保存，无需重复填写。如需替换，请同时填写 a1 和 web_session 后保存。
            </div>
            <a-button type="primary" :loading="loading" @click="login">{{
              status?.saved ? '更新登录态' : '保存登录态'
            }}</a-button></a-form
          ></a-card
        ><a-card title="发布图文" :bordered="false"
          ><a-form layout="vertical" @paste="pasteImages"
            ><a-form-item label="标题"
              ><a-input
                v-model:value="form.title"
                :maxlength="XHS_NOTE_TITLE_MAX_LENGTH"
                show-count /></a-form-item
            ><a-form-item label="正文"
              ><a-textarea
                v-model:value="form.content"
                :maxlength="XHS_NOTE_CONTENT_MAX_LENGTH"
                :rows="6"
                show-count /></a-form-item
            ><a-form-item label="照片"
              ><label class="upload-zone"
                ><input
                  type="file"
                  accept="image/jpeg,image/png,image/webp"
                  multiple
                  @change="upload"
                /><strong>选择照片</strong
                ><span>支持 JPG、PNG、WebP，也可在页面粘贴图片</span></label
              >
              <div v-if="form.previews.length" class="image-strip">
                <div v-for="(src, index) in form.previews" :key="src">
                  <img :src="src" alt="待发布图片" /><a-button
                    type="text"
                    danger
                    @click="remove(index)"
                    >删除</a-button
                  >
                </div>
              </div></a-form-item
            ><a-button type="primary" :loading="loading" @click="publish"
              >发布笔记</a-button
            ></a-form
          ></a-card
        >
      </div></a-spin
    ><XhsVerificationModal :open="verifyOpen" :image="verifyImage" />
  </div>
</template>
