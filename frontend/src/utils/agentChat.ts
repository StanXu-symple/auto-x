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
