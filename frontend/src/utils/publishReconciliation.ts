import type { ArticlePublishReconcileDecision } from '@/types'

export const reconciliationLabels: Record<ArticlePublishReconcileDecision, string> = {
  published: '人工确认已发布',
  not_published: '人工确认未发布',
  uncertain: '人工核对后仍不确定',
}

export function safeXhsPublishedUrl(value?: string | null): string | undefined {
  if (!value || value.length > 2048) return undefined
  try {
    const url = new URL(value)
    const host = url.hostname.toLowerCase()
    if (
      url.protocol === 'https:' &&
      !url.username &&
      !url.password &&
      (!url.port || url.port === '443') &&
      url.pathname.replace(/\//g, '').length > 0 &&
      !/[\s\u0000-\u001f]/.test(value) &&
      ['xiaohongshu.com', 'xhslink.com'].some(
        (domain) => host === domain || host.endsWith(`.${domain}`),
      )
    )
      return url.href
  } catch {
    // Never open an invalid URL as a link.
  }
  return undefined
}
