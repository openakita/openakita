import { describe, expect, it } from "vitest";

import { resolveOnboardingCompletionStep } from "../onboardingOutcome";

describe("onboarding completion routing", () => {
  it("shows the success screen only when every critical step succeeded", () => {
    expect(resolveOnboardingCompletionStep(false)).toBe("ob-done");
  });

  it("keeps critical setup failures out of the success screen", () => {
    expect(resolveOnboardingCompletionStep(true)).toBe("ob-failed");
  });
});
