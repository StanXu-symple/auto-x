<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { BookOpen, CheckCircle2, Send, ShieldCheck, UploadCloud } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { xhsApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'

const loading = ref(false)
const statusLoading = ref(true)
const status = ref<{
  saved: boolean
  connected: boolean
  installed: boolean
  worker_status?: string
  message?: string
} | null>(null)
const verificationVisible = ref(false)
const verificationImage = ref('')
const verificationVersion = ref('')
let verificationTimer: ReturnType<typeof setInterval> | undefined
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
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    statusLoading.value = false
  }
}

async function login() {
  if (!form.a1 || !form.web_session) return ElMessage.warning('请填写 a1 和 web_session')
  loading.value = true
  try {
    await xhsApi.login({ a1: form.a1, web_session: form.web_session })
    form.a1 = ''
    form.web_session = ''
    await refresh()
    ElMessage.success('登录态已保存')
  } catch (error) {
    ElMessage.error(getErrorMessage(error))
  } finally {
    loading.value = false
  }
}

async function uploadFiles(files: File[]) {
  if (!files.length) return
  loading.value = true
  try {
    const result = await xhsApi.upload(files)
    form.images.push(...result.files.map((file: { path: string }) => file.path))
    form.previews.push(...files.map((file) => URL.createObjectURL(file)))
    ElMessage.success(`已上传 ${files.length} 张图片`)
  } catch (error) {
    ElMessage.error(getErrorMessage(error, '上传失败'))
  } finally {
    loading.value = false
  }
}

async function chooseFiles(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  await uploadFiles(files)
}

async function pasteFiles(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.items || [])
    .filter((item) => item.kind === 'file' && item.type.startsWith('image/'))
    .map((item, index) => {
      const file = item.getAsFile()
      if (!file) return null
      const extension = {
        'image/jpeg': 'jpg',
        'image/png': 'png',
        'image/webp': 'webp',
      }[file.type]
      return extension
        ? new File([file], `clipboard-${Date.now()}-${index}.${extension}`, { type: file.type })
        : file
    })
    .filter((file): file is File => file !== null)
  if (!files.length) return
  event.preventDefault()
  await uploadFiles(files)
}

function removeImage(index: number) {
  const url = form.previews[index]
  if (url) URL.revokeObjectURL(url)
  form.images.splice(index, 1)
  form.previews.splice(index, 1)
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
    // The publish request remains authoritative during a transient polling failure.
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

async function publish() {
  if (!form.title || !form.content || !form.images.length) {
    return ElMessage.warning('请填写标题、正文并上传图片')
  }
  loading.value = true
  startVerificationPolling()
  try {
    await xhsApi.post({ title: form.title, content: form.content, images: form.images })
    verificationVisible.value = false
    ElMessage.success('发布成功')
  } catch (error) {
    verificationVisible.value = false
    ElMessage.error(getErrorMessage(error, '发布失败'))
  } finally {
    stopVerificationPolling()
    loading.value = false
  }
}

onMounted(() => {
  void refresh()
  window.addEventListener('paste', pasteFiles)
})

onBeforeUnmount(() => {
  stopVerificationPolling()
  window.removeEventListener('paste', pasteFiles)
  form.previews.forEach((url) => URL.revokeObjectURL(url))
})
</script>

<template>
  <div v-loading="statusLoading" element-loading-text="正在读取登录态..." class="xhs-page page-stack">
    <header class="xhs-hero">
      <div class="xhs-hero__mark"><BookOpen :size="22" /></div>
      <div><span class="xhs-kicker">CONTENT CHANNEL</span><h1>小红书管理</h1><p>从登录态到图文发布，集中管理你的内容出口。</p></div>
      <div class="xhs-status" :class="{ 'is-connected': status?.saved }"><span />{{ status?.saved ? '登录态已保存' : '等待连接' }}</div>
    </header>
    <div class="xhs-grid">
      <section class="panel xhs-card">
        <div class="xhs-card__head"><div><span class="xhs-index">01</span><h2>连接账号</h2><p>分开填写浏览器 Cookie 字段</p></div><ShieldCheck :size="22" /></div>
        <div class="xhs-guide"><strong>操作教程</strong><ol><li>在浏览器登录小红书。</li><li>打开开发者工具的 Application / Storage。</li><li>进入小红书域名 Cookies，复制 a1 和 web_session 的值。</li><li>分别粘贴并保存，系统会验证登录态。</li></ol></div>
        <label>a1</label><el-input v-model="form.a1" type="password" show-password :placeholder="status?.saved ? '••••••••••••' : '请输入 a1 Cookie 值'" />
        <label>web_session</label><el-input v-model="form.web_session" type="password" show-password :placeholder="status?.saved ? '••••••••••••' : '请输入 web_session Cookie 值'" />
        <small>已保存的值不会回显，凭据只在后端 CLI 调用时使用。</small>
        <el-button type="primary" :loading="loading" @click="login"><CheckCircle2 :size="16" />{{ status?.saved ? '更新登录态' : '保存登录态' }}</el-button>
      </section>
      <section class="panel xhs-card">
        <div class="xhs-card__head"><div><span class="xhs-index">02</span><h2>发布图文</h2><p>上传图片后发布到小红书</p></div><UploadCloud :size="22" /></div>
        <label>标题</label><el-input v-model="form.title" maxlength="80" show-word-limit placeholder="写一个清晰、有记忆点的标题" />
        <label>正文</label><el-input v-model="form.content" type="textarea" :rows="7" placeholder="分享你的观点、步骤或体验" />
        <label>照片</label>
        <label class="xhs-upload-zone" tabindex="0"><input class="xhs-file-input" type="file" accept="image/jpeg,image/png,image/webp" multiple @change="chooseFiles" /><UploadCloud :size="20" /><strong>选择或粘贴照片</strong><span>支持 JPG、PNG、WebP，可多选，也可直接粘贴剪贴板图片</span></label>
        <div v-if="form.previews.length" class="xhs-photo-grid"><div v-for="(photo, index) in form.previews" :key="photo" class="xhs-photo"><button type="button" aria-label="删除照片" @click.stop="removeImage(index)">×</button><el-image :src="photo" :preview-src-list="form.previews" :initial-index="index" preview-teleported hide-on-click-modal fit="cover" alt="已上传照片" /></div></div>
        <div class="xhs-compose__foot"><span>{{ form.images.length }} 张图片已上传</span><el-button type="primary" :loading="loading" @click="publish"><Send :size="16" />发布笔记</el-button></div>
      </section>
    </div>
    <el-dialog v-model="verificationVisible" title="完成小红书安全验证" width="min(92vw, 520px)" :close-on-click-modal="false" append-to-body>
      <div class="xhs-verification">
        <p>请使用已登录当前账号的小红书 App 扫描二维码，验证完成后页面会自动继续发布。</p>
        <div class="xhs-verification__image"><img v-if="verificationImage" :src="verificationImage" alt="小红书安全验证二维码" /><span v-else>正在获取验证二维码...</span></div>
      </div>
    </el-dialog>
  </div>
</template>
