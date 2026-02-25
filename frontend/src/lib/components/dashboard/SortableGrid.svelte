<script>
  import { onMount, onDestroy } from 'svelte'
  import Sortable from 'sortablejs'

  let { items = [], onReorder, children, class: className = '' } = $props()

  let gridEl = $state(null)
  let sortable = null

  onMount(() => {
    if (!gridEl) return
    sortable = Sortable.create(gridEl, {
      animation: 150,
      handle: '.drag-handle',
      ghostClass: 'sortable-ghost',
      chosenClass: 'sortable-chosen',
      dragClass: 'sortable-drag',
      onEnd(evt) {
        const { oldIndex, newIndex } = evt
        if (oldIndex === newIndex) return
        const reordered = [...items]
        const [moved] = reordered.splice(oldIndex, 1)
        reordered.splice(newIndex, 0, moved)
        onReorder?.(reordered)
      },
    })
  })

  onDestroy(() => {
    sortable?.destroy()
  })
</script>

<div bind:this={gridEl} class={className}>
  {@render children()}
</div>

<style>
  :global(.sortable-ghost) {
    opacity: 0.3;
  }
  :global(.sortable-chosen) {
    opacity: 0.8;
  }
  :global(.sortable-drag) {
    opacity: 0.9;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
</style>
