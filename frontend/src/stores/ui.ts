import { ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'

export type ToastTone = 'success' | 'error' | 'warning' | 'info'

export const useUiStore = defineStore('ui', () => {
  const sidebarOpen = ref(false)

  function toast(title: string, tone: ToastTone = 'info', description?: string) {
    ElMessage({
      type: tone,
      message: description ? `${title} · ${description}` : title,
      duration: tone === 'error' ? 6000 : 4000,
      showClose: true,
    })
  }

  return { sidebarOpen, toast }
})
