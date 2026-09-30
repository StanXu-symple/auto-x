<script setup lang="ts">
import type { Article, ArticlePublishChannel } from '@/types'
import ArticleMediaGallery from '@/components/ArticleMediaGallery.vue'

defineProps<{ article: Article; channel?: ArticlePublishChannel }>()
</script>

<template>
  <a-form layout="vertical" class="article-preview">
    <a-form-item label="标题">
      <a-input :value="article.title" readonly />
    </a-form-item>
    <a-form-item
      label="摘要"
      :extra="channel === 'xhs' ? '小红书仅发送标题、正文和图片，摘要不会发布。' : undefined"
    >
      <a-textarea :value="article.excerpt || ''" :rows="2" readonly placeholder="未填写摘要" />
    </a-form-item>
    <a-form-item label="正文">
      <a-textarea :value="article.content" :rows="10" readonly />
    </a-form-item>
    <a-form-item label="媒体图片">
      <ArticleMediaGallery :images="article.images || []" />
    </a-form-item>
  </a-form>
</template>
