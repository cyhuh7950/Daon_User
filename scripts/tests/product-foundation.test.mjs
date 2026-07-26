import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";

const root = path.resolve(".");
const clients = ["web", "windows", "android", "ios"];
const roles = ["personal_user", "organization_member", "workspace_admin", "reviewer", "approver", "organization_admin", "operator"];
const states = ["loading", "empty", "ready", "warning", "error", "forbidden", "unavailable"];
const areas = ["home", "workspaces", "inbox", "history", "notifications", "model_connections", "account_organization_settings", "operations"];

const readJson = async (relative) => JSON.parse(await readFile(path.join(root, relative), "utf8"));

function contrastRatio(first, second) {
  const luminance = (hex) => {
    const rgb = hex.slice(1).match(/.{2}/g).map((value) => parseInt(value, 16) / 255)
      .map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2];
  };
  const [high, low] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (high + 0.05) / (low + 0.05);
}

test("전역 IA는 8개 영역과 안정적 Route 계약을 빠짐없이 가진다", async () => {
  const navigation = await readJson("packages/contracts/navigation.json");
  assert.deepEqual(navigation.allowed_clients, clients);
  assert.deepEqual(navigation.allowed_roles, roles);
  assert.deepEqual(navigation.required_states, states);
  assert.deepEqual([...new Set(navigation.routes.map((route) => route.area))].sort(), [...areas].sort());
  for (const key of ["route_id", "web_pattern", "native_route_key", "navigation_group", "clients", "roles", "breadcrumb", "title_key", "required_capability", "states"])
    assert.ok(navigation.routes.every((route) => Object.hasOwn(route, key)), `missing route field ${key}`);
  for (const key of ["route_id", "web_pattern", "native_route_key"])
    assert.equal(new Set(navigation.routes.map((route) => route[key])).size, navigation.routes.length, `duplicate ${key}`);
  assert.ok(navigation.routes.every((route) => route.clients.every((value) => clients.includes(value))));
  assert.ok(navigation.routes.every((route) => route.roles.every((value) => roles.includes(value))));
  assert.ok(navigation.routes.every((route) => states.every((value) => route.states.includes(value))));
  assert.doesNotMatch(JSON.stringify(navigation), /(?:localhost|127\.0\.0\.1|https?:\/\/|provider[_-]?code|secret)/i);
});

test("화면 목록은 역할·상태·Production Owner·Mock 경계를 기계 판독 가능하게 고정한다", async () => {
  const navigation = await readJson("packages/contracts/navigation.json");
  const catalog = await readJson("packages/contracts/screens.json");
  const routeIds = new Set(navigation.routes.map((route) => route.route_id));
  const fields = ["screen_id", "route_id", "purpose", "clients", "roles", "entry_points", "primary_actions", "states", "help_interface", "evidence_links", "production_bound_owner", "mock_boundary"];
  assert.equal(new Set(catalog.screens.map((screen) => screen.screen_id)).size, catalog.screens.length);
  for (const screen of catalog.screens) {
    for (const field of fields) assert.ok(Object.hasOwn(screen, field), `${screen.screen_id} missing ${field}`);
    assert.ok(routeIds.has(screen.route_id));
    assert.ok(screen.clients.every((value) => clients.includes(value)));
    assert.ok(screen.roles.every((value) => roles.includes(value)));
    assert.ok(states.every((value) => screen.states.includes(value)));
    assert.match(screen.production_bound_owner, /^R1-M3-/);
    assert.ok(["none", "unavailable", "adapter_mock"].includes(screen.mock_boundary.mode));
    if (screen.mock_boundary.mode !== "none") {
      assert.ok(screen.mock_boundary.adapter);
      assert.ok(screen.mock_boundary.replacement_owner);
    }
    assert.equal(screen.accessibility.supports_os_text_scaling, true);
    assert.ok(screen.accessibility.screen_reader_label_key);
  }
});

