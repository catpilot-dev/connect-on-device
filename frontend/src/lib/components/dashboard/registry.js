// Shared widget registry and layout persistence
// Used by DashboardPage (live, customizable) and RouteDetailPage Dashboard tab (replay, read-only)

export const WIDGET_REGISTRY = [
  // ── Vehicle Vitals ──
  { id: 'coolant_temp', label: 'Coolant Temp', category: 'Vitals',
    fields: ['coolantTemp'], unit: '\u00b0C', type: 'gauge',
    range: [-48, 160], zones: [{to: 80, color: 'blue'}, {to: 105, color: 'green'}, {to: 160, color: 'red'}] },
  { id: 'oil_temp', label: 'Oil Temp', category: 'Vitals',
    fields: ['oilTemp'], unit: '\u00b0C', type: 'gauge',
    range: [-48, 160], zones: [{to: 60, color: 'blue'}, {to: 120, color: 'green'}, {to: 160, color: 'red'}] },
  { id: 'voltage', label: 'Battery Voltage', category: 'Vitals',
    fields: ['voltage'], unit: 'V', type: 'gauge',
    range: [10, 16], zones: [{to: 11.5, color: 'red'}, {to: 14.5, color: 'green'}, {to: 16, color: 'yellow'}] },
  { id: 'speed', label: 'Speed', category: 'Vitals',
    fields: ['vEgo'], unit: 'km/h', type: 'gauge', scale: 3.6,
    range: [0, 200] },

  // ── OP Controls ──
  { id: 'steering', label: 'Steering', category: 'Controls',
    fields: ['steeringAngleDeg', 'steerCmd'], type: 'steering_wheel' },
  { id: 'accel', label: 'Gas / Brake', category: 'Controls',
    fields: ['gasPressed', 'brakePressed', 'accelCmd'], type: 'gas_brake_bar' },
  { id: 'engagement', label: 'Engagement', category: 'Controls',
    fields: ['sdState', 'sdEnabled', 'cruiseSpeed'], type: 'engagement_badge' },

  // ── Sparkline Charts ──
  { id: 'speed_chart', label: 'Speed Chart', category: 'Charts',
    fields: ['vEgo'], type: 'sparkline', scale: 3.6, color: '#3b82f6' },
  { id: 'steering_chart', label: 'Steering Chart', category: 'Charts',
    fields: ['steeringAngleDeg'], type: 'sparkline', color: '#22c55e' },
  { id: 'accel_chart', label: 'Accel Command', category: 'Charts',
    fields: ['accelCmd'], type: 'sparkline', color: '#f59e0b' },
  { id: 'temps_chart', label: 'Temperatures', category: 'Charts',
    fields: ['coolantTemp', 'oilTemp'], type: 'sparkline_multi',
    colors: ['#ef4444', '#f97316'], labels: ['Coolant', 'Oil'] },
]

export const STORAGE_KEY = 'dashboard_layout'

export const DEFAULT_LAYOUT = [
  'speed', 'coolant_temp', 'oil_temp', 'voltage',
  'steering', 'accel', 'engagement',
  'speed_chart', 'temps_chart',
]

export function loadLayout() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored) {
      const parsed = JSON.parse(stored)
      if (Array.isArray(parsed) && parsed.length > 0) return parsed
    }
  } catch {}
  return [...DEFAULT_LAYOUT]
}

export function saveLayout(layout) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(layout))
  } catch {}
}
