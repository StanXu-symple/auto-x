<script setup lang="ts">
import { onBeforeUnmount, reactive, watch } from 'vue'
import { articlesApi } from '@/services/api'

const props = defineProps<{ images: string[] }>()
const urls = reactive<Record<string, string>>({})
const failed = reactive<Record<string, boolean>>({})
let request = 0

function releaseUrls() {
  for (const [path, url] of Object.entries(urls)) {
    URL.revokeObjectURL(url)
    delete urls[path]
  }
}

watch(
  () => [...props.images],
  async (images) => {
    const current = ++request
    releaseUrls()
    for (const path of Object.keys(failed)) delete failed[path]
    await Promise.all(
      [...new Set(images)].map(async (path) => {
        try {
          const blob = await articlesApi.image(path)
          if (current === request) urls[path] = URL.createObjectURL(blob)
        } catch {
          if (current === request) failed[path] = true
        }
      }),
    )
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  request++
  releaseUrls()
})
</script>

<template>
  <span v-if="!images.length" class="muted">暂无图片</span>
  <div v-else class="article-media-gallery">
    <div
      v-for="(path, index) in images"
      :key="`${path}-${index}`"
      class="article-media-gallery__item"
    >
      <a-image
        v-if="urls[path]"
        :src="urls[path]"
        :alt="`文章图片 ${index + 1}`"
        :width="120"
        :height="120"
      />
      <span v-else class="muted">{{ failed[path] ? '图片加载失败' : '图片加载中…' }}</span>
      <small class="muted">图片 {{ index + 1 }}</small>
    </div>
  </div>
</template>

<style scoped>
.article-media-gallery {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(120px, 1fr));
  gap: 12px;
}
.article-media-gallery__item {
  display: grid;
  align-content: start;
  gap: 6px;
  min-height: 120px;
}
.article-media-gallery__item :deep(.ant-image-img) {
  object-fit: cover;
  border-radius: 4px;
}
.article-media-gallery__item small {
  font-size: 11px;
}
</style>
