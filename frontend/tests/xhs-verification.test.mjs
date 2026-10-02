import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { after, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { effectScope } from 'vue'
import ts from 'typescript'

// Use the installed TypeScript compiler and Node test runner; no browser/test dependency.
const tmpRoot = new URL('../node_modules/.tmp/', import.meta.url)
mkdirSync(tmpRoot, { recursive: true })
const directory = mkdtempSync(fileURLToPath(new URL('xhs-verification-', tmpRoot)))
const compiledPath = `${directory}/useXhsVerification.mjs`
const source = readFileSync(
  new URL('../src/composables/useXhsVerification.ts', import.meta.url),
  'utf8',
)
writeFileSync(
  compiledPath,
  ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  }).outputText,
)
const { useXhsVerification } = await import(pathToFileURL(compiledPath).href)
after(() => rmSync(directory, { recursive: true, force: true }))

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}

function setup(t, fetcher) {
  const timers = new Map()
  let timerId = 0
  t.mock.method(globalThis, 'setTimeout', (callback) => {
    timers.set(++timerId, callback)
    return timerId
  })
  t.mock.method(globalThis, 'clearTimeout', (id) => timers.delete(id))
  const scope = effectScope()
  const verification = scope.run(() => useXhsVerification(fetcher))
  t.after(() => scope.stop())
  async function tick() {
    const next = timers.entries().next().value
    assert.ok(next, 'A polling timer should be scheduled')
    timers.delete(next[0])
    next[1]()
    await Promise.resolve()
  }
  return { ...verification, timers, tick, scope }
}

test('serial polling displays QR and sends its version without overlapping slow requests', async (t) => {
  const pending = deferred()
  const versions = []
  const state = setup(t, (version) => {
    versions.push(version)
    return versions.length === 1
      ? pending.promise
      : Promise.resolve({ required: true, version: 'v1' })
  })
  state.start()
  assert.equal(versions.length, 0)
  await state.tick()
  assert.equal(versions.length, 1)
  assert.equal(state.timers.size, 0)
  pending.resolve({ required: true, image: 'qr-one', version: 'v1' })
  await Promise.resolve()
  assert.equal(state.open.value, true)
  assert.equal(state.image.value, 'qr-one')
  await state.tick()
  assert.deepEqual(versions, [undefined, 'v1'])
  assert.equal(state.image.value, 'qr-one')
})

test('required=false clears the image and version before the next challenge', async (t) => {
  const versions = []
  const results = [
    { required: true, image: 'qr-one', version: 'v1' },
    { required: false },
    { required: true, image: 'qr-two', version: 'v2' },
  ]
  const state = setup(t, async (version) => {
    versions.push(version)
    return results.shift()
  })
  state.start()
  await state.tick()
  await state.tick()
  assert.equal(state.open.value, false)
  assert.equal(state.image.value, '')
  await state.tick()
  assert.deepEqual(versions, [undefined, 'v1', undefined])
  assert.equal(state.image.value, 'qr-two')
})

test('stop clears QR and discards a late response without reopening or rescheduling', async (t) => {
  const pending = deferred()
  let calls = 0
  const state = setup(t, async () =>
    ++calls === 1 ? { required: true, image: 'qr-one', version: 'v1' } : pending.promise,
  )
  state.start()
  await state.tick()
  await state.tick()
  state.stop()
  assert.equal(state.open.value, false)
  assert.equal(state.image.value, '')
  pending.resolve({ required: true, image: 'late-qr', version: 'late-version' })
  await Promise.resolve()
  await Promise.resolve()
  assert.equal(state.open.value, false)
  assert.equal(state.image.value, '')
  assert.equal(state.timers.size, 0)
})

test('restart waits for an older request and rejects its result', async (t) => {
  const pending = deferred()
  const versions = []
  const state = setup(t, (version) => {
    versions.push(version)
    return versions.length === 1 ? pending.promise : Promise.resolve({ required: false })
  })
  state.start()
  await state.tick()
  state.stop()
  state.start()
  await state.tick()
  assert.equal(versions.length, 1)
  pending.resolve({ required: true, image: 'old-qr', version: 'old-version' })
  await Promise.resolve()
  assert.equal(state.open.value, false)
  await state.tick()
  assert.deepEqual(versions, [undefined, undefined])
  assert.equal(state.timers.size, 1)
})

test('scope disposal stops pending polling and prevents a later restart', async (t) => {
  const pending = deferred()
  const state = setup(t, () => pending.promise)
  state.start()
  await state.tick()
  state.scope.stop()
  pending.resolve({ required: true, image: 'late-qr', version: 'v1' })
  await Promise.resolve()
  state.start()
  assert.equal(state.open.value, false)
  assert.equal(state.image.value, '')
  assert.equal(state.timers.size, 0)
})

test('polling recovers after a failed request', async (t) => {
  let calls = 0
  const state = setup(t, async () => {
    if (++calls === 1) throw new Error('temporary network failure')
    return { required: true, image: 'qr-one', version: 'v1' }
  })
  state.start()
  await state.tick()
  assert.equal(state.open.value, false)
  await state.tick()
  assert.equal(state.image.value, 'qr-one')
})

test('a new challenge without an image clears the old image and requests a full refresh', async (t) => {
  const versions = []
  const results = [
    { required: true, image: 'qr-one', version: 'v1' },
    { required: true, version: 'v2' },
    { required: true, image: 'qr-two', version: 'v2' },
  ]
  const state = setup(t, async (version) => {
    versions.push(version)
    return results.shift()
  })
  state.start()
  await state.tick()
  await state.tick()
  assert.equal(state.open.value, true)
  assert.equal(state.image.value, '')
  await state.tick()
  assert.deepEqual(versions, [undefined, 'v1', undefined])
  assert.equal(state.image.value, 'qr-two')
})
