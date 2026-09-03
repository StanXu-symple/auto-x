<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ page: number; pageSize: number; total: number }>()
const emit = defineEmits<{ change: [page: number] }>()

const totalPages = computed(() => Math.max(1, Math.ceil(props.total / props.pageSize)))
</script>

<template>
  <div class="pagination">
    <span>共 {{ total }} 条，{{ totalPages }} 页</span>
    <el-pagination
      background
      layout="prev, pager, next"
      :current-page="page"
      :page-size="pageSize"
      :total="total"
      :pager-count="5"
      @current-change="emit('change', $event)"
    />
  </div>
</template>
