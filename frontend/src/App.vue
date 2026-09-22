<script setup lang="ts">
import { RouterView } from 'vue-router'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import type { ThemeConfig } from 'ant-design-vue/es/config-provider/context'
const theme: ThemeConfig = {
  token: {
    colorPrimary: '#a64e35', colorInfo: '#a64e35', colorSuccess: '#3d705a',
    colorWarning: '#946421', colorError: '#b84035', colorText: '#292c29',
    colorTextSecondary: '#72756c', colorBgBase: '#ffffff', colorBgLayout: '#f7f7f2',
    colorBorder: '#dcded5', colorBorderSecondary: '#e8e9e2', borderRadius: 6,
    fontFamily: '"Helvetica Neue", "PingFang SC", "Microsoft YaHei", sans-serif',
    fontSize: 14, controlHeight: 38,
  },

}
</script>

<template>
  <a-config-provider :locale="zhCN" :theme="theme">
    <RouterView />
  </a-config-provider>
</template>
