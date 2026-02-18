// Timeline event state machine — ported from reference derived.ts
// Converts flat events.json entries into spans for timeline display

const OVERRIDING_STATES = new Set(['overriding', 'preEnabled'])

/**
 * Build timeline events from raw drive events.
 * @param {Array} rawEvents - Flat array of events from all segments
 * @param {number} routeDurationMs - Total route duration in ms
 * @returns {Array<{type: string, route_offset_millis: number, end_route_offset_millis?: number, alertStatus?: number}>}
 */
export function buildTimelineEvents(rawEvents, routeDurationMs) {
  if (!rawEvents?.length) return []

  const events = [...rawEvents].sort((a, b) => a.route_offset_millis - b.route_offset_millis)

  const result = []
  let lastEngaged = null
  let lastAlert = null
  let lastOverride = null

  for (const ev of events) {
    if (ev.type === 'user_flag') {
      result.push({ type: 'user_flag', route_offset_millis: ev.route_offset_millis })
      continue
    }

    if (ev.type !== 'state') continue

    const { enabled, alertStatus, state } = ev.data

    // Engaged spans
    if (lastEngaged && !enabled) {
      result.push({
        type: 'engaged',
        route_offset_millis: lastEngaged.route_offset_millis,
        end_route_offset_millis: ev.route_offset_millis,
      })
      lastEngaged = null
    }
    if (!lastEngaged && enabled) lastEngaged = ev

    // Alert spans
    if (lastAlert && lastAlert.data.alertStatus !== alertStatus) {
      result.push({
        type: 'alert',
        route_offset_millis: lastAlert.route_offset_millis,
        end_route_offset_millis: ev.route_offset_millis,
        alertStatus: lastAlert.data.alertStatus,
      })
      lastAlert = null
    }
    if (!lastAlert && alertStatus !== 0) lastAlert = ev

    // Override spans
    if (lastOverride && !OVERRIDING_STATES.has(state)) {
      result.push({
        type: 'overriding',
        route_offset_millis: lastOverride.route_offset_millis,
        end_route_offset_millis: ev.route_offset_millis,
      })
      lastOverride = null
    }
    if (!lastOverride && OVERRIDING_STATES.has(state)) lastOverride = ev
  }

  // Close trailing spans at route end
  const endMs = routeDurationMs || 0
  if (lastEngaged) {
    result.push({ type: 'engaged', route_offset_millis: lastEngaged.route_offset_millis, end_route_offset_millis: endMs })
  }
  if (lastAlert) {
    result.push({ type: 'alert', route_offset_millis: lastAlert.route_offset_millis, end_route_offset_millis: endMs, alertStatus: lastAlert.data.alertStatus })
  }
  if (lastOverride) {
    result.push({ type: 'overriding', route_offset_millis: lastOverride.route_offset_millis, end_route_offset_millis: endMs })
  }

  return result
}

/**
 * Calculate engagement stats from timeline events
 * @param {Array} events
 * @returns {{ engagedMs: number, userFlags: number }}
 */
export function calcRouteStats(events) {
  let engagedMs = 0
  let userFlags = 0
  for (const ev of events) {
    if (ev.type === 'engaged') engagedMs += ev.end_route_offset_millis - ev.route_offset_millis
    else if (ev.type === 'user_flag') userFlags++
  }
  return { engagedMs, userFlags }
}
