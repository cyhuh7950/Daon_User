import mobileStudioContract from "@daon-user/contracts/mobile-studio-actions.json" with { type: "json" };

export type MobileStudioDecision = {
  allowed: boolean;
  code: string;
  createsContentRevision: boolean;
  stateDomain: string;
  continueOn: string;
};

const decisions = new Map(mobileStudioContract.actions.map((item) => [item.action, Object.freeze({ ...item.decision })]));

export const mobileStudioActions = Object.freeze(mobileStudioContract.actions.map((item) => item.action));

export function evaluateMobileStudioAction(action: string): MobileStudioDecision {
  return decisions.get(action) ?? {
    allowed: false,
    code: "MOBILE_STUDIO_ACTION_UNKNOWN",
    createsContentRevision: false,
    stateDomain: "none",
    continueOn: "Web·Windows에서 확인"
  };
}
