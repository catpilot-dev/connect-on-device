<script>
  import { preserveRoute, unpreserveRoute, deleteRoute as apiDeleteRoute } from '../api.js'
  import { selectedRoute } from '../stores.js'
  import ConfirmDialog from './ConfirmDialog.svelte'
  import DownloadDialog from './DownloadDialog.svelte'

  /** @type {{ route: object, onEnrich?: () => void, enrichBusy?: boolean }} */
  let { route, onEnrich, enrichBusy = false } = $props()

  let isPreserved = $state(false)
  let preserveLoading = $state(false)
  let showDeleteConfirm = $state(false)
  let showDownload = $state(false)

  $effect(() => {
    isPreserved = route?.is_preserved ?? false
  })

  async function togglePreserve() {
    if (preserveLoading) return
    preserveLoading = true
    try {
      if (isPreserved) {
        await unpreserveRoute(route.local_id)
        isPreserved = false
      } else {
        await preserveRoute(route.local_id)
        isPreserved = true
      }
    } catch (e) {
      console.error('Preserve toggle error:', e)
    } finally {
      preserveLoading = false
    }
  }

  async function confirmDelete() {
    try {
      await apiDeleteRoute(route.local_id)
      selectedRoute.set(null) // Go back to list
    } catch (e) {
      console.error('Delete error:', e)
    }
  }
</script>

<div class="grid gap-2" class:grid-cols-4={!!onEnrich} class:grid-cols-3={!onEnrich}>
  <!-- Enrich -->
  {#if onEnrich}
    <button
      class="btn-ghost text-sm w-full justify-center"
      onclick={onEnrich}
      disabled={enrichBusy}
      title="Re-parse route data from rlogs"
    >
      <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
        <path d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
      </svg>
      {enrichBusy ? 'Busy' : 'Enrich'}
    </button>
  {/if}

  <!-- Save -->
  <button
    class="btn-ghost text-sm w-full justify-center"
    class:text-engage-blue={isPreserved}
    onclick={togglePreserve}
    disabled={preserveLoading}
    title={isPreserved ? 'Remove preservation' : 'Preserve route'}
  >
    <svg class="w-4 h-4 shrink-0" fill={isPreserved ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/>
    </svg>
    {isPreserved ? 'Saved' : 'Save'}
  </button>

  <!-- Export -->
  <button
    class="btn-ghost text-sm w-full justify-center"
    onclick={() => showDownload = true}
  >
    <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>
    </svg>
    Export
  </button>

  <!-- Delete -->
  <button
    class="btn-danger text-sm w-full justify-center"
    onclick={() => showDeleteConfirm = true}
  >
    <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
    </svg>
    Delete
  </button>
</div>

<!-- Confirm delete dialog -->
<ConfirmDialog
  bind:open={showDeleteConfirm}
  title="Delete Route"
  message="This will hide the route. Are you sure?"
  confirmLabel="Delete"
  danger
  onConfirm={confirmDelete}
  onCancel={() => showDeleteConfirm = false}
/>

<!-- Download dialog -->
<DownloadDialog
  bind:open={showDownload}
  routeName={route.local_id}
  maxSegment={route.maxqlog ?? 0}
  onClose={() => showDownload = false}
/>
