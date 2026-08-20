export function concealProtectedRoute(root) {
  if (!root || typeof root.setAttribute !== "function") return;
  root.hidden = true;
  root.inert = true;
  root.setAttribute("aria-hidden", "true");
  root.setAttribute("data-session-validated", "false");
}

export function revealProtectedRoute(root) {
  if (!root || typeof root.removeAttribute !== "function") return;
  root.hidden = false;
  root.inert = false;
  root.removeAttribute("aria-hidden");
  root.setAttribute("data-session-validated", "true");
}
