<script setup lang="ts">
import { computed } from 'vue'
import StatusPill from '@/components/StatusPill.vue'
import { skillSourceLabel, skillTypeLabel } from '@/utils/skills'
import type { AiSkill } from '@/types'

const props = defineProps<{ open: boolean; skill: AiSkill | null }>()
defineEmits<{ 'update:open': [value: boolean] }>()
const source = computed(() => props.skill?.source_metadata)
const outputSchema = computed(() =>
  props.skill?.output_schema && Object.keys(props.skill.output_schema).length
    ? JSON.stringify(props.skill.output_schema, null, 2)
    : '',
)
</script>

<template>
  <a-modal
    :open="open"
    :z-index="1100"
    :title="skill ? `${skillTypeLabel(skill)}详情 · ${skill.name}` : '详情'"
    :width="880"
    @update:open="$emit('update:open', $event)"
  >
    <template v-if="skill">
      <a-descriptions bordered size="small" :column="{ xs: 1, sm: 2 }">
        <a-descriptions-item label="名称" :span="2">{{ skill.name }}</a-descriptions-item>
        <a-descriptions-item label="来源">{{ skillSourceLabel(skill) }}</a-descriptions-item>
        <a-descriptions-item label="状态">
          <StatusPill :value="skill.is_active" :label="skill.is_active ? '启用' : '停用'" />
        </a-descriptions-item>
        <a-descriptions-item v-if="skill.version != null" label="版本" :span="2">{{
          skill.version
        }}</a-descriptions-item>
        <a-descriptions-item label="说明" :span="2">{{
          skill.description || '暂无说明'
        }}</a-descriptions-item>
        <template v-if="source">
          <a-descriptions-item label="GitHub 仓库" :span="2">{{
            source.repository
          }}</a-descriptions-item>
          <a-descriptions-item label="分支 / 标签 / Commit" :span="2">{{
            source.ref
          }}</a-descriptions-item>
          <a-descriptions-item label="Skill 目录" :span="2">{{
            source.path || '/'
          }}</a-descriptions-item>
          <a-descriptions-item label="Commit" :span="2"
            ><span class="skill-detail-version">{{ source.commit }}</span></a-descriptions-item
          >
        </template>
      </a-descriptions>
      <h3 class="skill-prompt-heading">{{ skillTypeLabel(skill) }}内容</h3>
      <pre class="skill-prompt" tabindex="0" :aria-label="`${skillTypeLabel(skill)}内容`">{{
        skill.instructions || '暂无提示词'
      }}</pre>
      <template v-if="outputSchema">
        <h3 class="skill-prompt-heading">输出格式要求</h3>
        <pre class="skill-prompt" tabindex="0" aria-label="输出格式要求">{{ outputSchema }}</pre>
      </template>
    </template>
    <a-empty v-else description="未找到详情，请刷新后重试" />
    <template #footer>
      <a-button @click="$emit('update:open', false)">关闭</a-button>
    </template>
  </a-modal>
</template>

<style scoped>
.skill-prompt-heading {
  margin: 20px 0 10px;
  font-size: 14px;
  font-weight: 600;
}
.skill-prompt {
  max-height: 50vh;
  margin: 0;
  padding: 16px;
  overflow: auto;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--paper-soft);
  color: var(--ink);
  font-family: var(--font);
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.skill-detail-version {
  word-break: break-all;
}
:deep(.ant-descriptions-item-content) {
  overflow-wrap: anywhere;
}
</style>
