<script>
  /**
   * Generic confirm dialog using native <dialog> for maximum browser compat.
   * bits-ui Dialog could be used but native <dialog> works everywhere
   * (Chrome 37+, Firefox 98+, Safari 15.4+, all modern mobile browsers).
   */

  /** @type {{ open: boolean, title: string, message: string, confirmLabel?: string, danger?: boolean, onConfirm: () => void, onCancel: () => void }} */
  let {
    open = $bindable(false),
    title = 'Confirm',
    message = '',
    confirmLabel = 'Confirm',
    danger = false,
    onConfirm,
    onCancel,
  } = $props()

  let dialogEl = $state(null)

  $effect(() => {
    if (!dialogEl) return
    if (open && !dialogEl.open) {
      dialogEl.showModal()
    } else if (!open && dialogEl.open) {
      dialogEl.close()
    }
  })

  function handleConfirm() {
    open = false
    onConfirm?.()
  }

  function handleCancel() {
    open = false
    onCancel?.()
  }
</script>

<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
<dialog
  bind:this={dialogEl}
  class="bg-surface-800 text-surface-100 rounded-xl border border-surface-700 shadow-2xl p-0 backdrop:bg-black/60 max-w-sm w-full"
  onclose={handleCancel}
  onkeydown={(e) => e.key === 'Escape' && handleCancel()}
>
  <div class="p-5">
    <h3 class="text-lg font-semibold mb-2">{title}</h3>
    <p class="text-surface-300 text-sm mb-5">{message}</p>
    <div class="flex justify-end gap-2">
      <button class="btn-ghost" onclick={handleCancel}>Cancel</button>
      <button
        class={danger ? 'btn-danger' : 'btn-primary'}
        onclick={handleConfirm}
      >
        {confirmLabel}
      </button>
    </div>
  </div>
</dialog>
