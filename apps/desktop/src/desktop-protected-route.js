export function concealProtectedDesktop(documentRef = document) {
  documentRef.documentElement?.setAttribute("data-desktop-protected-concealed", "true");
  documentRef.getElementById("root")?.setAttribute("inert", "");
}

export function revealProtectedDesktop(documentRef = document) {
  documentRef.documentElement?.removeAttribute("data-desktop-protected-concealed");
  documentRef.getElementById("root")?.removeAttribute("inert");
}
