import { computed, reactive, ref, shallowRef } from 'vue'
import { message } from 'ant-design-vue'
import { getErrorMessage } from '@/services/http'
import type { PaginatedResponse } from '@/types'

export interface PageQuery { page: number; page_size: number }

export function usePagedTable<T>(fetchPage: (query: PageQuery) => Promise<PaginatedResponse<T>>, errorMessage: string) {
  const rows = shallowRef<T[]>([])
  const total = ref(0)
  const loading = ref(false)
  const query = reactive<PageQuery>({ page: 1, page_size: 15 })
  let request = 0
  const pagination = computed(() => ({
    current: query.page, pageSize: query.page_size, total: total.value,
    showSizeChanger: true, pageSizeOptions: ['15', '30', '50', '100'],
    showQuickJumper: true, showTotal: (count: number) => `共 ${count} 条`,
  }))
  async function load() {
    const current = ++request
    loading.value = true
    try {
      let result = await fetchPage({ ...query })
      if (current !== request) return
      // Deleting the final row on a page returns to the last available page.
      const lastPage = Math.max(1, Math.ceil(result.total / query.page_size))
      if (query.page > lastPage) {
        query.page = lastPage
        result = await fetchPage({ ...query })
        if (current !== request) return
      }
      rows.value = result.items
      total.value = result.total
    } catch (error) {
      if (current === request) message.error(getErrorMessage(error, errorMessage))
    } finally {
      if (current === request) loading.value = false
    }
  }
  function change(page: { current?: number; pageSize?: number }) {
    const size = page.pageSize || query.page_size
    query.page = size !== query.page_size ? 1 : page.current || 1
    query.page_size = size
    void load()
  }
  function reset() { query.page = 1; return load() }
  return { rows, total, loading, query, pagination, load, change, reset }
}
