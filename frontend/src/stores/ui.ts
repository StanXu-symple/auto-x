import { ref } from 'vue'
import { defineStore } from 'pinia'
import { message } from 'ant-design-vue'

export type ToastTone = 'success' | 'error' | 'warning' | 'info'

export const useUiStore = defineStore('ui', () => {
  const sidebarOpen = ref(false)

  function toast(title: string, tone: ToastTone = 'info', description?: string) {
    message.open({ type: tone, content: description ? `${title} · ${description}` : title, duration: tone === 'error' ? 6 : 4 })
  }

  return { sidebarOpen, toast }
})
