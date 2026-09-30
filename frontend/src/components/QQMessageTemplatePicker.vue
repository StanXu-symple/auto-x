<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { SaveOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { QQMessageTemplate } from '@/types'

const props = defineProps<{ messageTemplate: string; templateVariables: Record<string, string> }>()
const emit = defineEmits<{
  'update:messageTemplate': [value: string]
  'update:templateVariables': [value: Record<string, string>]
  saved: []
}>()
const options = ref<QQMessageTemplate[]>([])
const loading = ref(false)
const applying = ref(false)
const open = ref(false)
const saving = ref(false)
const name = ref('')
const savedBody = ref('')
const savedVariables = ref<Record<string, string>>({})
let alive = true
onBeforeUnmount(() => {
  alive = false
  request++
})
let request = 0
let search = ''
let page = 1
let total = 0
async function load(value = '', append = false) {
  const token = ++request
  loading.value = true
  if (!append) {
    search = value
    page = 1
    options.value = []
  }
  const next = append ? page + 1 : 1
  try {
    const result = await qqApi.messageTemplates({
      page: next,
      page_size: 15,
      search: search || undefined,
    })
    if (token !== request) return
    options.value = append ? [...options.value, ...result.items] : result.items
    page = next
    total = result.total
  } catch (error) {
    if (token === request) message.error(getErrorMessage(error, '无法读取消息模板'))
  } finally {
    if (token === request) loading.value = false
  }
}
function scroll(event: UIEvent) {
  const el = event.target as HTMLElement
  if (
    !loading.value &&
    options.value.length < total &&
    el.scrollTop + el.clientHeight >= el.scrollHeight - 24
  )
    void load(search, true)
}
async function apply(id: number) {
  applying.value = true
  try {
    const row = await qqApi.messageTemplate(id)
    if (!alive) return
    emit('update:messageTemplate', row.message_template)
    emit('update:templateVariables', { ...row.template_variables })
    message.success(`已填入「${row.name}」，保存群目标后生效`)
  } catch (error) {
    message.error(getErrorMessage(error, '应用消息模板失败'))
  } finally {
    applying.value = false
  }
}
function quickSave() {
  if (!props.messageTemplate.trim()) return message.warning('请先填写消息模板内容')
  name.value = ''
  savedBody.value = props.messageTemplate
  savedVariables.value = { ...props.templateVariables }
  open.value = true
}
async function save() {
  if (saving.value) return
  if (!name.value.trim()) return message.warning('请填写模板名称')
  saving.value = true
  try {
    await qqApi.createMessageTemplate({
      name: name.value.trim(),
      message_template: savedBody.value,
      template_variables: savedVariables.value,
    })
    open.value = false
    emit('saved')
    message.success('消息模板已保存，可在模板库中选择')
  } catch (error) {
    message.error(getErrorMessage(error, '保存消息模板失败'))
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="template-heading">
    <strong>消息模板</strong
    ><a-button size="small" :disabled="applying" @click="quickSave"
      ><SaveOutlined /> 保存模板</a-button
    >
  </div>
  <a-select
    :value="null"
    show-search
    :filter-option="false"
    :loading="loading || applying"
    :disabled="applying"
    placeholder="选择已保存的消息模板（可搜索）"
    :options="options.map((row) => ({ label: row.name, value: row.id }))"
    style="width: 100%; margin-bottom: 8px"
    @search="(value: string) => load(value)"
    @dropdown-visible-change="
      (visible: boolean) => {
        if (visible) load()
      }
    "
    @popup-scroll="scroll"
    @select="apply"
  />
  <p class="muted template-hint">
    选择后替换当前正文和自定义变量，仍可继续编辑；保存群目标后生效。
  </p>
  <a-modal
    v-model:open="open"
    title="保存消息模板"
    ok-text="保存模板"
    cancel-text="取消"
    :confirm-loading="saving"
    @ok="save"
  >
    <a-form layout="vertical"
      ><a-form-item label="模板名称" required
        ><a-input
          v-model:value="name"
          :maxlength="100"
          placeholder="例如：每日资讯推送"
          @press-enter="save" /></a-form-item
    ></a-form>
  </a-modal>
</template>

<style scoped>
.template-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 8px;
}
.template-hint {
  font-size: 12px;
  margin: 0 0 10px;
}
</style>
