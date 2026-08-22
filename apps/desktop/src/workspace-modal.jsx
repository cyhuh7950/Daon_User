import { useEffect, useRef } from "react";

const FOCUSABLE = "button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled])";

export function WorkspaceModal({ open, title, titleId, onRequestClose, children }) {
  const dialogRef = useRef(null);
  const openerRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    openerRef.current = document.activeElement;
    const dialog = dialogRef.current;
    dialog?.querySelector(FOCUSABLE)?.focus();
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onRequestClose();
        return;
      }
      if (event.key === "Tab") {
        const controls = [...dialog.querySelectorAll(FOCUSABLE)];
        if (!controls.length) return;
        const first = controls[0];
        const last = controls.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    };
    dialog?.addEventListener("keydown", onKeyDown);
    return () => {
      dialog?.removeEventListener("keydown", onKeyDown);
      openerRef.current?.focus?.();
    };
  }, [onRequestClose, open]);
  if (!open) return null;
  return (
    <div className="workspace-modal-backdrop" role="presentation">
      <section ref={dialogRef} className="workspace-modal" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <header><h2 id={titleId}>{title}</h2><button type="button" onClick={onRequestClose} aria-label="닫기">×</button></header>
        {children}
      </section>
    </div>
  );
}
