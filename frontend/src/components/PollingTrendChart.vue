<script setup lang="ts">
import { computed, ref, useId } from 'vue'
import type { PollingRun } from '@/types'

const props = defineProps<{ runs: PollingRun[] }>()
const gradientId = `polling-${useId()}`
const selected = ref<number | null>(null)
const points = computed(() => {
  const buckets = new Map<number, { time: number; success: number; failed: number }>()
  for (const run of props.runs) {
    const time = Math.floor(new Date(run.started_at).getTime() / 60_000) * 60_000
    if (!Number.isFinite(time)) continue
    const point = buckets.get(time) || { time, success: 0, failed: 0 }
    if (run.status === 'success') point.success++
    else if (['error', 'failed', 'rate_limited', 'lock_lost'].includes(run.status)) point.failed++
    buckets.set(time, point)
  }
  return [...buckets.values()].sort((a, b) => a.time - b.time)
})
const top = computed(() =>
  Math.max(
    4,
    Math.ceil(Math.max(...points.value.map((p) => Math.max(p.success, p.failed)), 0) / 4) * 4,
  ),
)
const x = (index: number) => {
  const first = points.value[0]?.time || 0
  const last = points.value[points.value.length - 1]?.time || first
  return last === first ? 310 : 42 + ((points.value[index]!.time - first) / (last - first)) * 536
}
const y = (value: number) => 202 - (value / top.value) * 170
const timeLabel = (time: number) =>
  new Date(time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
const fullLabel = (time: number) =>
  new Date(time).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
// Horizontal control points keep each segment smooth and within its actual value range.
function line(key: 'success' | 'failed') {
  return points.value
    .map((point, index) => {
      if (!index) return `M ${x(index)} ${y(point[key])}`
      const previous = points.value[index - 1]!
      const middle = (x(index - 1) + x(index)) / 2
      return `C ${middle} ${y(previous[key])}, ${middle} ${y(point[key])}, ${x(index)} ${y(point[key])}`
    })
    .join(' ')
}
const successLine = computed(() => line('success'))
const failedLine = computed(() => line('failed'))
const area = computed(() =>
  points.value.length > 1
    ? `${successLine.value} L ${x(points.value.length - 1)} 202 L ${x(0)} 202 Z`
    : '',
)
const current = computed(() => (selected.value == null ? null : points.value[selected.value]))
const totals = computed(() =>
  points.value.reduce(
    (sum, point) => ({ success: sum.success + point.success, failed: sum.failed + point.failed }),
    { success: 0, failed: 0 },
  ),
)
const labels = computed(() =>
  points.value
    .map((point, index) => ({ ...point, index }))
    .filter(
      (_, index) =>
        index === 0 ||
        index === points.value.length - 1 ||
        (points.value.length > 4 && index === Math.floor((points.value.length - 1) / 2)),
    ),
)
</script>

<template>
  <div v-if="points.length" class="polling-chart">
    <div class="polling-chart__legend">
      <span
        ><i />成功 <strong>{{ totals.success }}</strong></span
      ><span
        ><i class="failure" />失败 <strong>{{ totals.failed }}</strong></span
      ><small>次 / 分钟</small>
    </div>
    <div class="polling-chart__plot" @mouseleave="selected = null">
      <svg viewBox="0 0 620 244" role="group" aria-label="最近轮询趋势，按分钟统计成功与失败次数">
        <defs>
          <linearGradient :id="gradientId" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="#3d705a" stop-opacity=".2" />
            <stop offset="100%" stop-color="#3d705a" stop-opacity=".015" />
          </linearGradient>
        </defs>
        <g v-for="tick in 5" :key="tick" class="polling-chart__grid">
          <line x1="42" x2="578" :y1="y(((tick - 1) * top) / 4)" :y2="y(((tick - 1) * top) / 4)" />
          <text x="28" :y="y(((tick - 1) * top) / 4) + 4" text-anchor="end">
            {{ ((tick - 1) * top) / 4 }}
          </text>
        </g>
        <path :d="area" :fill="`url(#${gradientId})`" />
        <path :d="successLine" class="polling-chart__line" />
        <path :d="failedLine" class="polling-chart__line polling-chart__line--failed" />
        <g v-for="(point, index) in points" :key="point.time">
          <circle
            :cx="x(index)"
            :cy="y(point.success)"
            :r="selected === index ? 5 : 3"
            class="polling-chart__dot"
          />
          <circle
            :cx="x(index)"
            :cy="y(point.failed)"
            :r="selected === index ? 5 : 3"
            class="polling-chart__dot polling-chart__dot--failed"
          />
        </g>
        <line
          v-if="current && selected != null"
          :x1="x(selected)"
          :x2="x(selected)"
          y1="24"
          y2="202"
          class="polling-chart__cursor"
        />
        <text
          v-for="point in labels"
          :key="point.time"
          :x="x(point.index)"
          y="232"
          text-anchor="middle"
          class="polling-chart__time"
        >
          {{ timeLabel(point.time) }}
        </text>
        <rect
          v-for="(point, index) in points"
          :key="`hit-${point.time}`"
          :x="index === 0 ? 32 : (x(index - 1) + x(index)) / 2"
          y="20"
          :width="
            (index === points.length - 1 ? 588 : (x(index) + x(index + 1)) / 2) -
            (index === 0 ? 32 : (x(index - 1) + x(index)) / 2)
          "
          height="188"
          fill="transparent"
          tabindex="0"
          role="button"
          :aria-label="`${fullLabel(point.time)}，成功 ${point.success} 次，失败 ${point.failed} 次`"
          class="polling-chart__hit"
          @mouseenter="selected = index"
          @focus="selected = index"
          @blur="selected = null"
          @click="selected = index"
          @keydown.enter="selected = index"
          @keydown.space.prevent="selected = index"
        />
      </svg>
      <div
        v-if="current && selected != null"
        class="polling-chart__tooltip"
        :style="{ left: `${Math.max(20, Math.min(80, (x(selected) / 620) * 100))}%` }"
      >
        <strong>{{ fullLabel(current.time) }}</strong
        ><span>成功 {{ current.success }} 次 · 失败 {{ current.failed }} 次</span>
      </div>
    </div>
    <div class="polling-chart__footer">
      <span>最近 {{ runs.length }} 条记录 · 按分钟聚合</span
      ><span>{{ points.length === 1 ? '等待更多数据点' : '每 60 秒刷新' }}</span>
    </div>
  </div>
  <div v-else class="polling-chart__empty"><a-empty description="等待轮询数据" /></div>
</template>

<style scoped>
.polling-chart {
  --chart-success: #3d705a;
  --chart-failed: #b56a50;
}
.polling-chart__legend {
  display: flex;
  align-items: center;
  gap: 24px;
  font-size: 12px;
  color: var(--muted);
}
.polling-chart__legend span {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.polling-chart__legend i {
  width: 18px;
  height: 3px;
  border-radius: 3px;
  background: var(--chart-success);
}
.polling-chart__legend i.failure {
  background: var(--chart-failed);
}
.polling-chart__legend strong {
  color: var(--ink);
  font: 500 14px var(--mono);
}
.polling-chart__legend small {
  margin-left: auto;
  font-size: 11px;
}
.polling-chart__plot {
  position: relative;
  margin-top: 12px;
}
.polling-chart__plot svg {
  display: block;
  width: 100%;
  overflow: visible;
}
.polling-chart__grid line {
  stroke: var(--line);
  stroke-dasharray: 3 6;
  stroke-opacity: 0.7;
}
.polling-chart__grid text,
.polling-chart__time {
  fill: var(--muted);
  font: 11px var(--mono);
}
.polling-chart__line {
  fill: none;
  stroke: var(--chart-success);
  stroke-width: 2.5;
  stroke-linecap: round;
  vector-effect: non-scaling-stroke;
}
.polling-chart__line--failed {
  stroke: var(--chart-failed);
  stroke-width: 2;
  stroke-dasharray: 5 4;
}
.polling-chart__dot {
  fill: var(--chart-success);
  stroke: var(--paper);
  stroke-width: 2;
}
.polling-chart__dot--failed {
  fill: var(--chart-failed);
}
.polling-chart__cursor {
  stroke: var(--muted);
  stroke-dasharray: 3 5;
  opacity: 0.5;
  pointer-events: none;
}
.polling-chart__hit {
  cursor: crosshair;
}
.polling-chart__hit:focus-visible {
  outline: 1px solid var(--sage);
  outline-offset: 0;
  rx: 4px;
}
.polling-chart__tooltip {
  position: absolute;
  top: 0;
  transform: translateX(-50%);
  display: grid;
  gap: 4px;
  padding: 10px 14px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: 0 6px 22px #292c2910;
  font-size: 11px;
  white-space: nowrap;
  pointer-events: none;
}
.polling-chart__tooltip span {
  color: var(--muted);
  font-variant-numeric: tabular-nums;
}
.polling-chart__footer {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  margin-top: 8px;
  font-size: 11px;
  color: var(--muted);
}
.polling-chart__empty {
  min-height: 250px;
  display: grid;
  place-items: center;
}
@media (max-width: 600px) {
  .polling-chart__legend {
    gap: 14px;
  }
  .polling-chart__footer {
    font-size: 10px;
  }
}
</style>
