<script>
  let { nav } = $props()
</script>

<header class="sticky top-0 z-50 border-b border-surface-700/50 bg-surface-900/80 backdrop-blur-lg">
  <div class="mx-auto max-w-6xl px-4 py-3 flex items-center gap-4 flex-wrap">
    <!-- Brand -->
    <div class="flex items-center gap-2">
      <span class="text-lg font-semibold text-surface-50">connect</span>
    </div>

    <!-- Navigation tabs -->
    {#if nav}
      {@render nav()}
    {/if}
  </div>
</header>
