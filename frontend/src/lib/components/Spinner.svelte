<script>
  let { class: className = 'w-4 h-4' } = $props()
</script>
<svg class="animate-spin {className}" viewBox="0 0 24 24" fill="none">
  <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3"
    stroke-linecap="round" stroke-dasharray="32 32" />
</svg>
