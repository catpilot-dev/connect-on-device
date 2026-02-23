<script>
  import { onMount, onDestroy } from 'svelte'
  import WidgetCard from '../components/dashboard/WidgetCard.svelte'
  import GaugeWidget from '../components/dashboard/GaugeWidget.svelte'
  import SteeringWidget from '../components/dashboard/SteeringWidget.svelte'
  import GasBrakeWidget from '../components/dashboard/GasBrakeWidget.svelte'
  import EngagementWidget from '../components/dashboard/EngagementWidget.svelte'
  import SparklineWidget from '../components/dashboard/SparklineWidget.svelte'
  import SparklineMultiWidget from '../components/dashboard/SparklineMultiWidget.svelte'
  import WidgetPicker from '../components/dashboard/WidgetPicker.svelte'
  import { WIDGET_REGISTRY, DEFAULT_LAYOUT, STORAGE_KEY, loadLayout, saveLayout } from '../components/dashboard/registry.js'

  const HISTORY_SIZE = 300  // 60s at 5Hz

  // ── State ──
  let layout = $state(loadLayout())
  let showPicker = $state(false)

  // Unified telemetry data bus
  let telemetry = $state({
    t: 0, coolantTemp: 0, oilTemp: 0, voltage: 0,
    vEgo: 0, steeringAngleDeg: 0,
    gasPressed: false, brakePressed: false,
    steerCmd: 0, accelCmd: 0,
    sdState: '', sdEnabled: false, cruiseSpeed: 0,
  })

  let history = $state([])

  // ── Live state ──
  let ws = $state(null)
  let liveConnected = $state(false)

  // Build per-field history for sparkline widgets
  function fieldHistory(field) {
    return history.map(h => ({ t: h.t, v: h[field] ?? 0 }))
  }

  function multiFieldHistories(fields) {
    return fields.map(f => fieldHistory(f))
  }

  function addWidget(id) {
    if (!layout.includes(id)) {
      layout = [...layout, id]
      saveLayout(layout)
    }
  }

  function removeWidget(id) {
    layout = layout.filter(w => w !== id)
    saveLayout(layout)
  }

  function getWidgetDef(id) {
    return WIDGET_REGISTRY.find(w => w.id === id)
  }

  // ── Live WebSocket ──
  function connectLive() {
    if (ws) { ws.close(); ws = null }
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    const socket = new WebSocket(`${proto}://${location.host}/ws/dashboard`)
    socket.onopen = () => { liveConnected = true }
    socket.onclose = () => {
      liveConnected = false
      setTimeout(connectLive, 2000)
    }
    socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data)
        if (msg.error) return
        telemetry = msg
        history = [...history.slice(-(HISTORY_SIZE - 1)), msg]
      } catch {}
    }
    ws = socket
  }

  function disconnectLive() {
    if (ws) { ws.close(); ws = null }
    liveConnected = false
  }

  onMount(() => {
    connectLive()
  })

  onDestroy(() => {
    disconnectLive()
  })
</script>

<div class="mx-auto max-w-6xl px-4 py-4 flex flex-col gap-4">
  <!-- Header row -->
  <div class="flex items-center justify-between flex-wrap gap-3">
    <div class="flex items-center gap-2">
      <h2 class="text-sm font-medium text-surface-200">Live Dashboard</h2>
      <div class="flex items-center gap-1.5 text-sm">
        <div class="w-2 h-2 rounded-full {liveConnected ? 'bg-engage-green' : 'bg-engage-red'}"></div>
        <span class="text-surface-400">{liveConnected ? 'Connected' : 'Disconnected'}</span>
      </div>
    </div>

    <button
      class="btn-ghost text-sm"
      onclick={() => showPicker = true}
    >
      <svg class="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
        <path d="M12 4v16m-8-8h16" />
      </svg>
      Add Widget
    </button>
  </div>

  <!-- Widget grid -->
  <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3">
    {#each layout as widgetId (widgetId)}
      {@const def = getWidgetDef(widgetId)}
      {#if def}
        <WidgetCard label={def.label} onremove={() => removeWidget(widgetId)}>
          {#if def.type === 'gauge'}
            <GaugeWidget
              value={telemetry[def.fields[0]] ?? 0}
              unit={def.unit}
              range={def.range}
              zones={def.zones ?? []}
              scale={def.scale ?? 1}
              color={def.color ?? '#3b82f6'}
              history={fieldHistory(def.fields[0]).map(h => h.v)}
            />
          {:else if def.type === 'steering_wheel'}
            <SteeringWidget
              angle={telemetry.steeringAngleDeg}
              steerCmd={telemetry.steerCmd}
            />
          {:else if def.type === 'gas_brake_bar'}
            <GasBrakeWidget
              gasPressed={telemetry.gasPressed}
              brakePressed={telemetry.brakePressed}
              accelCmd={telemetry.accelCmd}
            />
          {:else if def.type === 'engagement_badge'}
            <EngagementWidget
              sdState={telemetry.sdState}
              sdEnabled={telemetry.sdEnabled}
              cruiseSpeed={telemetry.cruiseSpeed}
            />
          {:else if def.type === 'sparkline'}
            <SparklineWidget
              history={fieldHistory(def.fields[0])}
              scale={def.scale ?? 1}
              color={def.color ?? '#3b82f6'}
            />
          {:else if def.type === 'sparkline_multi'}
            <SparklineMultiWidget
              histories={multiFieldHistories(def.fields)}
              colors={def.colors ?? []}
              labels={def.labels ?? []}
            />
          {/if}
        </WidgetCard>
      {/if}
    {/each}
  </div>

  {#if layout.length === 0}
    <div class="flex flex-col items-center justify-center h-48 text-surface-500">
      <p class="text-lg mb-2">No widgets added</p>
      <button class="btn-ghost" onclick={() => showPicker = true}>
        Add your first widget
      </button>
    </div>
  {/if}
</div>

{#if showPicker}
  <WidgetPicker
    registry={WIDGET_REGISTRY}
    activeIds={layout}
    onpick={addWidget}
    onclose={() => showPicker = false}
  />
{/if}
