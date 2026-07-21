"use client";

import { createElement, useState } from "react";
import { resolveWeight } from "./source-knowledge-model.js";
import { transitionHelp } from "./workspace-interaction.js";

export function reduceHelpOpen(current, action) {
  return transitionHelp(current, action);
}

export function Help({ id, label, children, initialOpen = false }) {
  const [open, setOpen] = useState(initialOpen);
  const tooltipId = `source-help-${id}`;
  const change = (action) => setOpen((current) => reduceHelpOpen(current, action));
  return createElement(
    "span",
    { className: "source-help", onPointerLeave: () => change("pointer-leave") },
    createElement(
      "button",
      {
        type: "button",
        className: "icon-button",
        "aria-label": label,
        "aria-expanded": open,
        "aria-controls": tooltipId,
        "aria-describedby": open ? tooltipId : undefined,
        onFocus: () => change("focus"),
        onBlur: () => change("blur"),
        onPointerEnter: () => change("pointer-enter"),
        onClick: () => change("open"),
        onKeyDown: (event) => {
          if (event.key === "Escape") change("escape");
        }
      },
      "i"
    ),
    open ? createElement("span", { id: tooltipId, role: "tooltip", className: "info-tooltip" }, children) : null
  );
}

export function WeightControl({ source, overrideValue, onSetOverride = () => {}, onClearOverride = () => {} }) {
  const hasOverride = overrideValue !== undefined;
  const profile = hasOverride ? { ...source.weightProfile, source: overrideValue } : source.weightProfile;
  const snapshot = resolveWeight(profile);
  const snapshotItems = [
    ["요청값", snapshot.requested.toFixed(1)],
    ["적용값", snapshot.applied.toFixed(1)],
    ["적용 계층", snapshot.layer],
    ["계층 결합", "가장 구체적인 하나 · 곱하지 않음"],
    ["Clamp 사유", snapshot.clampReason ?? "없음"]
  ];
  return createElement(
    "div",
    { className: "weight-control", "data-weight-layer": snapshot.layer },
    hasOverride
      ? createElement(
          "div",
          { className: "weight-override-control" },
          createElement("label", { htmlFor: "source-weight" }, `개별 Source 가중치 ${Number(overrideValue).toFixed(1)}`),
          createElement("input", {
            id: "source-weight",
            type: "range",
            min: "0.5",
            max: "2",
            step: "0.1",
            value: overrideValue,
            onChange: (event) => onSetOverride(Number(event.target.value))
          }),
          createElement("button", { type: "button", className: "secondary-action", onClick: onClearOverride }, "개별 Source Override 해제")
        )
      : createElement("button", { type: "button", className: "secondary-action", onClick: () => onSetOverride(snapshot.requested) }, "개별 Source Override 추가"),
    createElement(
      "dl",
      { className: "weight-snapshot", "aria-label": "가중치 Snapshot Preview" },
      ...snapshotItems.map(([term, value]) => createElement("div", { key: term }, createElement("dt", null, term), createElement("dd", null, value)))
    )
  );
}
