<script setup lang="ts">
import { ArrowLeft, Radar } from 'lucide-vue-next'
</script>

<template>
  <main class="standalone-state">
    <div class="standalone-state__glow" />
    <div class="brand-mark brand-mark--large"><Radar :size="32" /></div>
    <span>404</span>
    <h1>这个页面不在监听范围内</h1>
    <p>你访问的地址不存在，可能已被移动或删除。</p>
    <RouterLink class="button button--primary" to="/dashboard"><ArrowLeft :size="17" />返回仪表盘</RouterLink>
  </main>
</template>
