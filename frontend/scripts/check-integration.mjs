import assert from 'node:assert/strict'
import { existsSync } from 'node:fs'
import { createServer as createHttpServer } from 'node:http'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'
import { createServer as createViteServer } from 'vite'

const frontend = fileURLToPath(new URL('../', import.meta.url))
const root = resolve(frontend, '..')
const localPython = resolve(root, process.platform === 'win32' ? '.venv/Scripts/python.exe' : '.venv/bin/python')
const python = process.env.PYTHON || (existsSync(localPython) ? localPython : 'python')
function validate(samples) {
  const result = spawnSync(python, [resolve(frontend, 'scripts/check_contracts.py')], {
    cwd: root, input: JSON.stringify(samples), encoding: 'utf8', timeout: 30000, maxBuffer: 2 * 1024 * 1024,
  })
  if (result.error) throw new Error(`Cannot run Python acceptance (${result.error.code ?? result.error.name}). Set PYTHON to an interpreter with requirements-dev.txt installed.`)
  if (result.status !== 0) throw new Error(result.stderr.trim() || 'Python acceptance failed')
  return JSON.parse(result.stdout)
}
async function collect(createMockApi) {
  const samples = []
  for (const scenario of ['success', 'tests-failed', 'stopped', 'save-failure', 'schedule-error']) {
    let now = Date.parse('2026-10-22T00:00:00Z')
    const api = createMockApi({ now: () => now, stepMs: 100, storage: null })
    api.setScenario(scenario)
    const request = { task_description: 'Verify null greeting', source_project: 'java-null-greeting', max_steps: 20 }
    const created = await api.createTask(request)
    const states = [await api.getTask(created.task_id)]
    now += 150
    states.push(await api.getTask(created.task_id))
    now += 10000
    states.push(await api.getTask(created.task_id))
    samples.push({ scenario, request, created, states, steps: await api.getSteps(created.task_id), experiences: await api.listExperiences(100, 0) })
  }
  return samples
}

// A bounded HTTP fixture, not a FastAPI replacement. It lives only for this check.
function fixtureServer() {
  let sample
  let records = []
  let fault = ''
  let received
  const server = createHttpServer(async (request, response) => {
    const send = (status, value) => { response.writeHead(status, { 'Content-Type': 'application/json' }); response.end(JSON.stringify(value)) }
    const url = new URL(request.url, 'http://fixture.local')
    if (fault === '500') return send(500, { detail: 'private storage details must not reach UI' })
    if (url.pathname === '/api/experiences') return send(200, fault === 'shape' ? { items: records } : records.slice(Number(url.searchParams.get('offset') ?? 0), Number(url.searchParams.get('offset') ?? 0) + Number(url.searchParams.get('limit') ?? 20)))
    if (request.method === 'POST' && url.pathname === '/api/tasks') {
      let body = ''
      for await (const chunk of request) body += chunk
      received = JSON.parse(body)
      return send(202, sample.created)
    }
    if (url.pathname === `/api/tasks/${sample.created.task_id}`) return send(200, sample.states.at(-1))
    if (url.pathname === `/api/tasks/${sample.created.task_id}/steps`) return send(200, sample.steps)
    return send(404, { detail: 'Not found' })
  })
  return { server, use(value, experiences) { sample = value; records = experiences; fault = '' }, fail(value) { fault = value }, received: () => received }
}

let loader
let vite
const fixture = fixtureServer()
const previousCwd = process.cwd()
const previousProxy = process.env.API_PROXY_TARGET
try {
  process.chdir(frontend)
  loader = await createViteServer({ root: frontend, configFile: false, server: { middlewareMode: true }, optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'error' })
  const { createMockApi } = await loader.ssrLoadModule('/src/api/mock.ts')
  const { createHttpApi } = await loader.ssrLoadModule('/src/api/http.ts')
  const samples = await collect(createMockApi)
  const checked = validate(samples)
  // Prove the checker actually rejects drift, rather than merely accepting fixtures.
  for (const corrupt of [
    sample => { sample.states[2].status = 'unknown' },
    sample => { sample.steps.steps[0].result.call_id = sample.created.task_id },
    sample => { sample.states[2].result.tests_passed = false },
  ]) {
    const invalid = structuredClone(samples)
    corrupt(invalid[0])
    assert.throws(() => validate(invalid), /Contract\/SQLite acceptance failed: ValidationError/)
  }
  await new Promise((resolve, reject) => { fixture.server.once('error', reject); fixture.server.listen(0, '127.0.0.1', resolve) })
  const target = `http://127.0.0.1:${fixture.server.address().port}`
  process.env.API_PROXY_TARGET = target
  vite = await createViteServer({ root: frontend, configFile: resolve(frontend, 'vite.config.ts'), mode: 'integration', server: { host: '127.0.0.1', port: 0, open: false }, optimizeDeps: { noDiscovery: true, include: [] }, logLevel: 'error' })
  assert.equal(vite.config.server.proxy['/api'].target, target)
  await vite.listen()
  const base = `http://127.0.0.1:${vite.httpServer.address().port}`
  // Simulate the browser's resolution of same-origin relative requests.
  const client = createHttpApi('', (url, options) => fetch(new URL(url, base), options))
  for (const sample of samples) {
    fixture.use(sample, checked.sqlite_records)
    assert.deepEqual(await client.createTask(sample.request), sample.created)
    assert.deepEqual(fixture.received(), sample.request)
    assert.deepEqual(await client.getTask(sample.created.task_id), sample.states.at(-1))
    assert.deepEqual(await client.getSteps(sample.created.task_id), sample.steps)
    assert.deepEqual(await client.listExperiences(6, 0), checked.sqlite_records.slice(0, 6))
    assert.deepEqual(await client.listExperiences(6, 5), checked.sqlite_records.slice(5))
  }
  fixture.fail('shape')
  await assert.rejects(client.listExperiences(), error => error.status === 502)
  fixture.fail('500')
  await assert.rejects(client.listExperiences(), error => error.status === 500 && !error.message.includes('private'))
  console.log(`PASS: ${checked.scenarios} scenarios, ${checked.checked_objects} public-model objects, SQLite save/reopen/search/paging (${checked.sqlite_records.length} rows).`)
  console.log('PASS: drift rejection, all four real HTTP adapter methods through Vite proxy, malformed/500 responses.')
  console.log('This validates contracts and local integration infrastructure; it does not execute Agent, LLM or Maven.')
} catch (error) {
  console.error(error.message)
  process.exitCode = 1
} finally {
  await vite?.close()
  await loader?.close()
  fixture.server.closeAllConnections()
  await new Promise(resolve => fixture.server.close(resolve))
  if (previousProxy === undefined) delete process.env.API_PROXY_TARGET
  else process.env.API_PROXY_TARGET = previousProxy
  process.chdir(previousCwd)
}
