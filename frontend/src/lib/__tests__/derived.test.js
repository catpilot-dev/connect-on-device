import { describe, it, expect } from 'vitest'
import { buildTimelineEvents, calcRouteStats } from '../derived.js'

// ── buildTimelineEvents ─────────────────────────────────────

describe('buildTimelineEvents', () => {
  it('empty input returns []', () => {
    expect(buildTimelineEvents([], 60000)).toEqual([])
    expect(buildTimelineEvents(null, 60000)).toEqual([])
    expect(buildTimelineEvents(undefined, 60000)).toEqual([])
  })

  it('single engage-disengage produces one span', () => {
    const events = [
      { type: 'state', route_offset_millis: 1000, data: { enabled: true, alertStatus: 0, state: 'enabled' } },
      { type: 'state', route_offset_millis: 5000, data: { enabled: false, alertStatus: 0, state: 'disabled' } },
    ]
    const result = buildTimelineEvents(events, 60000)
    const engaged = result.filter(e => e.type === 'engaged')
    expect(engaged).toHaveLength(1)
    expect(engaged[0].route_offset_millis).toBe(1000)
    expect(engaged[0].end_route_offset_millis).toBe(5000)
  })

  it('trailing engage closes at routeDurationMs', () => {
    const events = [
      { type: 'state', route_offset_millis: 1000, data: { enabled: true, alertStatus: 0, state: 'enabled' } },
    ]
    const result = buildTimelineEvents(events, 60000)
    const engaged = result.filter(e => e.type === 'engaged')
    expect(engaged).toHaveLength(1)
    expect(engaged[0].end_route_offset_millis).toBe(60000)
  })

  it('alert spans', () => {
    const events = [
      { type: 'state', route_offset_millis: 1000, data: { enabled: true, alertStatus: 1, state: 'enabled' } },
      { type: 'state', route_offset_millis: 3000, data: { enabled: true, alertStatus: 0, state: 'enabled' } },
    ]
    const result = buildTimelineEvents(events, 60000)
    const alerts = result.filter(e => e.type === 'alert')
    expect(alerts).toHaveLength(1)
    expect(alerts[0].alertStatus).toBe(1)
    expect(alerts[0].route_offset_millis).toBe(1000)
    expect(alerts[0].end_route_offset_millis).toBe(3000)
  })

  it('override spans from preEnabled', () => {
    const events = [
      { type: 'state', route_offset_millis: 1000, data: { enabled: true, alertStatus: 0, state: 'preEnabled' } },
      { type: 'state', route_offset_millis: 2000, data: { enabled: true, alertStatus: 0, state: 'enabled' } },
    ]
    const result = buildTimelineEvents(events, 60000)
    const overrides = result.filter(e => e.type === 'overriding')
    expect(overrides).toHaveLength(1)
    expect(overrides[0].route_offset_millis).toBe(1000)
    expect(overrides[0].end_route_offset_millis).toBe(2000)
  })

  it('user_flag passthrough', () => {
    const events = [
      { type: 'user_flag', route_offset_millis: 5000 },
    ]
    const result = buildTimelineEvents(events, 60000)
    expect(result).toHaveLength(1)
    expect(result[0].type).toBe('user_flag')
    expect(result[0].route_offset_millis).toBe(5000)
  })

  it('unsorted input gets sorted', () => {
    const events = [
      { type: 'state', route_offset_millis: 5000, data: { enabled: false, alertStatus: 0, state: 'disabled' } },
      { type: 'state', route_offset_millis: 1000, data: { enabled: true, alertStatus: 0, state: 'enabled' } },
    ]
    const result = buildTimelineEvents(events, 60000)
    const engaged = result.filter(e => e.type === 'engaged')
    expect(engaged).toHaveLength(1)
    expect(engaged[0].route_offset_millis).toBe(1000)
    expect(engaged[0].end_route_offset_millis).toBe(5000)
  })

  it('complex multi-span scenario', () => {
    const events = [
      { type: 'state', route_offset_millis: 1000, data: { enabled: true, alertStatus: 0, state: 'enabled' } },
      { type: 'user_flag', route_offset_millis: 2000 },
      { type: 'state', route_offset_millis: 3000, data: { enabled: true, alertStatus: 1, state: 'enabled' } },
      { type: 'state', route_offset_millis: 4000, data: { enabled: true, alertStatus: 0, state: 'overriding' } },
      { type: 'state', route_offset_millis: 5000, data: { enabled: false, alertStatus: 0, state: 'disabled' } },
    ]
    const result = buildTimelineEvents(events, 60000)
    const types = result.map(e => e.type)
    expect(types).toContain('engaged')
    expect(types).toContain('user_flag')
    expect(types).toContain('alert')
    expect(types).toContain('overriding')
  })
})

// ── calcRouteStats ──────────────────────────────────────────

describe('calcRouteStats', () => {
  it('sums engaged spans', () => {
    const events = [
      { type: 'engaged', route_offset_millis: 1000, end_route_offset_millis: 5000 },
      { type: 'engaged', route_offset_millis: 10000, end_route_offset_millis: 20000 },
    ]
    const stats = calcRouteStats(events)
    expect(stats.engagedMs).toBe(14000)
  })

  it('counts user_flags', () => {
    const events = [
      { type: 'user_flag', route_offset_millis: 1000 },
      { type: 'user_flag', route_offset_millis: 2000 },
      { type: 'engaged', route_offset_millis: 0, end_route_offset_millis: 60000 },
    ]
    const stats = calcRouteStats(events)
    expect(stats.userFlags).toBe(2)
    expect(stats.engagedMs).toBe(60000)
  })

  it('empty returns zeros', () => {
    const stats = calcRouteStats([])
    expect(stats.engagedMs).toBe(0)
    expect(stats.userFlags).toBe(0)
  })
})
