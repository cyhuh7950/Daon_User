import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(`../../${path}`, import.meta.url), "utf8");

test("Notebook Home은 목록 상태·검색·정렬·보기·생성·설정 접근 계약을 제공한다", async () => {
  const [component, css, exports] = await Promise.all([
    read("packages/ui/src/notebook-home.jsx"),
    read("packages/ui/src/notebook-home.css"),
    read("packages/ui/src/index.js"),
  ]);
  for (const token of ["notebook-home-loading", "notebook-home-empty", "notebook-home-error", "notebook-search", "sortMode", "viewMode", "새 Notebook", "화면 설정", "라이선스", "사용자 설명서", "onOpenNotebook"]) assert.match(component, new RegExp(token, "u"));
  assert.match(component, /mode:\s*"empty"/u);
  assert.match(component, /mode:\s*"existing"/u);
  assert.doesNotMatch(component, /localhost|127\.0\.0\.1|NEXT_PUBLIC_|dangerouslySetInnerHTML/u);
  assert.match(css, /(?:font-size:\s*12px|font:\s*12px)/u);
  assert.match(css, /@media\s*\(prefers-reduced-motion:\s*reduce\)/u);
  assert.match(css, /:focus-visible/u);
  assert.match(exports, /NotebookHome/u);
});

test("Notebook Home 공통 설정 메뉴는 화면·라이선스·사용자 설명서 실제 화면으로 연결된다", async () => {
  const [workspace, licensePane, manualPane, licensePage, manualPage] = await Promise.all([
    read("apps/web/components/notebook-home-workspace.jsx"),
    read("apps/web/components/license-settings-pane.jsx"),
    read("apps/web/components/manual-settings-pane.jsx"),
    read("apps/web/app/settings/license/page.jsx"),
    read("apps/web/app/settings/manual/page.jsx"),
  ]);
  assert.match(workspace, /onOpenSetting=\{handleOpenSetting\}/u);
  assert.match(workspace, /screen:\s*"\/settings\/screen"/u);
  assert.match(workspace, /license:\s*"\/settings\/license"/u);
  assert.match(workspace, /manual:\s*"\/settings\/manual"/u);
  assert.match(licensePane, /getWorkspaceLicense/u);
  assert.match(licensePane, /applyCurrentOrganizationLicenseWithStepUp/u);
  assert.match(licensePane, /file\.size > 0/u);
  assert.match(licensePane, /endsWith\("\.json"\)/u);
  assert.match(licensePane, /file\.type === "application\/json"/u);
  assert.match(manualPane, /getManualManifest/u);
  assert.match(manualPane, /readManualDocument/u);
  assert.match(manualPane, /downloadManualAsset/u);
  assert.match(licensePage, /LicenseSettingsPane/u);
  assert.match(manualPage, /ManualSettingsPane/u);
  for (const source of [workspace, licensePane, manualPane]) {
    assert.doesNotMatch(source, /https?:\/\/|localhost|127\.0\.0\.1|NEXT_PUBLIC_/u);
  }
});

test("Notebook 삭제 dialog는 카드 레이아웃 바깥의 Home 레벨에서 렌더링된다", async () => {
  const component = await read("packages/ui/src/notebook-home.jsx");
  const card = component.slice(component.indexOf("function NotebookCard"), component.indexOf("export function NotebookHome"));
  assert.doesNotMatch(card, /DeleteDialog/u);
  assert.match(component, /deletingNotebook/u);
  assert.match(component, /<DeleteDialog notebook=\{deletingNotebook\}/u);
});
