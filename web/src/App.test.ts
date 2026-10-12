import { render, screen } from "@testing-library/svelte";
import { describe, expect, it } from "vitest";
import App from "./App.svelte";

describe("App", () => {
  it("renders the heading and one pill per outcome", () => {
    render(App);
    expect(screen.getByRole("heading", { name: "sibei-flow" })).toBeTruthy();
    expect(screen.getByText("PR proposed").getAttribute("data-outcome")).toBe("pr_proposed");
    expect(screen.getByText("needs prod action")).toBeTruthy();
  });

  it("has no write controls (the UI is read-only)", () => {
    const { container } = render(App);
    expect(container.querySelectorAll("button, form, input, textarea")).toHaveLength(0);
  });
});
