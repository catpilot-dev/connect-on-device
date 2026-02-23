<script>
  import { Collapsible } from 'bits-ui'
  import ChevronIcon from './ChevronIcon.svelte'
  let { title, metadata = '', open = $bindable(false), onOpenChange, children } = $props()
</script>
<div class="card p-4">
  <Collapsible.Root bind:open {onOpenChange}>
    <Collapsible.Trigger class="w-full flex items-center justify-between">
      <h3 class="text-surface-400 text-xs font-semibold uppercase tracking-wider">{title}</h3>
      <div class="flex items-center gap-3">
        {#if metadata}<span class="text-xs text-surface-500 font-mono">{metadata}</span>{/if}
        <ChevronIcon rotated={open} />
      </div>
    </Collapsible.Trigger>
    <Collapsible.Content>
      <div class="mt-4">
        {@render children?.()}
      </div>
    </Collapsible.Content>
  </Collapsible.Root>
</div>
