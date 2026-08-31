<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { AtSign, Clock3 } from 'lucide-vue-next'
import type { CreateMonitoredUserPayload, MonitoredUser, UpdateMonitoredUserPayload } from '@/types'
import BaseModal from './BaseModal.vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    account?: MonitoredUser | null
    loading?: boolean
    serverError?: string
    defaultInterval?: number
  }>(),
  { account: null, loading: false, serverError: '', defaultInterval: 300 },
)

const emit = defineEmits<{
  close: []
  submit: [payload: CreateMonitoredUserPayload | UpdateMonitoredUserPayload]
}>()

const form = reactive({ username: '', poll_interval_seconds: 300, use_global_interval: true, include_replies: true, include_retweets: true })
const touched = reactive({ username: false, interval: false })

watch(
  () => [props.open, props.account] as const,
  () => {
    if (!props.open) return
    form.username = props.account?.username || ''
    form.poll_interval_seconds = props.account?.poll_interval_seconds || props.account?.effective_poll_interval_seconds || props.defaultInterval
    form.use_global_interval = props.account ? props.account.poll_interval_seconds == null : true
    form.include_replies = props.account?.include_replies ?? true
    form.include_retweets = props.account?.include_retweets ?? true
    touched.username = false
    touched.interval = false
  },
  { immediate: true },
)

const cleanUsername = computed(() => form.username.trim().replace(/^@/, ''))
const usernameError = computed(() => {
  if (!cleanUsername.value) return '请输入 X 用户名'
  if (!/^[A-Za-z0-9_]{1,15}$/.test(cleanUsername.value)) return '用户名应为 1–15 位字母、数字或下划线'
  return ''
})
const intervalError = computed(() => {
  if (form.use_global_interval) return ''
  if (!Number.isFinite(form.poll_interval_seconds)) return '请输入有效数字'
  if (form.poll_interval_seconds < 15) return '轮询间隔不能少于 15 秒'
  if (form.poll_interval_seconds > 86_400) return '轮询间隔不能超过 24 小时'
  return ''
})

function submit() {
  touched.username = true
  touched.interval = true
  if ((!props.account && usernameError.value) || intervalError.value) return

  if (props.account) {
    emit('submit', {
      poll_interval_seconds: form.use_global_interval ? null : Number(form.poll_interval_seconds),
      include_replies: form.include_replies,
      include_retweets: form.include_retweets,
    })
  } else {
    emit('submit', {
      username: cleanUsername.value,
      poll_interval_seconds: form.use_global_interval ? null : Number(form.poll_interval_seconds),
      include_replies: form.include_replies,
      include_retweets: form.include_retweets,
    })
  }
}
</script>

<template>
  <BaseModal
    :open="open"
    :title="account ? `编辑 @${account.username}` : '添加监听账号'"
    :description="account ? '更新账号的独立轮询与内容采集策略' : '添加后，调度器将按设定频率抓取该用户的新内容'"
    @close="!loading && emit('close')"
  >
    <el-form id="account-editor" class="form-stack sentinel-form" label-position="top" @submit.prevent="submit">
      <el-alert v-if="serverError" :title="serverError" type="error" :closable="false" show-icon />

      <el-form-item label="X 用户名" required :error="touched.username ? usernameError : ''">
        <el-input v-model="form.username" :disabled="!!account" placeholder="例如 elonmusk" maxlength="16" clearable @blur="touched.username = true">
          <template #prefix><AtSign :size="16" /></template>
        </el-input>
        <small v-if="!account && !(touched.username && usernameError)" class="field__hint">无需输入 @，账号必须为公开账号</small>
      </el-form-item>

      <el-form-item label="独立轮询间隔" :error="touched.interval ? intervalError : ''">
        <div class="element-toggle-row">
          <span><strong>跟随系统默认频率</strong><small>系统设置改变后，该账号自动同步</small></span>
          <el-switch v-model="form.use_global_interval" />
        </div>
        <div class="element-number-row">
          <Clock3 :size="17" />
          <el-input-number v-model="form.poll_interval_seconds" :disabled="form.use_global_interval" :min="15" :max="86400" :step="15" controls-position="right" @blur="touched.interval = true" />
          <span>秒</span>
        </div>
        <small v-if="!intervalError" class="field__hint">建议 2–15 分钟；过于频繁可能触发 X 的请求限制</small>
        <div class="interval-presets">
          <el-button v-for="option in [60, 300, 600, 1800, 3600]" :key="option" size="small" :disabled="form.use_global_interval" :type="!form.use_global_interval && form.poll_interval_seconds === option ? 'primary' : ''" plain @click="form.poll_interval_seconds = option">
            {{ option < 3600 ? `${option / 60}分钟` : '1小时' }}
          </el-button>
        </div>
      </el-form-item>

      <div class="form-toggle-grid">
        <div class="element-toggle-row"><span><strong>包含转推</strong><small>采集该用户转发的内容</small></span><el-switch v-model="form.include_retweets" /></div>
        <div class="element-toggle-row"><span><strong>包含回复</strong><small>采集该用户回复的内容</small></span><el-switch v-model="form.include_replies" /></div>
      </div>
    </el-form>
    <template #footer>
      <el-button :disabled="loading" @click="emit('close')">取消</el-button>
      <el-button type="primary" :loading="loading" @click="submit">{{ account ? '保存更改' : '添加并监听' }}</el-button>
    </template>
  </BaseModal>
</template>
