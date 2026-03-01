<script>
  import { Switch } from 'bits-ui'
  let { checked = $bindable(false), disabled = false, onCheckedChange, label = '' } = $props()
</script>
<Switch.Root bind:checked {disabled} {onCheckedChange}
  class="shrink-0 w-11 h-6 rounded-full transition-colors
    data-[state=checked]:bg-engage-blue data-[state=unchecked]:bg-surface-600
    disabled:opacity-40 disabled:cursor-not-allowed"
  aria-label={label}
>
  <Switch.Thumb
    class="block w-5 h-5 rounded-full bg-white shadow transition-transform
      data-[state=checked]:translate-x-[22px] data-[state=unchecked]:translate-x-0.5" />
</Switch.Root>