test("Design Token 정본은 승인된 정확값과 의미 기반 상태 표현을 가진다", async () => {
  const tokens = await readJson("packages/design-tokens/tokens.json");
  assert.deepEqual(tokens.typography, { body: "12px", form: "12px", description: "10px", auxiliary: "9px", sidebar_title: "14px", screen_title: "16px" });
  assert.deepEqual(tokens.breakpoints, { wide: { min: 1440 }, desktop: { min: 1024, max: 1439 }, tablet: { min: 600, max: 1023 }, mobile: { max: 599 } });
  assert.deepEqual(tokens.spacing, ["0px", "4px", "8px", "12px", "16px", "24px", "32px", "40px", "48px"]);
  assert.deepEqual(tokens.radius, ["4px", "8px", "12px", "999px"]);
  assert.deepEqual(tokens.motion.duration, ["120ms", "180ms", "240ms"]);
  assert.equal(tokens.motion.reduced_motion, "remove_or_reduce");
  assert.deepEqual(tokens.target_size, { minimum: "24px", desktop_control: "32px", touch_control: "44px" });
  assert.deepEqual(tokens.color.palette, { canvas: "#F4F7FB", surface: "#FFFFFF", muted_surface: "#EAF0F6", primary_text: "#172033", secondary_text: "#4B5B73", border: "#C7D2E0", accent: "#2563EB", accent_hover: "#1D4ED8", focus: "#0369A1", success: "#0F766E", warning: "#B45309", danger: "#B91C1C", ruleset_authority: "#6D28D9" });
  assert.equal(tokens.status.requires_label, true);
  assert.equal(tokens.status.requires_icon, true);
  assert.equal(tokens.status.requires_text, true);
  assert.equal(tokens.status.color_only_forbidden, true);
});

test("CSS와 TypeScript Adapter는 JSON Token 정본에서 파생되고 값이 일치한다", async () => {
  const tokens = await readJson("packages/design-tokens/tokens.json");
  const css = await readFile(path.join(root, "packages/design-tokens/tokens.css"), "utf8");
  const ts = await readFile(path.join(root, "packages/design-tokens/tokens.ts"), "utf8");
  const expectedCss = {
    "font-body": tokens.typography.body,
    "font-form": tokens.typography.form,
    "font-description": tokens.typography.description,
    "font-auxiliary": tokens.typography.auxiliary,
    "font-sidebar-title": tokens.typography.sidebar_title,
    "font-screen-title": tokens.typography.screen_title,
    "color-canvas": tokens.color.palette.canvas,
    "color-surface": tokens.color.palette.surface,
    "color-muted-surface": tokens.color.palette.muted_surface,
    "color-text-primary": tokens.color.palette.primary_text,
    "color-text-secondary": tokens.color.palette.secondary_text,
    "color-border": tokens.color.palette.border,
    "color-accent": tokens.color.palette.accent,
    "color-accent-hover": tokens.color.palette.accent_hover,
    "color-focus": tokens.color.palette.focus,
    "color-success": tokens.color.palette.success,
    "color-warning": tokens.color.palette.warning,
    "color-danger": tokens.color.palette.danger,
    "color-ruleset-authority": tokens.color.palette.ruleset_authority,
    "breakpoint-wide-min": `${tokens.breakpoints.wide.min}px`,
    "breakpoint-desktop-min": `${tokens.breakpoints.desktop.min}px`,
    "breakpoint-desktop-max": `${tokens.breakpoints.desktop.max}px`,
    "breakpoint-tablet-min": `${tokens.breakpoints.tablet.min}px`,
    "breakpoint-tablet-max": `${tokens.breakpoints.tablet.max}px`,
    "breakpoint-mobile-max": `${tokens.breakpoints.mobile.max}px`,
    "target-minimum": tokens.target_size.minimum,
    "target-desktop-control": tokens.target_size.desktop_control,
    "target-touch-control": tokens.target_size.touch_control
  };
  for (const [name, value] of Object.entries(expectedCss)) assert.match(css, new RegExp(`--daon-${name}:\\s*${value.replace("#", "\\#")}`));
  tokens.spacing.forEach((value, index) => assert.match(css, new RegExp(`--daon-spacing-${index}:\\s*${value}`)));
  tokens.radius.forEach((value, index) => assert.match(css, new RegExp(`--daon-radius-${index}:\\s*${value}`)));
  tokens.motion.duration.forEach((value, index) => assert.match(css, new RegExp(`--daon-motion-${index}:\\s*${value}`)));
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(ts, /import designTokens from "\.\/tokens\.json" with \{ type: "json" \}/);
  assert.match(ts, /export \{ designTokens \}/);
});

