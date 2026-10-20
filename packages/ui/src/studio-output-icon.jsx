const GLYPHS = Object.freeze({
  evidence_report: <><path d="M5 3h9l5 5v13H5Z" /><path d="M14 3v5h5M8 12h8M8 16h5" /></>,
  compliance_checklist: <><rect x="4" y="4" width="16" height="17" rx="2" /><rect x="8" y="2" width="8" height="4" rx="2" /><path d="m7 10 2 2 4-4m-6 9 2 2 4-4m3-5h1m-1 7h1" /></>,
  comparison_table: <><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M9 4v16m6-10v10M3 15h18" /></>,
  knowledge_map: <><rect x="2" y="9" width="6" height="6" rx="1.5" /><rect x="16" y="2" width="6" height="6" rx="1.5" /><rect x="16" y="16" width="6" height="6" rx="1.5" /><path d="M8 12h4V5h4m-4 7v7h4" /></>,
  business_draft: <><path d="M12 3H4v18h15v-8M8 8h5M8 12h3m1 3 8-8 3 3-8 8-4 1Zm6-10 3 3" /></>,
  slides: <><rect x="2" y="3" width="20" height="14" rx="2" /><path d="M7 3v14m5 0v5m-4 0h8M11 7h7m-7 4h5" /></>,
  infographic: <><path d="M4 21v-9m6 9V8m6 13v-7m6 7V5" /><circle cx="4" cy="6" r="1" /><circle cx="16" cy="8" r="1" /></>,
  flashcards: <><rect x="7" y="3" width="13" height="17" rx="2" /><path d="M3 7H2v16h13M10 9h7m-7 4h5" /></>,
  quiz: <><rect x="3" y="3" width="18" height="18" rx="2" /><path d="m9 8 1-2h4l2 2v2l-4 3v2" /><circle cx="12" cy="18" r=".5" /></>,
  audio: <path d="M2 10v6m4-10v14m4-11v8m4-14v11m5-12v6m-3-3h6" />,
  video: <><rect x="2" y="5" width="14" height="14" rx="2" /><path d="m16 9 6-4v14l-6-4" /></>,
});

export function StudioOutputIcon({ outputType, className = "" }) {
  const type = Object.hasOwn(GLYPHS, outputType) ? outputType : "evidence_report";
  return <svg className={`studio-output-icon ${className}`.trim()} data-output-type={type}
    width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor"
    strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
    {GLYPHS[type]}
  </svg>;
}
