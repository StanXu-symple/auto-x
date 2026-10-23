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

export interface AgentOutputPreview {
  reply: string
  title: string
  excerpt: string
  content: string
}

/** Read complete and partial string values from the streamed AgentResult JSON. */
export function extractAgentOutputPreview(raw: string): AgentOutputPreview {
  const preview: AgentOutputPreview = { reply: '', title: '', excerpt: '', content: '' }
  const fields = new Set<keyof AgentOutputPreview>(['reply', 'title', 'excerpt', 'content'])
  let inString = false
  let isKey = false
  let expectingKey = false
  let pendingKey = ''
  let key = ''
  let escape = false
  let unicode = ''
  let highSurrogate = -1

  function append(value: string) {
    if (isKey) key += value
    else if (fields.has(pendingKey as keyof AgentOutputPreview))
      preview[pendingKey as keyof AgentOutputPreview] += value
  }

  for (const char of raw) {
    if (!inString) {
      if (char === '{' || char === ',') {
        expectingKey = true
        pendingKey = ''
      } else if (char === '}') {
        expectingKey = false
        pendingKey = ''
      } else if (char === ':') expectingKey = false
      else if (char === '"') {
        inString = true
        isKey = expectingKey
        key = ''
        highSurrogate = -1
      }
      continue
    }
    if (unicode) {
      unicode += char
      if (unicode.length === 5) {
        const code = Number.parseInt(unicode.slice(1), 16)
        if (Number.isFinite(code)) {
          if (code >= 0xd800 && code <= 0xdbff) highSurrogate = code
          else if (code >= 0xdc00 && code <= 0xdfff && highSurrogate >= 0) {
            append(String.fromCodePoint(0x10000 + (highSurrogate - 0xd800) * 0x400 + code - 0xdc00))
            highSurrogate = -1
          } else {
            if (highSurrogate >= 0) append('\uFFFD')
            append(String.fromCodePoint(code))
            highSurrogate = -1
          }
        }
        unicode = ''
      }
      continue
    }
    if (escape) {
      escape = false
      if (char === 'u') unicode = 'u'
      else {
        const decoded: Record<string, string> = {
          '"': '"',
          '\\': '\\',
          '/': '/',
          b: '\b',
          f: '\f',
          n: '\n',
          r: '\r',
          t: '\t',
        }
        append(decoded[char] ?? char)
      }
      continue
    }
    if (char === '\\') {
      escape = true
      continue
    }
    if (char === '"') {
      inString = false
      if (isKey) pendingKey = key
      else pendingKey = ''
      continue
    }
    if (highSurrogate >= 0) {
      append('\uFFFD')
      highSurrogate = -1
    }
    append(char)
  }
  return preview
}

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
