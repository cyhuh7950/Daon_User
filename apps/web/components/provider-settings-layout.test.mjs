import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import postcss from "postcss";

const css = postcss.parse(readFileSync(fileURLToPath(new URL("../app/settings/model-connections/provider-settings.css", import.meta.url)), "utf8"));

function declarations(selector) {
  const result = {};
  css.walkRules((rule) => {
    if (rule.parent.type === "atrule" || !rule.selectors.includes(selector)) return;
    rule.walkDecls((declaration) => { result[declaration.prop] = declaration.value; });
  });
  return result;
}

test("settings and model columns stay side by side across provider types", () => {
  const columns = declarations(".provider-configuration-columns");
  assert.equal(columns.display, "grid");
  assert.match(columns["grid-template-columns"], /repeat\(2,/u);
  assert.equal(declarations(".provider-configuration-settings")["align-content"], "start");
});

test("section headings use one compact row at desktop width", () => {
  const heading = declarations(".provider-admin-panel > .studio-section-heading > div");
  const catalog = declarations(".workspace-model-defaults > .studio-section-heading > div");
  assert.equal(heading.display, "flex");
  assert.equal(catalog.display, "flex");
  assert.equal(declarations(".provider-settings-shell .provider-status")["margin-bottom"], "4px");
});
