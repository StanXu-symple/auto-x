<script setup lang="ts">
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    width?: 'small' | 'medium' | 'large'
    closeOnBackdrop?: boolean
  }>(),
  { width: 'medium', closeOnBackdrop: true },
)

const emit = defineEmits<{ close: [] }>()
const widths = { small: '410px', medium: '520px', large: '760px' }
</script>

<template>
  <el-dialog
    :model-value="open"
    :width="widths[props.width]"
    :close-on-click-modal="closeOnBackdrop"
    :close-on-press-escape="true"
    :show-close="true"
    align-center
    class="sentinel-dialog"
    @close="emit('close')"
  >
    <template #header>
      <div class="sentinel-dialog__heading"><h2>{{ title }}</h2><p v-if="description">{{ description }}</p></div>
    </template>
    <slot />
    <template v-if="$slots.footer" #footer><slot name="footer" /></template>
  </el-dialog>
</template>
