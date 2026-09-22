<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { HomeOutlined, MonitorOutlined, CodeOutlined, TeamOutlined, MessageOutlined, FileSearchOutlined, KeyOutlined, FormOutlined, BookOutlined, BellOutlined, ScheduleOutlined, PictureOutlined, SettingOutlined } from '@ant-design/icons-vue'
defineEmits<{ navigate: [] }>()
const route = useRoute()
const groups = [
  { label: '工作台', items: [{ path: '/dashboard', label: '概览', icon: HomeOutlined }, { path: '/monitoring', label: '运行监控', icon: MonitorOutlined }, { path: '/runtime-logs', label: '实时日志', icon: CodeOutlined }] },
  { label: '监听与采集', items: [{ path: '/accounts', label: '监听账号', icon: TeamOutlined }, { path: '/tweets', label: '内容流', icon: MessageOutlined }, { path: '/polling-logs', label: '轮询记录', icon: FileSearchOutlined }] },
  { label: '创作与发布', items: [{ path: '/ai-writing', label: 'AI 创作', icon: FormOutlined }, { path: '/articles', label: '文章管理', icon: BookOutlined }, { path: '/qq-notifications', label: 'QQ 推送', icon: BellOutlined }, { path: '/qq-tasks', label: '定时任务', icon: ScheduleOutlined }, { path: '/xhs', label: '小红书', icon: PictureOutlined }] },
  { label: '配置', items: [{ path: '/x-authorization', label: 'X 数据源', icon: KeyOutlined }, { path: '/settings', label: '系统设置', icon: SettingOutlined }] },
]
const selected = computed(() => [route.path])
</script>
<template>
  <nav aria-label="主导航">
    <a-menu mode="inline" :selected-keys="selected" class="console-menu">
      <a-menu-item-group v-for="group in groups" :key="group.label" :title="group.label">
        <a-menu-item v-for="item in group.items" :key="item.path">
          <template #icon><component :is="item.icon" /></template>
          <RouterLink :to="item.path" @click="$emit('navigate')">{{ item.label }}</RouterLink>
        </a-menu-item>
      </a-menu-item-group>
    </a-menu>
  </nav>
</template>
