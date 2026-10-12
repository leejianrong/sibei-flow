<script lang="ts">
  import OutcomePill from "./lib/OutcomePill.svelte";
  import type { Outcome } from "./lib/outcome";

  // Scaffold only: the real run list and detail views land with the dashboard
  // port (SIB-93). This proves the toolchain end to end.
  const outcomes: Outcome[] = ["pr_proposed", "no_fix", "needs_prod_action", "out_of_scope"];
</script>

<main>
  <h1>sibei-flow</h1>
  <p>Dashboard scaffold (Svelte 5 + Vite + TypeScript). Read-only: this UI has no write actions.</p>
  <ul>
    {#each outcomes as outcome (outcome)}
      <li><OutcomePill {outcome} /></li>
    {/each}
  </ul>
</main>

<style>
  main {
    font-family: system-ui, sans-serif;
    max-width: 40rem;
    margin: 2rem auto;
    padding: 0 1rem;
  }
  ul {
    list-style: none;
    padding: 0;
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
</style>
