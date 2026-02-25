<script>
  let { value = 0, unit = '', thresholds = [], scale = 1, offset = 0, decimals = 0 } = $props()

  const displayValue = $derived(value * scale + offset)

  const color = $derived.by(() => {
    if (!thresholds.length) return '#e2e8f0'
    for (let i = thresholds.length - 1; i >= 0; i--) {
      if (displayValue >= thresholds[i].above) return thresholds[i].color
    }
    return thresholds[0]?.color ?? '#e2e8f0'
  })

  const displayText = $derived(displayValue.toFixed(decimals))
  // Scale font to fit: ~50cqi for 2-3 chars, shrink for wider values
  const cqi = $derived(Math.min(50, 120 / displayText.length))
</script>

<div class="digits-widget">
  {#if unit}
    <span class="digits-unit">{unit}</span>
  {/if}
  <span class="digits-value" style="color: {color}; font-size: clamp(2rem, {cqi}cqi, 14rem)">
    {displayText}
  </span>
</div>

<style>
  .digits-widget {
    position: relative;
    display: flex;
    align-items: center;
    justify-content: center;
    height: 100%;
  }
  .digits-value {
    font-weight: 700;
    font-variant-numeric: tabular-nums;
    line-height: 1;
  }
  .digits-unit {
    position: absolute;
    top: 4px;
    right: 6px;
    font-size: 0.75rem;
    color: var(--color-surface-600);
  }
</style>
