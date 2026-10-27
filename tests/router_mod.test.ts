import { expect, test } from 'claude-code/testing'

const TABLE = {
  agents: {
    explore: { role: 'Explorer', model: 'haiku', effort: 'max' },
    'general-purpose': { role: 'Executor', model: 'sonnet', effort: 'high' },
    plan: { role: 'ExpertAdvisor', model: 'inherit', effort: 'inherit' },
  },
}

function engine(on: any, table: unknown, seen: any[], writes: string[] = []) {
  on('env.get', (_: any, e: any) => ({ value: e.name === 'TEAM_AGENT_ROUTING' ? '/r/routing.json' : undefined }))
  on('fs.read', () => (table === undefined ? { deny: 'missing' } : { value: JSON.stringify(table) }))
  on('fs.exists', () => ({ value: false }))
  on('fs.write', (_: any, e: any) => (writes.push(e.text), { value: undefined }))
  on('tool.call', (_: any, e: any) => {
    seen.push(e)
    return { result: {} }
  })
}

test('fills missing model and effort by agent type', async ($, on) => {
  const seen: any[] = []
  engine(on, TABLE, seen)
  await $.tool.call({ tool: 'Agent', prompt: 'p', description: 'd', subagent_type: 'Explore' })
  expect(seen[0].model).toBe('haiku')
  expect(seen[0].effort).toBe('max')
})

test('an omitted type is general-purpose; explicit values are kept', async ($, on) => {
  const seen: any[] = []
  engine(on, TABLE, seen)
  await $.tool.call({ tool: 'Agent', prompt: 'p', description: 'd', model: 'opus' })
  expect(seen[0].model).toBe('opus')
  expect(seen[0].effort).toBe('high')
})

test('explicit escalation values are kept; inherit and fork pass untouched', async ($, on) => {
  const seen: any[] = []
  engine(on, TABLE, seen)
  await $.tool.call({ tool: 'Agent', prompt: 'p', description: 'd', model: 'opus', effort: 'low' })
  await $.tool.call({ tool: 'Agent', prompt: 'p', description: 'd', subagent_type: 'Plan' })
  await $.tool.call({ tool: 'Agent', prompt: 'p', description: 'd', subagent_type: 'fork' })
  expect([seen[0].model, seen[0].effort]).toEqual(['opus', 'low'])
  expect([seen[1].model, seen[1].effort]).toEqual([undefined, undefined])
  expect([seen[2].model, seen[2].effort]).toEqual([undefined, undefined])
})

test('a missing routing table never blocks a dispatch', async ($, on) => {
  const seen: any[] = []
  engine(on, undefined, seen)
  const result = await $.tool.call({ tool: 'Agent', prompt: 'p', description: 'd', subagent_type: 'Explore' })
  expect(seen.length).toBe(1)
  expect(seen[0].model).toBe(undefined)
  expect('deny' in result && result.deny).toBeFalsy()
})

test('agent.spawn backstops the model and logs what the engine resolved', async ($, on) => {
  const seen: any[] = []
  const writes: string[] = []
  engine(on, TABLE, seen, writes)
  on('agent.spawn', (_: any, e: any) => ({ model: e.model === 'haiku' ? 'claude-haiku-5-5' : 'parent', agentId: 'a1' }))
  const spawned = await $.agent.spawn({ prompt: 'p', subagentType: 'Explore' })
  expect(spawned.model).toBe('claude-haiku-5-5')
  const logged = JSON.parse(writes[0].trim())
  expect([logged.role, logged.requested, logged.resolved, logged.agentId]).toEqual(['Explorer', 'haiku', 'claude-haiku-5-5', 'a1'])
})