test("Semantic Color는 WCAG 2.2 AA Contrast 기준을 기계 검증한다", async () => {
  const tokens = await readJson("packages/design-tokens/tokens.json");
  for (const pair of tokens.contrast_pairs) {
    const actual = contrastRatio(pair.foreground, pair.background);
    assert.ok(actual >= pair.minimum_ratio, `${pair.id}: ${actual.toFixed(2)} < ${pair.minimum_ratio}`);
  }
  assert.ok(tokens.contrast_pairs.some((pair) => pair.kind === "normal_text" && pair.minimum_ratio === 4.5));
  assert.ok(tokens.contrast_pairs.some((pair) => pair.kind === "ui_boundary" && pair.minimum_ratio === 3));
});

test("접근성 계약은 Keyboard·Focus·설명·필수 상태 노출을 고정한다", async () => {
  const contract = await readJson("packages/ui/accessibility-contract.json");
  assert.equal(contract.standard, "WCAG 2.2 Level AA");
  assert.deepEqual(contract.keyboard_components, ["global_navigation", "menu", "tooltip", "popover", "dialog"]);
  assert.equal(contract.focus_indicator.token, "color.focus");
  assert.equal(contract.focus_indicator.must_not_be_obscured, true);
  assert.deepEqual(contract.tooltip.triggers, ["hover", "focus", "touch"]);
  assert.equal(contract.icon_only_control.accessible_name_required, true);
  assert.equal(contract.icon_only_control.tooltip_or_popover_required, true);
  assert.deepEqual(contract.mandatory_visible_states, ["error", "warning", "progress", "forbidden"]);
  assert.equal(contract.mandatory_visible_states_tooltip_only_forbidden, true);
  assert.equal(contract.os_text_scaling.supported, true);
});

test("Workflow는 승인된 세 Action Major만 올리고 기존 Gate 계약을 보존한다", async () => {
  const workflow = JSON.parse(await readFile(path.join(root, ".github/workflows/release-1-quality-gate.yml"), "utf8"));
  const job = workflow.jobs["release-1-quality-gate"];
  assert.deepEqual(job.steps.map((step) => step.id), ["checkout", "clear-evidence", "setup-node", "toolchain-pins", "npm-corepack", "setup-uv", "toolchain-versions", "verify-toolchain", "tauri-linux-prerequisites", "npm-ci", "desktop-rust-type-diagnostic", "quality-gate", "fallback-evidence", "upload-evidence"]);
  const checkout = job.steps.find((step) => step.id === "checkout");
  assert.equal(checkout.uses, "actions/checkout@v5");
  assert.deepEqual(checkout.with, { "fetch-depth": 0 });
  assert.equal(job.steps.find((step) => step.id === "setup-node").uses, "actions/setup-node@v5");
  assert.equal(job.steps.find((step) => step.id === "upload-evidence").uses, "actions/upload-artifact@v6");
  assert.deepEqual(job.steps.find((step) => step.id === "setup-node").with, { "node-version-file": ".node-version", cache: "npm" });
  assert.equal(job.steps.find((step) => step.id === "fallback-evidence").if, "${{ always() }}");
  assert.equal(job.steps.find((step) => step.id === "upload-evidence").with.name, "release-1-quality-gate-${{ github.sha }}");
});

