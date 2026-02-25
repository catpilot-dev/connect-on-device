<script>
  import { Collapsible } from 'bits-ui'
  import ChevronIcon from './ChevronIcon.svelte'
  let { title, metadata = '', disabled = false, disabledReason = '', open = $bindable(false), onOpenChange, children } = $props()
</script>
<div class="card p-4 {disabled ? 'opacity-50' : ''}">
  <Collapsible.Root bind:open onOpenChange={(o) => { if (!disabled) onOpenChange?.(o) }} disabled={disabled}>
    <Collapsible.Trigger class="w-full flex items-center justify-between" disabled={disabled}>
      <h3 class="text-surface-400 text-xs font-semibold uppercase tracking-wider">{title}</h3>
      <div class="flex items-center gap-3">
        {#if disabled && disabledReason}
          <span class="text-xs text-engage-orange">{disabledReason}</span>
        {:else if metadata}
          <span class="text-xs text-surface-500 font-mono">{metadata}</span>
        {/if}
        {#if !disabled}
          <ChevronIcon rotated={open} />
        {/if}
      </div>
    </Collapsible.Trigger>
    <Collapsible.Content>
      <div class="mt-4">
        {@render children?.()}
      </div>
    </Collapsible.Content>
  </Collapsible.Root>
</div>
