<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Clock3, Info, RotateCcw, Save, Settings2, UsersRound, Workflow } from 'lucide-vue-next'
import { systemApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { useUiStore } from '@/stores/ui'
import type { SystemSettings } from '@/types'

const ui = useUiStore()
const settings = ref<SystemSettings | null>(null)
const loading = ref(true)
const saving = ref(false)
const settingsError = ref('')
const formError = ref('')
const form = reactive<SystemSettings>({ global_poll_interval_seconds: 300, max_concurrency: 5 })

const dirty = computed(() =>
  Boolean(settings.value && (
    form.global_poll_interval_seconds !== settings.value.global_poll_interval_seconds ||
    form.max_concurrency !== settings.value.max_concurrency
  )),
)

function applySettings(value: SystemSettings) {
  form.global_poll_interval_seconds = value.global_poll_interval_seconds
  form.max_concurrency = value.max_concurrency
}

async function load() {
  loading.value = true
  settingsError.value = ''
  try {
    const value = await systemApi.settings()
    settings.value = value
    applySettings(value)
  } catch (requestError) {
    settingsError.value = getErrorMessage(requestError, '无法加载系统配置')
  } finally {
    loading.value = false
  }
}

function validate() {
  if (!Number.isInteger(form.global_poll_interval_seconds) || form.global_poll_interval_seconds < 15 || form.global_poll_interval_seconds > 86_400) {
    return '默认轮询间隔必须是 15 到 86400 秒之间的整数'
  }
  if (!Number.isInteger(form.max_concurrency) || form.max_concurrency < 1 || form.max_concurrency > 100) {
    return '最大并发任务数必须是 1 到 100 之间的整数'
  }
  return ''
}

async function save() {
  formError.value = validate()
  if (formError.value) return
  saving.value = true
  try {
    const updated = await systemApi.updateSettings({ ...form })
    settings.value = updated
    applySettings(updated)
    ui.toast('系统设置已保存', 'success', '新的调度配置将在下一轮任务中生效')
  } catch (requestError) {
    formError.value = getErrorMessage(requestError, '保存设置失败')
  } finally {
    saving.value = false
  }
}

function reset() {
  if (settings.value) applySettings(settings.value)
  formError.value = ''
}

onMounted(load)
</script>

<template>
  <div class="settings-view page-stack">
    <section class="settings-intro" aria-labelledby="settings-heading">
      <span class="settings-intro__icon"><Settings2 :size="21" /></span>
      <div><h2 id="settings-heading">全局轮询设置</h2><p>统一控制未配置独立策略账号的轮询节奏与系统并发容量。</p></div>
      <span class="settings-intro__state">系统默认策略</span>
    </section>

    <div class="settings-workspace">
      <article class="panel settings-form-panel settings-form-panel--focused">
        <header class="panel__header"><div><h2>调度参数</h2><p>保存后由调度器在下一轮任务中读取</p></div><Workflow :size="19" /></header>

        <div v-if="loading" class="settings-form-skeleton" aria-label="正在加载设置"><span /><span /><span /></div>
        <div v-else-if="settingsError && !settings" class="settings-load-state">
          <strong>配置暂时不可用</strong><span>{{ settingsError }}</span><el-button @click="load">重新加载</el-button>
        </div>
        <el-form v-else class="form-stack sentinel-form" label-position="top" @submit.prevent="save">
          <el-alert v-if="formError" :title="formError" type="error" :closable="false" show-icon />
          <el-form-item label="默认轮询间隔">
            <div class="element-number-row"><Clock3 :size="17" /><el-input-number v-model="form.global_poll_interval_seconds" :min="15" :max="86400" :step="15" controls-position="right" /><span>秒</span></div>
            <small class="field__hint">允许范围为 15 秒至 24 小时，账号可通过独立策略覆盖此值。</small>
            <div class="interval-presets" aria-label="常用轮询间隔">
              <el-button v-for="option in [60, 300, 600, 1800, 3600]" :key="option" size="small" :type="form.global_poll_interval_seconds === option ? 'primary' : ''" plain @click="form.global_poll_interval_seconds = option">{{ option < 3600 ? `${option / 60}分钟` : '1小时' }}</el-button>
            </div>
          </el-form-item>
          <el-form-item label="最大并发任务">
            <div class="element-number-row"><Workflow :size="17" /><el-input-number v-model="form.max_concurrency" :min="1" :max="100" controls-position="right" /><span>个</span></div>
            <small class="field__hint">限制同时执行的轮询任务数量，调整时需考虑 CPU、网络和数据源容量。</small>
          </el-form-item>
          <div class="settings-form-panel__footer">
            <span v-if="dirty" class="unsaved-indicator"><span />有未保存的更改</span><span v-else class="saved-indicator">当前配置已同步</span>
            <el-button v-if="dirty" :disabled="saving" @click="reset"><RotateCcw :size="16" />撤销</el-button>
            <el-button type="primary" :loading="saving" :disabled="!dirty || !settings" @click="save"><Save v-if="!saving" :size="16" />保存设置</el-button>
          </div>
        </el-form>
      </article>

      <aside class="panel settings-scope-panel">
        <header class="panel__header"><div><h2>配置作用范围</h2><p>确认本页参数对任务调度的影响</p></div><Info :size="19" /></header>
        <div class="settings-scope-list">
          <div><span><UsersRound :size="17" /></span><p><strong>继承默认策略的账号</strong><small>未设置独立轮询间隔的活跃账号会使用这里的默认值。</small></p></div>
          <div><span><Clock3 :size="17" /></span><p><strong>下一轮调度生效</strong><small>保存不会中断正在运行的任务，新配置由后续调度读取。</small></p></div>
          <div><span><Workflow :size="17" /></span><p><strong>统一并发上限</strong><small>并发限制面向全局调度器，用于控制瞬时资源消耗。</small></p></div>
        </div>
      </aside>
    </div>
  </div>
</template>
