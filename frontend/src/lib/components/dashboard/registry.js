// Shared widget registry and layout persistence
// Used by DashboardPage (live, customizable) and RouteDetailPage Dashboard tab (replay, read-only)

// SVG icon paths (24x24 viewBox, stroke-based)
const ICONS = {
  thermometer: 'M12 9V3m0 0a3 3 0 0 0-3 3v6.2a5 5 0 1 0 6 0V6a3 3 0 0 0-3-3z',
  droplet: 'M12 2.7c-4.5 5.3-7 8.5-7 11.3a7 7 0 0 0 14 0c0-2.8-2.5-6-7-11.3z',
  battery: 'M6 7h11a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1zm14 3v4',
  gauge: 'M12 21a9 9 0 1 1 0-18 9 9 0 0 1 0 18zm0-5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zm0-1.5l4-5',
  steering: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm0 6a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM5.6 15l3.4-3m5 3 3.4 3M12 3v6',
  pedal: 'M7 4v16m10-16v16M4 12h16',
  shield: 'M12 2l7 4v5c0 5.5-3 8.5-7 10-4-1.5-7-4.5-7-10V6l7-4z',
  chart: 'M3 20h18M6 16l4-5 4 3 4-6',
}

export const WIDGET_REGISTRY = [
  // ── Vehicle Vitals ──
  { id: 'coolant_temp', label: 'Coolant', icon: ICONS.thermometer, category: 'Vitals',
    fields: ['coolantTemp'], unit: '\u00b0C', type: 'digits',
    thresholds: [{above: -50, color: '#3b82f6'}, {above: 60, color: '#22c55e'}, {above: 95, color: '#eab308'}, {above: 105, color: '#ef4444'}] },
  { id: 'oil_temp', label: 'Oil', icon: ICONS.droplet, category: 'Vitals',
    fields: ['oilTemp'], unit: '\u00b0C', type: 'digits',
    thresholds: [{above: -50, color: '#3b82f6'}, {above: 60, color: '#22c55e'}, {above: 120, color: '#eab308'}, {above: 140, color: '#ef4444'}] },
  { id: 'voltage', label: 'Battery', icon: ICONS.battery, category: 'Vitals',
    fields: ['voltage'], unit: 'V', type: 'digits', decimals: 1,
    thresholds: [{above: 0, color: '#ef4444'}, {above: 11.5, color: '#eab308'}, {above: 12.4, color: '#22c55e'}, {above: 14.8, color: '#eab308'}] },
  { id: 'speed', label: 'Speed', icon: ICONS.gauge, category: 'Vitals',
    fields: ['vEgo'], unit: 'km/h', type: 'gauge', scale: 3.6,
    range: [0, 200] },

  // ── OP Controls ──
  { id: 'steering', label: 'Steering', icon: ICONS.steering, category: 'Controls',
    fields: ['steeringAngleDeg'], unit: '\u00b0', type: 'digits', decimals: 1,
    thresholds: [{above: -9999, color: '#e2e8f0'}] },
  { id: 'accel', label: 'Gas / Brake', icon: ICONS.pedal, category: 'Controls',
    fields: ['gasPressed', 'brakePressed', 'accelCmd'], type: 'gas_brake_bar' },
  { id: 'engagement', label: 'Engagement', icon: ICONS.shield, category: 'Controls',
    fields: ['sdState', 'sdEnabled', 'cruiseSpeed'], type: 'engagement_badge' },

  // ── Quick Settings ──
  { id: 'speed_offset', label: 'Speed Offset', category: 'Settings',
    type: 'cycle', paramKey: 'MapdSpeedLimitOffsetPercent',
    values: ['0', '5', '10', '15'], unit: '%' },
  { id: 'curve_comfort', label: 'Curve Comfort', category: 'Settings',
    type: 'cycle', paramKey: 'MapdCurveTargetLatAccel',
    values: ['0', '1', '2', '3'], labels: ['1.5', '2.0', '2.5', '3.0'], unit: 'm/s²' },

  // ── Sparkline Charts ──
  { id: 'speed_chart', label: 'Speed Chart', icon: ICONS.chart, category: 'Charts',
    fields: ['vEgo'], type: 'sparkline', scale: 3.6, color: '#3b82f6' },
  { id: 'steering_chart', label: 'Steering Chart', icon: ICONS.chart, category: 'Charts',
    fields: ['steeringAngleDeg'], type: 'sparkline', color: '#22c55e' },
  { id: 'accel_chart', label: 'Accel Command', icon: ICONS.chart, category: 'Charts',
    fields: ['accelCmd'], type: 'sparkline', color: '#f59e0b' },
  { id: 'temps_chart', label: 'Temperatures', icon: ICONS.chart, category: 'Charts',
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

/** Transform a widget definition for imperial units when !isMetric */
export function resolveWidgetDef(def, isMetric) {
  if (isMetric || !def) return def
  if (def.unit === '\u00b0C') {
    // °C → °F: F = C * 1.8 + 32
    return {
      ...def,
      unit: '\u00b0F',
      scale: (def.scale ?? 1) * 1.8,
      offset: 32,
      thresholds: def.thresholds?.map(t => ({ ...t, above: t.above * 1.8 + 32 })),
      range: def.range?.map(v => v * 1.8 + 32),
      zones: def.zones?.map(z => ({ ...z, to: z.to * 1.8 + 32 })),
    }
  }
  if (def.unit === 'km/h') {
    // km/h → mph: adjust scale from 3.6 to 2.237
    return {
      ...def,
      unit: 'mph',
      scale: (def.scale ?? 1) / 1.609,
      range: def.range?.map(v => Math.round(v / 1.609)),
    }
  }
  return def
}
