<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/PageHeader.vue'
import XAuthorizationView from '@/views/XAuthorizationView.vue'
import XhsView from '@/views/XhsView.vue'
import AiDataSourceView from '@/views/AiDataSourceView.vue'

const route = useRoute()
const router = useRouter()
const normalizeTab = (key: unknown) => (key === 'xhs' || key === 'ai' ? key : 'x')
const tab = computed(() => normalizeTab(route.query.tab))

function changeTab(key: string | number) {
  const next = normalizeTab(key)
  if (next === tab.value) return
  void router.replace({
    path: '/data-sources',
    query: { ...route.query, tab: next },
    hash: route.hash,
  })
}
</script>

<template>
  <div class="page-stack">
    <PageHeader
      eyebrow="CONFIGURATION / SOURCES"
      title="数据源"
      description="管理 X、小红书与 AI 数据源、访问凭据和连接状态"
    />
    <a-tabs :active-key="tab" :destroy-inactive-tab-pane="false" @change="changeTab">
      <a-tab-pane key="x" tab="X数据源">
        <XAuthorizationView embedded />
      </a-tab-pane>
      <a-tab-pane key="xhs" tab="小红书数据源">
        <XhsView embedded />
      </a-tab-pane>
      <a-tab-pane key="ai" tab="AI数据源">
        <AiDataSourceView />
      </a-tab-pane>
    </a-tabs>
  </div>
</template>
