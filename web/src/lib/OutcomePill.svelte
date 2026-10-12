<script lang="ts">
  import { describeOutcome } from "./outcome";

  let { outcome }: { outcome: string } = $props();
  const view = $derived(describeOutcome(outcome));
</script>

<span class="pill {view.tone}" data-outcome={outcome}>{view.label}</span>

<style>
  .pill {
    display: inline-block;
    padding: 0.1rem 0.6rem;
    border-radius: 999px;
    font-size: 0.8rem;
    border: 1px solid currentColor;
  }
  .good {
    color: #1a7f37;
  }
  .warn {
    color: #9a6700;
  }
  .neutral {
    color: #57606a;
  }
</style>
