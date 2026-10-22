export function mentionAt(value: string, caret: number) {
  const match = /(?:^|\s)@([^@\s]*)$/.exec(value.slice(0, caret))
  return match
    ? { start: caret - match[1]!.length - 1, end: caret, query: match[1]!.toLowerCase() }
    : null
}

export function removeMention(value: string, mention: { start: number; end: number }) {
  return value.slice(0, mention.start) + value.slice(mention.end)
}

export const isActiveTurn = (turn: { status: string }) =>
  ['queued', 'running'].includes(turn.status)

export function createAgentRequestId(): string {
  const crypto = globalThis.crypto
  if (crypto?.randomUUID) return crypto.randomUUID()
  if (!crypto?.getRandomValues) throw new Error('当前浏览器无法生成消息标识，请更换浏览器后重试')

  // randomUUID requires HTTPS; getRandomValues also works on HTTP deployments.
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6]! & 0x0f) | 0x40
  bytes[8] = (bytes[8]! & 0x3f) | 0x80
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}
