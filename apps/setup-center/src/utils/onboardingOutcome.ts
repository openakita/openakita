export type OnboardingCompletionStep = "ob-done" | "ob-failed";

export function resolveOnboardingCompletionStep(
  hasCriticalErrors: boolean,
): OnboardingCompletionStep {
  return hasCriticalErrors ? "ob-failed" : "ob-done";
}
