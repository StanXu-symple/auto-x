<script setup lang="ts">
defineProps<{ title: string; okText?: string }>()
defineEmits<{ confirm: [] }>()
</script>

<template><a-popconfirm :title="title" :ok-text="okText || '确认'" cancel-text="取消" @confirm="$emit('confirm')"><slot /></a-popconfirm></template>
