<script>
  import { onMount } from 'svelte'
  import { fetchParams, setParam } from '../../api.js'

  let { paramKey = '', values = [], labels = [], colors = [], unit = '' } = $props()

  let idx = $state(0)
  let saving = $state(false)

  const displayLabels = $derived(labels.length ? labels : values.map(String))
  const displayText = $derived(displayLabels[idx] ?? '?')
  const cqi = $derived(Math.min(50, 120 / displayText.length))
  const color = $derived(colors[idx] ?? '#e2e8f0')

  onMount(async () => {
    try {
      const params = await fetchParams()
      const raw = params[paramKey]
      if (raw !== undefined) {
        const stored = String(raw)
        const i = values.findIndex(v => String(v) === stored)
        if (i >= 0) idx = i
      }
    } catch {}
  })

  async function cycle() {
    if (saving) return
    saving = true
    const next = (idx + 1) % values.length
    try {
      await setParam(paramKey, String(values[next]))
      idx = next
    } catch {}
    saving = false
  }
</script>

<button class="cycle-widget" onclick={cycle} disabled={saving}>
  {#if unit}
    <span class="cycle-unit">{unit}</span>
  {/if}
  <span class="cycle-value" style="font-size: clamp(2rem, {cqi}cqi, 14rem); color: {color}">
    {displayText}
  </span>
</button>

<style>
  .cycle-widget {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
    width: 100%;
    cursor: pointer;
    background: none;
    border: none;
    padding: 0;
    transition: opacity 0.1s;
  }
  .cycle-widget:active {
    opacity: 0.6;
  }
  .cycle-widget:disabled {
    opacity: 0.4;
    cursor: wait;
  }
  .cycle-value {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1;
    color: #e2e8f0;
  }
  .cycle-unit {
    position: absolute;
    top: 4px;
    right: 6px;
    font-size: 0.75rem;
    color: var(--color-surface-600);
  }
</style>
