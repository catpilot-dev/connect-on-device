<script>
  import { preserveRoute, unpreserveRoute, deleteRoute as apiDeleteRoute } from '../api.js'
  import { selectedRoute } from '../stores.js'
  import ConfirmDialog from './ConfirmDialog.svelte'
  import DownloadDialog from './DownloadDialog.svelte'

  /** @type {{ route: object }} */
  let { route } = $props()

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
        await unpreserveRoute(route.fullname)
        isPreserved = false
      } else {
        await preserveRoute(route.fullname)
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
      await apiDeleteRoute(route.fullname)
      selectedRoute.set(null) // Go back to list
    } catch (e) {
      console.error('Delete error:', e)
    }
  }
</script>

<div class="flex items-center gap-2 flex-wrap">
  <!-- Preserve toggle -->
  <button
    class="btn-ghost text-sm"
    class:text-engage-blue={isPreserved}
    onclick={togglePreserve}
    disabled={preserveLoading}
    title={isPreserved ? 'Remove preservation' : 'Preserve route'}
  >
    <svg class="w-4 h-4" fill={isPreserved ? 'currentColor' : 'none'} stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z"/>
    </svg>
    {isPreserved ? 'Preserved' : 'Preserve'}
  </button>

  <!-- Download -->
  <button
    class="btn-ghost text-sm"
    onclick={() => showDownload = true}
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/>
    </svg>
    Download
  </button>

  <!-- Delete -->
  <button
    class="btn-danger text-sm"
    onclick={() => showDeleteConfirm = true}
  >
    <svg class="w-4 h-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
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
  routeName={route.fullname}
  maxSegment={route.maxqlog ?? 0}
  onClose={() => showDownload = false}
/>
