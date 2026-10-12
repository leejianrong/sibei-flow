/** What a repair run ended as: the `RepairResult.outcome` values plus the brain-side drop. */
export type Outcome = "pr_proposed" | "no_fix" | "needs_prod_action" | "out_of_scope";

export type Tone = "good" | "warn" | "neutral";

export interface OutcomeView {
  label: string;
  tone: Tone;
}

const KNOWN: Record<Outcome, OutcomeView> = {
  pr_proposed: { label: "PR proposed", tone: "good" },
  no_fix: { label: "no fix", tone: "neutral" },
  needs_prod_action: { label: "needs prod action", tone: "warn" },
  out_of_scope: { label: "out of scope", tone: "neutral" },
};

/**
 * How to show an outcome. An outcome this build does not know (a newer brain)
 * renders as its raw value, neutrally, rather than throwing or hiding the run.
 */
export function describeOutcome(outcome: string): OutcomeView {
  // Own keys only: "toString" or "constructor" must not resolve to a prototype member.
  if (Object.hasOwn(KNOWN, outcome)) return KNOWN[outcome as Outcome];
  return { label: outcome, tone: "neutral" };
}
