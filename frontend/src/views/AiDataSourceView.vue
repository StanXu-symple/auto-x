<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ReloadOutlined } from '@ant-design/icons-vue'
import { message } from 'ant-design-vue'
import { aiDataSourceApi } from '@/services/api'
import { getErrorMessage } from '@/services/http'
import type { AiDataSourceStatus } from '@/types'

const loading = reactive({ source: false })
const source = ref<AiDataSourceStatus | null>(null)
const sourceModels = ref<string[]>([])
const sourceForm = reactive({
  name: 'OpenAI',
  base_url: 'https://api.openai.com/v1',
  model: 'gpt-5.6-terra',
  api_key: '',
})

async function loadSource() {
  loading.source = true
  try {
    source.value = await aiDataSourceApi.status()
    if (source.value.configured) {
      sourceForm.name = source.value.name || 'OpenAI'
      sourceForm.base_url = source.value.base_url || ''
      sourceForm.model = source.value.model || ''
    }
  } catch (e) {
    message.error(getErrorMessage(e, '无法加载 AI 数据源'))
  } finally {
    loading.source = false
  }
}
async function saveSource() {
  if (!sourceForm.name || !sourceForm.base_url || !sourceForm.model)
    return message.warning('请填写名称、Base URL 和模型')
  const apiKey = sourceForm.api_key.trim()
  if (!source.value?.configured && !apiKey) return message.warning('首次配置请填写 API Key')
  try {
    source.value = await aiDataSourceApi.save({
      ...sourceForm,
      api_key: apiKey || undefined,
      protocol: 'openai_responses',
    })
    sourceForm.api_key = ''
    message.success('AI 数据源已加密保存')
  } catch (e) {
    message.error(getErrorMessage(e, '保存失败'))
  }
}
async function testSource() {
  try {
    const result = await aiDataSourceApi.test()
    sourceModels.value = result.models
    result.valid ? message.success(result.message) : message.warning(result.message)
    await loadSource()
  } catch (e) {
    message.error(getErrorMessage(e, '测试失败'))
  }
}

onMounted(loadSource)
</script>

<template>
  <div class="page-stack">
    <div class="toolbar">
      <span class="toolbar__hint">配置 AI 模型、访问凭据并测试连接</span>
      <a-button :loading="loading.source" @click="loadSource"><ReloadOutlined /> 刷新状态</a-button>
    </div>
    <a-card :bordered="false"
      ><a-alert
        type="info"
        show-icon
        message="API Key 仅用于后端调用，保存后以密码占位符表示已配置，不显示真实密钥。"
      /><a-form layout="vertical" style="max-width: 700px; margin-top: 18px"
        ><a-form-item label="名称"><a-input v-model:value="sourceForm.name" /></a-form-item
        ><a-form-item label="Base URL"><a-input v-model:value="sourceForm.base_url" /></a-form-item
        ><a-form-item label="模型"
          ><a-input v-model:value="sourceForm.model" />
          <div v-if="sourceModels.length" class="muted">
            可用模型：{{ sourceModels.join('、') }}
          </div></a-form-item
        ><a-form-item
          label="API Key"
          :extra="
            source?.configured
              ? 'API Key 已保存，留空保留原密钥，填写新值后保存可替换。'
              : '首次配置请填写 API Key。'
          "
          ><a-input-password
            v-model:value="sourceForm.api_key"
            :placeholder="
              source?.configured ? '••••••••（已保存，输入新值可替换）' : '首次配置必填'
            " /></a-form-item
        ><a-space
          ><a-button type="primary" :loading="loading.source" @click="saveSource"
            >保存数据源</a-button
          ><a-button :loading="loading.source" @click="testSource">测试连接</a-button></a-space
        ></a-form
      ></a-card
    >
  </div>
</template>
