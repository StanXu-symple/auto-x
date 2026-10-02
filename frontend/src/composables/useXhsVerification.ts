import { onScopeDispose, readonly, ref } from 'vue'

interface VerificationResult {
  required: boolean
  image?: string
  version?: string
}

export function useXhsVerification(
  fetchVerification: (version?: string) => Promise<VerificationResult>,
  intervalMs = 1000,
) {
  const open = ref(false)
  const image = ref('')
  let version = ''
  let timer: ReturnType<typeof setTimeout> | undefined
  let generation = 0
  let active = false
  let disposed = false
  let inFlight = false

  function clearImage() {
    open.value = false
    image.value = ''
    version = ''
  }

  function schedule(current: number) {
    if (!active || current !== generation) return
    timer = setTimeout(() => void poll(current), intervalMs)
  }

  async function poll(current: number) {
    if (!active || current !== generation) return
    // A previous session may still have a request in flight after stop/start.
    if (inFlight) {
      schedule(current)
      return
    }
    inFlight = true
    try {
      const result = await fetchVerification(version || undefined)
      if (!active || current !== generation) return
      if (!result.required) {
        clearImage()
        return
      }
      if (result.image) {
        image.value = result.image
        version = result.version || ''
      } else if (result.version && result.version !== version) {
        // Never show the previous challenge while a new image is unavailable.
        image.value = ''
        version = ''
      }
      open.value = true
    } catch {
      // A transient polling failure should not interrupt the publishing request.
    } finally {
      inFlight = false
      schedule(current)
    }
  }

  function stop() {
    active = false
    generation += 1
    clearTimeout(timer)
    timer = undefined
    clearImage()
  }

  function start() {
    stop()
    if (disposed) return
    active = true
    schedule(generation)
  }

  onScopeDispose(() => {
    disposed = true
    stop()
  })

  return { open: readonly(open), image: readonly(image), start, stop }
}
