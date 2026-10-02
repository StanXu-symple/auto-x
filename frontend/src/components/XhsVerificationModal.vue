<script setup lang="ts">
defineProps<{ open: boolean; image: string }>()
</script>

<template>
  <a-modal
    :open="open"
    title="完成小红书安全验证"
    :footer="null"
    :closable="false"
    :mask-closable="false"
    :keyboard="false"
  >
    <p class="muted">请使用已登录当前账号的小红书 App 扫描二维码，验证完成后会继续发布。</p>
    <div class="verify-image">
      <img v-if="image" :src="image" alt="小红书安全验证二维码" />
      <span v-else>正在获取验证二维码…</span>
    </div>
  </a-modal>
</template>
