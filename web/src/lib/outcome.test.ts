import { describe, expect, it } from "vitest";
import { describeOutcome } from "./outcome";

describe("describeOutcome", () => {
  it("labels every known outcome", () => {
    expect(describeOutcome("pr_proposed")).toEqual({ label: "PR proposed", tone: "good" });
    expect(describeOutcome("needs_prod_action").tone).toBe("warn");
    expect(describeOutcome("no_fix").tone).toBe("neutral");
    expect(describeOutcome("out_of_scope").label).toBe("out of scope");
  });

  it("shows an unknown outcome as its raw value instead of throwing", () => {
    expect(describeOutcome("something_new")).toEqual({
      label: "something_new",
      tone: "neutral",
    });
  });

  it("does not resolve inherited object keys as outcomes", () => {
    expect(describeOutcome("toString").label).toBe("toString");
  });
});
