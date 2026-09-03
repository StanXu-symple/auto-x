const dateTimeFormatter = new Intl.DateTimeFormat('zh-CN', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})

const compactFormatter = new Intl.NumberFormat('zh-CN', {
  notation: 'compact',
  maximumFractionDigits: 1,
})

export const formatDateTime = (value?: string | null) => {
  if (!value) return '暂无'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '暂无' : dateTimeFormatter.format(date)
}

export const formatRelative = (value?: string | null) => {
  if (!value) return '暂无'
  const time = new Date(value).getTime()
  if (Number.isNaN(time)) return '暂无'
  const diff = Math.round((time - Date.now()) / 1000)
  const abs = Math.abs(diff)
  const formatter = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })
  if (abs < 60) return formatter.format(diff, 'second')
  if (abs < 3600) return formatter.format(Math.round(diff / 60), 'minute')
  if (abs < 86_400) return formatter.format(Math.round(diff / 3600), 'hour')
  return formatter.format(Math.round(diff / 86_400), 'day')
}

export const formatNumber = (value?: number | null) => compactFormatter.format(value || 0)

export const formatDuration = (milliseconds?: number | null) => {
  if (milliseconds == null) return '暂无'
  if (milliseconds < 1000) return `${Math.round(milliseconds)} ms`
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)} s`
  return `${Math.floor(milliseconds / 60_000)}m ${Math.round((milliseconds % 60_000) / 1000)}s`
}

export const formatInterval = (seconds?: number | null) => {
  if (!seconds) return '暂无'
  if (seconds < 60) return `${seconds} 秒`
  if (seconds < 3600) return `${seconds / 60 % 1 ? (seconds / 60).toFixed(1) : seconds / 60} 分钟`
  return `${seconds / 3600 % 1 ? (seconds / 3600).toFixed(1) : seconds / 3600} 小时`
}

export const formatBytes = (bytes?: number | null) => {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index > 1 ? 1 : 0)} ${units[index]}`
}

export const formatUptime = (seconds?: number | null) => {
  if (!seconds) return '暂无'
  const days = Math.floor(seconds / 86_400)
  const hours = Math.floor((seconds % 86_400) / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  return days ? `${days} 天 ${hours} 小时` : hours ? `${hours} 小时 ${minutes} 分钟` : `${minutes} 分钟`
}

export const tweetTime = (tweet: { posted_at?: string }) => tweet.posted_at
