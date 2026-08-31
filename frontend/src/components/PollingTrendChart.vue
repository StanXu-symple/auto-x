<script setup lang="ts">
import { computed } from 'vue'
import type { PollingTrendPoint } from '@/types'

const props = defineProps<{ points: PollingTrendPoint[] }>()

const chart = computed(() => {
  const width = 760
  const height = 220
  const paddingX = 14
  const paddingTop = 14
  const paddingBottom = 28
  const max = Math.max(1, ...props.points.map((point) => point.success + point.failed))
  const usableHeight = height - paddingTop - paddingBottom
  const step = props.points.length > 1 ? (width - paddingX * 2) / (props.points.length - 1) : width - paddingX * 2
  const y = (value: number) => paddingTop + usableHeight - (value / max) * usableHeight
  const success = props.points.map((point, index) => `${paddingX + index * step},${y(point.success)}`).join(' ')
  const failed = props.points.map((point, index) => `${paddingX + index * step},${y(point.failed)}`).join(' ')
  const area = props.points.length ? `${paddingX},${paddingTop + usableHeight} ${success} ${paddingX + (props.points.length - 1) * step},${paddingTop + usableHeight}` : ''
  return { width, height, paddingX, paddingTop, paddingBottom, max, usableHeight, step, y, success, failed, area }
})

const labels = computed(() => {
  if (!props.points.length) return []
  const indexes = new Set([0, Math.floor((props.points.length - 1) / 2), props.points.length - 1])
  return props.points
    .map((point, index) => ({ point, index }))
    .filter(({ index }) => indexes.has(index))
    .map(({ point, index }) => ({
      x: chart.value.paddingX + index * chart.value.step,
      text: point.label || point.time || (point.timestamp ? new Date(point.timestamp).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''),
    }))
})
</script>

<template>
  <div v-if="points.length" class="trend-chart">
    <svg :viewBox="`0 0 ${chart.width} ${chart.height}`" role="img" aria-label="轮询趋势图">
      <defs>
        <linearGradient id="chart-area" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="#7c5cff" stop-opacity="0.32" />
          <stop offset="1" stop-color="#7c5cff" stop-opacity="0" />
        </linearGradient>
      </defs>
      <g class="trend-chart__grid">
        <line v-for="line in 4" :key="line" x1="14" x2="746" :y1="14 + ((line - 1) * chart.usableHeight) / 3" :y2="14 + ((line - 1) * chart.usableHeight) / 3" />
      </g>
      <polygon :points="chart.area" fill="url(#chart-area)" />
      <polyline :points="chart.success" class="trend-chart__line trend-chart__line--success" />
      <polyline v-if="points.some((point) => point.failed)" :points="chart.failed" class="trend-chart__line trend-chart__line--failed" />
      <g v-for="(point, index) in points" :key="index">
        <circle :cx="chart.paddingX + index * chart.step" :cy="chart.y(point.success)" r="3.5" class="trend-chart__point" />
        <title>{{ point.label || point.time }}：成功 {{ point.success }}，失败 {{ point.failed }}</title>
      </g>
      <text v-for="label in labels" :key="label.x" :x="label.x" y="216" class="trend-chart__label" :text-anchor="label.x === 14 ? 'start' : label.x > 730 ? 'end' : 'middle'">{{ label.text }}</text>
    </svg>
  </div>
  <div v-else class="chart-empty"><span />等待轮询数据</div>
</template>
