import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import postcss from "postcss";

const stylesheet = postcss.parse(readFileSync(fileURLToPath(new URL("../app/globals.css", import.meta.url)), "utf8"));
const providerForm = [
  { tag: "main", classes: ["provider-settings-shell"] },
  { tag: "section", classes: ["provider-detail-grid"] },
  { tag: "label", classes: ["styled-check"] },
];

function matchesCompound(compound, element) {
  if (/[#:>+~]/u.test(compound)) return false;
  const tag = compound.match(/^[a-z][a-z-]*/u)?.[0];
  if (tag && tag !== element.tag) return false;
  for (const className of compound.matchAll(/\.([\w-]+)/gu)) {
    if (!element.classes.includes(className[1])) return false;
  }
  for (const attribute of compound.matchAll(/\[type=["']?([\w-]+)["']?\]/gu)) {
    if (attribute[1] !== element.type) return false;
  }
  return true;
}

function matchesSelector(selector, elements) {
  const compounds = selector.trim().split(/\s+/u);
  let elementIndex = elements.length - 1;
  for (let i = compounds.length - 1; i >= 0; i -= 1) {
    while (elementIndex >= 0 && !matchesCompound(compounds[i], elements[elementIndex])) elementIndex -= 1;
    if (elementIndex < 0) return false;
    elementIndex -= 1;
  }
  return true;
}

function specificity(selector) {
  const classes = (selector.match(/\.[\w-]+|\[[^\]]+\]/gu) ?? []).length;
  const tags = (selector.match(/(?:^|\s)[a-z][\w-]*/gu) ?? []).length;
  return classes * 100 + tags;
}

function resolvedStyles(elements) {
  const winning = new Map();
  let order = 0;
  stylesheet.walkRules((rule) => {
    if (rule.parent.type === "atrule") return; // Desktop 1920px fixture: ignore narrow-screen media rules.
    for (const selector of rule.selector.split(",")) {
      if (!matchesSelector(selector, elements)) continue;
      const weight = specificity(selector);
      rule.walkDecls((declaration) => {
        const previous = winning.get(declaration.prop);
        if (!previous || weight > previous.weight || (weight === previous.weight && order > previous.order)) {
          winning.set(declaration.prop, { value: declaration.value, weight, order });
        }
        order += 1;
      });
    }
  });
  return Object.fromEntries(Array.from(winning, ([property, result]) => [property, result.value]));
}

test("router Auto checkbox is compact and aligned with its caption at desktop width", () => {
  const label = resolvedStyles(providerForm);
  const checkbox = resolvedStyles([...providerForm, { tag: "input", type: "checkbox", classes: [] }]);

  assert.match(label.display, /flex/u);
  assert.equal(label["align-items"], "center");
  assert.equal(checkbox.width, "16px");
  assert.equal(checkbox.height, "16px");
  assert.equal(checkbox["min-height"], "16px");
  assert.equal(checkbox.padding, "0");
});
