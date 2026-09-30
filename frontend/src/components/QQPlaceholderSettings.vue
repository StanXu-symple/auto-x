<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { QQPlaceholder, QQPlaceholderField } from '@/types'

const emit = defineEmits<{ changed: [] }>()
const rows = ref<QQPlaceholder[]>([])
const fields = ref<QQPlaceholderField[]>([])
const loading = ref(false)
const saving = ref(false)
const open = ref(false)
const editing = ref<QQPlaceholder | null>(null)
const error = ref('')
const form = reactive({ placeholder: '', source_field: '' })
const defaults = new Set(['{author}', '{username}', '{text}', '{url}', '{posted_at}', '{title}'])
const options = computed(() => fields.value.map(field => ({ value: field.value, label: `${field.category} · ${field.label} (${field.value})` })))
function sourceLabel(value: string) { const field = fields.value.find(item => item.value === value); return field ? `${field.label} (${value})` : value }
async function load() {
  loading.value = true
  error.value = ''
  try { [rows.value, fields.value] = await Promise.all([qqApi.placeholders(), qqApi.placeholderFields()]) }
  catch (e) { error.value = getErrorMessage(e, '读取占位符配置失败') }
  finally { loading.value = false }
}
async function edit(row?: QQPlaceholder) {
  // Always request the complete backend field catalog when opening the editor.
  loading.value = true
  try {
    fields.value = await qqApi.placeholderFields()
    editing.value = row || null
    Object.assign(form, { placeholder: row?.placeholder || '', source_field: row?.source_field || '' })
    open.value = true
  } catch (e) { message.error(getErrorMessage(e, '无法读取可选原始字段，请重试')) }
  finally { loading.value = false }
}
async function save() {
  if (saving.value) return
  const placeholder = form.placeholder.trim()
  if (!/^\{[a-z][a-z0-9_]{0,63}\}$/.test(placeholder)) return message.warning('请使用单花括号，如 {likes}，名称以小写字母开头，仅含小写字母、数字和下划线')
  if (!fields.value.some(field => field.value === form.source_field)) return message.warning('请从下拉列表选择原始字段')
  saving.value = true
  try {
    const payload = { placeholder, source_field: form.source_field }
    editing.value ? await qqApi.updatePlaceholder(editing.value.id, payload) : await qqApi.createPlaceholder(payload)
    open.value = false
    message.success('占位符已保存，新生成的投递将使用此映射')
    await load()
    emit('changed')
  } catch (e) { message.error(getErrorMessage(e, '保存占位符失败')) }
  finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <div class="toolbar"><span class="toolbar__hint">配置消息模板占位符与内容流字段的对应关系</span><a-space><a-button :loading="loading" @click="load"><ReloadOutlined /> 刷新</a-button><a-button type="primary" @click="edit()"><PlusOutlined /> 新增占位符</a-button></a-space></div>
  <a-alert v-if="error" type="error" show-icon :message="error" />
  <a-table :data-source="rows" :loading="loading" row-key="id" :pagination="false">
    <a-table-column title="占位符"><template #default="{record}"><code>{{ record.placeholder }}</code></template></a-table-column>
    <a-table-column title="原始字段"><template #default="{record}">{{ sourceLabel(record.source_field) }}</template></a-table-column>
    <a-table-column title="操作" :width="100"><template #default="{record}"><a-button type="link" @click="edit(record)"><EditOutlined /> 编辑</a-button></template></a-table-column>
  </a-table>
  <p class="muted">原始字段完整列出内容流接口字段；原文链接和推送标题为系统生成字段。修改后用于新生成的投递，已入队消息保持原内容。</p>
  <a-modal v-model:open="open" :title="editing ? '编辑占位符' : '新增占位符'" ok-text="保存" cancel-text="取消" :confirm-loading="saving" @ok="save">
    <a-form layout="vertical">
      <a-form-item label="占位符" required :extra="editing && defaults.has(editing.placeholder) ? '默认占位符保留名称，可修改字段映射。' : '使用单花括号包裹小写名称，如 {likes}。正在使用的占位符不可改名。'"><a-input v-model:value="form.placeholder" placeholder="例如 {likes}" :disabled="!!editing && defaults.has(editing.placeholder)" /></a-form-item>
      <a-form-item label="原始字段" required extra="选择内容流字段或系统生成字段，不支持手动填写。"><a-select v-model:value="form.source_field" show-search option-filter-prop="label" :options="options" placeholder="请选择原始字段" /></a-form-item>
    </a-form>
  </a-modal>
</template>
