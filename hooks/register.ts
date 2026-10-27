// team-agent router mod for Claude Code.
//
// Reads ~/.team-agent/claude-code/routing.json (or $TEAM_AGENT_ROUTING), the
// table claude_code_adapter.py `sync` writes, and for every subagent dispatch:
//   tool.call (Agent)  fills in `model` and `effort` the call left out, for the
//                      requested agent type; values the main agent passed (a
//                      user's per-request choice, an escalation rung) are kept;
//   agent.spawn        backstops the model for spawns that bypass the tool;
//                      records what the engine resolved, for verification.
// `/team-agent` shows the table and the recent dispatches. Any failure leaves
// the call as it was: this mod never blocks a dispatch.
import type { Register } from 'claude-code'

type Route = { role?: string; model?: string; effort?: string }
type Table = { agents?: Record<string, Route> }
type Dispatch = { at: string; type: string; role: string; requested?: string; resolved?: string; route_effort?: string; agentId?: string }

const EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max'] as const
type Effort = (typeof EFFORTS)[number]
const SKIP_TYPES = new Set(['fork'])
const LOG_LIMIT = 200

let cache: { table?: Table; at: number; path?: string } = { at: 0 }
const recent: Dispatch[] = []

async function routingPath($: any): Promise<string | undefined> {
  const override = await $.env.get('TEAM_AGENT_ROUTING')
  if (override) return override
  const home = (await $.env.get('USERPROFILE')) ?? (await $.env.get('HOME'))
  return home ? `${home}/.team-agent/claude-code/routing.json` : undefined
}

async function loadTable($: any): Promise<Table | undefined> {
  if (cache.table && Date.now() - cache.at < 5000) return cache.table
  const path = await routingPath($)
  try {
    const table = path ? (JSON.parse(await $.fs.read(path)) as Table) : undefined
    cache = { table, at: Date.now(), path }
  } catch {
    cache = { table: undefined, at: Date.now(), path }
  }
  return cache.table
}

function routeFor(table: Table | undefined, type: string | undefined): Route | undefined {
  const key = (type || 'general-purpose').toLowerCase()
  if (SKIP_TYPES.has(key)) return undefined
  return table?.agents?.[key]
}

const usable = (value: string | undefined): value is string => !!value && value !== 'inherit'

async function remember($: any, entry: Dispatch): Promise<void> {
  recent.unshift(entry)
  recent.length = Math.min(recent.length, 20)
  const path = (await routingPath($))?.replace(/routing\.json$/, 'dispatch-log.jsonl')
  if (!path) return
  try {
    const old = (await $.fs.exists(path)) ? String(await $.fs.read(path)).split('\n').filter(Boolean) : []
    const lines = [...old, JSON.stringify(entry)].slice(-LOG_LIMIT)
    await $.fs.write(path, lines.join('\n') + '\n')
  } catch {
    // evidence is best effort; dispatch goes on
  }
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const result = await next(e)
    await $.command.register({ name: 'team-agent', description: 'Show team-agent subagent routing and recent dispatches' })
    return result
  })

  on('tool.call', { tool: 'Agent' }, async ($, e, next) => {
    const route = routeFor(await loadTable($), e.subagent_type)
    if (!route) return next(e)
    const changed: Record<string, string> = {}
    if (usable(route.model) && !e.model) changed.model = route.model
    if (usable(route.effort) && (EFFORTS as readonly string[]).includes(route.effort) && !e.effort) changed.effort = route.effort
    if (Object.keys(changed).length === 0) return next(e)
    return next({ ...e, ...changed } as typeof e & { effort?: Effort })
  }).catch(($, e, next) => next(e))

  on('agent.spawn', async ($, e, next) => {
    const route = e.fork ? undefined : routeFor(await loadTable($), e.subagentType)
    const input = route && usable(route.model) && !e.model && !e.resume
      ? { ...e, model: route.model }
      : e
    const result = await next(input)
    if (!e.resume) {
      await remember($, {
        at: new Date().toISOString(), type: e.subagentType, role: route?.role ?? '-',
        requested: input.model, resolved: 'model' in result ? result.model : undefined,
        route_effort: route?.effort, agentId: 'agentId' in result ? result.agentId : undefined,
      })
    }
    return result
  }).catch(($, e, next) => next(e))

  on('command.run', { command: 'team-agent' }, async ($) => {
    const table = await loadTable($)
    const routes = Object.entries(table?.agents ?? {})
      .map(([type, r]) => `  ${type} -> ${r.role ?? '?'}: model=${r.model ?? 'inherit'} effort=${r.effort ?? 'inherit'}`)
    const lines = [
      `team-agent routing (${cache.path ?? 'no path'}): fills missing model/effort, keeps explicit ones`,
      ...(routes.length ? routes : ['  no routing table: run the team-agent skill to configure']),
      'recent dispatches this session:',
      ...(recent.length ? recent.map(d => `  ${d.at} ${d.type} (${d.role}) requested=${d.requested ?? 'inherit'} resolved=${d.resolved ?? '?'}`) : ['  none yet']),
    ]
    return { text: lines.join('\n') }
  })
}
