<script>
  let { rotated = false, class: className = '' } = $props()
</script>
<svg class="w-4 h-4 text-surface-500 transition-transform {rotated ? 'rotate-180' : ''} {className}"
  viewBox="0 0 20 20" fill="currentColor">
  <path fill-rule="evenodd" d="M5.23 7.21a.75.75 0 011.06.02L10 11.168l3.71-3.938a.75.75 0 111.08 1.04l-4.25 4.5a.75.75 0 01-1.08 0l-4.25-4.5a.75.75 0 01.02-1.06z" clip-rule="evenodd" />
</svg>
