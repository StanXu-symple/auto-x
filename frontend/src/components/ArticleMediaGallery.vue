<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, watch } from 'vue'
import { articlesApi, tweetsApi } from '@/services/api'
import type { ArticleSourceScreenshot } from '@/types'

const props = defineProps<{
  images: string[]
  sourceScreenshot?: ArticleSourceScreenshot | null
  removable?: boolean
  disabled?: boolean
}>()
const emit = defineEmits<{
  removeImage: [path: string]
  removeSourceScreenshot: []
}>()
type GalleryMedia = { key: string; label: string; alt: string } & (
  { type: 'screenshot'; tweetId: string } | { type: 'image'; path: string }
)
const media = computed<GalleryMedia[]>(() => [
  ...(props.sourceScreenshot
    ? [
        {
          key: `screenshot:${props.sourceScreenshot.tweet_id}`,
          type: 'screenshot' as const,
          tweetId: props.sourceScreenshot.tweet_id,
          label: '原帖截图 · 自动关联',
          alt: '原帖截图',
        },
      ]
    : []),
  ...props.images.map((path, index) => ({
    key: `image:${path}`,
    type: 'image' as const,
    path,
    label: `图片 ${index + 1}`,
    alt: `文章图片 ${index + 1}`,
  })),
])
const urls = reactive<Record<string, string>>({})
const failed = reactive<Record<string, boolean>>({})
let request = 0

function removeMedia(item: GalleryMedia) {
  if (!props.removable || props.disabled) return
  if (item.type === 'screenshot') emit('removeSourceScreenshot')
  else emit('removeImage', item.path)
}

function releaseUrls() {
  for (const [path, url] of Object.entries(urls)) {
    URL.revokeObjectURL(url)
    delete urls[path]
  }
}

watch(
  [media, () => props.sourceScreenshot?.sha256, () => props.sourceScreenshot?.captured_at],
  async ([items]) => {
    const current = ++request
    releaseUrls()
    for (const path of Object.keys(failed)) delete failed[path]
    await Promise.all(
      [...new Map(items.map((item) => [item.key, item])).values()].map(async (item) => {
        try {
          const blob = await (item.type === 'screenshot'
            ? tweetsApi.screenshot(item.tweetId)
            : articlesApi.image(item.path))
          if (current === request) urls[item.key] = URL.createObjectURL(blob)
        } catch {
          if (current === request) failed[item.key] = true
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
  <span v-if="!media.length" class="muted">暂无图片</span>
  <div v-else class="article-media-gallery">
    <div
      v-for="(item, index) in media"
      :key="`${item.key}-${index}`"
      class="article-media-gallery__item"
    >
      <a-image
        v-if="urls[item.key]"
        :src="urls[item.key]"
        :alt="item.alt"
        :width="120"
        :height="120"
      />
      <span v-else class="muted">{{ failed[item.key] ? '图片加载失败' : '图片加载中…' }}</span>
      <small class="muted">{{ item.label }}</small>
      <a-button
        v-if="removable"
        type="text"
        size="small"
        danger
        :disabled="disabled"
        :aria-label="item.type === 'screenshot' ? '删除原帖截图' : `删除${item.alt}`"
        @click="removeMedia(item)"
      >
        删除图片
      </a-button>
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