test("Ubuntu Workflow는 Tauri 필수 패키지를 npm ci와 공통 Gate 전에 최소 설치한다", async () => {
  const workflow = JSON.parse(await readFile(path.join(root, ".github/workflows/release-1-quality-gate.yml"), "utf8"));
  const job = workflow.jobs["release-1-quality-gate"];
  const prerequisiteIndex = job.steps.findIndex((step) => step.id === "tauri-linux-prerequisites");
  const npmCiIndex = job.steps.findIndex((step) => step.id === "npm-ci");
  const qualityGateIndex = job.steps.findIndex((step) => step.id === "quality-gate");
  const prerequisite = job.steps[prerequisiteIndex];

  assert.ok(prerequisiteIndex >= 0 && prerequisiteIndex < npmCiIndex && npmCiIndex < qualityGateIndex);
  assert.equal(prerequisite.shell, "bash");
  assert.equal(prerequisite["continue-on-error"], undefined);
  assert.match(prerequisite.run, /^sudo apt-get update\nsudo apt-get install --yes --no-install-recommends /);
  for (const packageName of ["libwebkit2gtk-4.1-dev", "libayatana-appindicator3-dev", "librsvg2-dev", "patchelf", "ca-certificates", "pkg-config"])
    assert.match(prerequisite.run, new RegExp(`(?:^|\\s)${packageName.replaceAll(".", "\\.")}(?:\\s|$)`));
  assert.equal(job.steps[qualityGateIndex].run, "npm run verify:quality-gate");
  assert.match(await readFile(path.join(root, "rust-toolchain.toml"), "utf8"), /channel\s*=\s*"1\.97\.1"/);
});

test("R1-M3-02 Generator는 서버 위치 대신 저장소 상대 검증 Manifest만 참조한다", async () => {
  const generator = await readFile(path.join(root, "scripts/generate-r1-m3-02-evidence.mjs"), "utf8");
  assert.match(generator, /docs\/03_evidence\/release_1\/R1-M3-02\/server-validation-manifest\.json/);
  assert.doesNotMatch(generator, /ysna-server:\/|\/home\/ubuntu\/deploy\/daon-user/);
});

test("CI Desktop Rust 진단은 npm ci 뒤 Gate 앞에서 같은 명령으로 Fail-fast하고 Fallback에 남는다", async () => {
  const workflow = JSON.parse(await readFile(path.join(root, ".github/workflows/release-1-quality-gate.yml"), "utf8"));
  const steps = workflow.jobs["release-1-quality-gate"].steps;
  const npmCiIndex = steps.findIndex((step) => step.id === "npm-ci");
  const diagnosticIndex = steps.findIndex((step) => step.id === "desktop-rust-type-diagnostic");
  const qualityGateIndex = steps.findIndex((step) => step.id === "quality-gate");
  const diagnostic = steps[diagnosticIndex];
  const fallback = steps.find((step) => step.id === "fallback-evidence");
  const { CI_FALLBACK_STEP_IDS } = await import("../lib/quality-gate.mjs");

  assert.ok(npmCiIndex < diagnosticIndex && diagnosticIndex < qualityGateIndex);
  assert.equal(diagnostic.name, "Verify desktop Rust type prerequisites");
  assert.equal(diagnostic.run, "npm run verify:desktop-type");
  assert.equal(diagnostic["continue-on-error"], undefined);
  assert.equal(fallback.env.CI_STEP_DESKTOP_RUST_TYPE_DIAGNOSTIC, "${{ steps.desktop-rust-type-diagnostic.outcome }}");
  assert.ok(CI_FALLBACK_STEP_IDS.includes("desktop-rust-type-diagnostic"));
});

test("새 Package capability는 공통 Gate 실행 객체 계약을 사용한다", async () => {
  const policy = await readJson("quality-gate-policy.json");
  for (const componentId of ["packages/contracts", "packages/design-tokens"]) {
    const component = policy.components.find((item) => item.id === componentId);
    for (const [category, capability] of Object.entries(component.capabilities)) {
      if (!capability.command) continue;
      assert.equal(Array.isArray(capability.command), false, `${componentId}:${category} command must be an execution object`);
      assert.ok(capability.command.id);
      assert.ok(Array.isArray(capability.command.command));
      assert.equal(capability.command.failure_kind, "quality");
    }
  }
});
