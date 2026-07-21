const FOCUSABLE_SELECTOR = [
  "button:not([disabled])",
  "a[href]",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])"
].join(",");

export function focusInitialModalControl(modal) {
  const target = modal?.querySelector?.("[data-modal-initial-focus], " + FOCUSABLE_SELECTOR);
  target?.focus?.();
  return target ?? null;
}

export function trapModalTab(modal, event, activeElement = globalThis.document?.activeElement) {
  if (event?.key !== "Tab") return false;
  const focusable = [...(modal?.querySelectorAll?.(FOCUSABLE_SELECTOR) ?? [])];
  if (focusable.length === 0) return false;
  const first = focusable[0];
  const last = focusable.at(-1);
  const target = event.shiftKey && activeElement === first
    ? last
    : !event.shiftKey && activeElement === last
      ? first
      : null;
  if (!target) return false;
  event.preventDefault();
  target.focus();
  return true;
}

export function setBackgroundInert(background, active) {
  if (!background) return;
  if (active) {
    background.setAttribute("inert", "");
    background.inert = true;
    background.setAttribute("aria-hidden", "true");
  } else {
    background.inert = false;
    background.removeAttribute("inert");
    background.removeAttribute("aria-hidden");
  }
}

export function transitionHelp(open, action) {
  if (["pointer-enter", "focus", "toggle", "open"].includes(action)) return true;
  if (["escape", "blur", "pointer-leave", "close"].includes(action)) return false;
  return open;
}
