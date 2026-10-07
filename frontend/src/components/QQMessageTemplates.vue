<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons-vue'
import { message, Modal } from 'ant-design-vue'
import { qqApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import { usePagedTable } from '@/composables/usePagedTable'
import { formatDateTime } from '@/utils/format'
import type { QQMessageTemplate, QQMessageTemplatePayload, QQPlaceholder } from '@/types'

const props = defineProps<{ revision: number }>()
const emit = defineEmits<{ changed: [] }>()
const search = ref('')
const { rows, loading, pagination, load, reset, change } = usePagedTable(
  (query) => qqApi.messageTemplates({ ...query, search: search.value || undefined }),
  '无法加载消息模板',
)
defineExpose({ refresh: load })
const open = ref(false)
const saving = ref(false)
const editing = ref<number | null>(null)
const fields = ref<QQPlaceholder[]>([])
const form = reactive<QQMessageTemplatePayload>({
  name: '',
  message_template: '',
  template_variables: {},
})
const variables = ref<{ key: string; value: string }[]>([])
async function edit(row?: QQMessageTemplate) {
  try {
    fields.value = await qqApi.placeholders()
    editing.value = row?.id || null
    Object.assign(form, {
      name: row?.name || '',
      message_template: row?.message_template || '',
      template_variables: { ...row?.template_variables },
    })
    variables.value = Object.entries(form.template_variables).map(([key, value]) => ({
      key,
      value,
    }))
    open.value = true
  } catch (error) {
    message.error(getErrorMessage(error, '无法读取占位符'))
  }
}
async function save() {
  if (saving.value) return
  if (!form.name.trim() || !form.message_template.trim())
    return message.warning('请填写模板名称和正文')
  const values: Record<string, string> = {}
  for (const item of variables.value) {
    const key = item.key.trim()
    if (!/^[a-z][a-z0-9_]{1,31}$/.test(key))
      return message.warning('变量名须为 2–32 位小写字母、数字或下划线，以字母开头')
    if (Object.prototype.hasOwnProperty.call(values, key))
      return message.warning('自定义变量名称不能重复')
    values[key] = item.value
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      message_template: form.message_template,
      template_variables: values,
    }
    editing.value
      ? await qqApi.updateMessageTemplate(editing.value, payload)
      : await qqApi.createMessageTemplate(payload)
    open.value = false
    message.success('消息模板已保存')
    await load()
    emit('changed')
  } catch (error) {
    message.error(getErrorMessage(error, '保存模板失败'))
  } finally {
    saving.value = false
  }
}
function remove(row: QQMessageTemplate) {
  Modal.confirm({
    title: `删除消息模板「${row.name}」？`,
    content: '已使用此模板的群目标会保留自己的消息内容。',
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      try {
        await qqApi.removeMessageTemplate(row.id)
        await load()
        emit('changed')
        message.success('消息模板已删除')
      } catch (error) {
        message.error(getErrorMessage(error, '删除模板失败'))
        throw error
      }
    },
  })
}
watch(() => props.revision, load)
onMounted(load)
</script>

<template>
  <div class="toolbar">
    <a-input
      v-model:value="search"
      allow-clear
      placeholder="搜索模板名称"
      style="width: 240px"
      @press-enter="reset"
    /><a-space
      ><a-button :loading="loading" @click="load"><ReloadOutlined /> 刷新</a-button
      ><a-button type="primary" @click="edit()"><PlusOutlined /> 新增模板</a-button></a-space
    >
  </div>
  <a-table
    :data-source="rows"
    :loading="loading"
    :pagination="pagination"
    row-key="id"
    @change="change"
  >
    <a-table-column title="模板名称" data-index="name" />
    <a-table-column title="消息内容"
      ><template #default="{ record }"
        ><p class="template-preview">{{ record.message_template }}</p></template
      ></a-table-column
    >
    <a-table-column title="更新时间"
      ><template #default="{ record }">{{
        formatDateTime(record.updated_at)
      }}</template></a-table-column
    >
    <a-table-column title="操作"
      ><template #default="{ record }"
        ><a-space
          ><a-button type="link" aria-label="编辑消息模板" @click="edit(record)"
            ><EditOutlined /></a-button
          ><a-button type="text" danger aria-label="删除消息模板" @click="remove(record)"
            ><DeleteOutlined /></a-button></a-space></template
    ></a-table-column>
  </a-table>
  <a-modal
    v-model:open="open"
    :title="editing ? '编辑消息模板' : '新增消息模板'"
    ok-text="保存"
    cancel-text="取消"
    :confirm-loading="saving"
    @ok="save"
  >
    <a-form layout="vertical">
      <a-form-item label="模板名称" required
        ><a-input v-model:value="form.name" :maxlength="100" placeholder="例如：每日资讯推送"
      /></a-form-item>
      <a-form-item label="消息内容" required extra="点击占位符追加到正文。"
        ><a-textarea v-model:value="form.message_template" :rows="7" :maxlength="2000" show-count />
        <div class="template-fields">
          <a-button
            v-for="field in fields"
            :key="field.id"
            size="small"
            @click="form.message_template += field.placeholder"
            >{{ field.placeholder }}</a-button
          >
        </div></a-form-item
      >
      <a-form-item
        label="自定义变量（可选）"
        extra="例如变量名 topic、内容 科技，正文中使用 {topic}。"
      >
        <div v-for="(item, index) in variables" :key="index" class="variable-row">
          <a-input v-model:value="item.key" placeholder="变量名" :maxlength="32" /><a-input
            v-model:value="item.value"
            placeholder="替换内容"
          /><a-button
            type="text"
            danger
            aria-label="移除自定义变量"
            @click="variables.splice(index, 1)"
            ><DeleteOutlined
          /></a-button>
        </div>
        <a-button type="dashed" block @click="variables.push({ key: '', value: '' })"
          ><PlusOutlined /> 添加变量</a-button
        >
      </a-form-item>
    </a-form>
  </a-modal>
</template>

<style scoped>
.template-preview {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  max-width: 560px;
  margin: 0;
}
.variable-row {
  display: grid;
  grid-template-columns: 1fr 2fr auto;
  gap: 8px;
  margin-bottom: 8px;
}
.template-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 12px;
}
</style>
