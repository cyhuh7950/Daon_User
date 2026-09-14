import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import test from "node:test";

const root = new URL("../../", import.meta.url);
const privateNetworkEvidence = new URL("docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/browser-network.jsonl", root);
const privateEvidenceManifest = new URL("docs/03_evidence/release_1/R1-M8-10-WINDOWS-OFFLINE-STUDIO-01/phase-c3-manual/manifest.json", root);
const docs = [
  ["daon-getting-started", "Daon Getting Started"],
  ["daon-user-manual", "Daon 사용자 설명서"],
  ["daon-knowledge-llm-guide", "Daon 지식·LLM 활용 가이드"],
];

test("한국어 Manual 정본 3종은 실제 절차·권한·오류·준비 상태를 명시한다", async () => {
  for (const [id, title] of docs) {
    const markdown = await readFile(new URL(`docs/manual/${id}/index.md`, root), "utf8");
    assert.match(markdown, new RegExp(`# ${title.replace(/[.*+?^${}()|[\]\\]/gu, "\\$&")}`));
    for (const heading of ["목적", "접근 경로", "조작", "예상 결과", "제한·오류 대응"]) assert.match(markdown, new RegExp(`##+ .*${heading}`, "u"));
    assert.match(markdown, /공개 범위|로그인 후 조직 전용/u);
    assert.doesNotMatch(markdown, /(?:localhost|127\.0\.0\.1|postgres(?:ql)?:\/\/|\/home\/ubuntu|DAON_[A-Z0-9_]*(?:KEY|TOKEN|SECRET)|BEGIN (?:RSA )?PRIVATE KEY)/u);
  }
  const gettingStarted = await readFile(new URL("docs/manual/daon-getting-started/index.md", root), "utf8");
  for (const step of ["Notebook 생성", "Source 추가", "질문", "Citation", "Studio 산출물"]) assert.match(gettingStarted, new RegExp(step, "u"));
  assert.match(gettingStarted, /준비 중|미구현|미검증/u);
});

test("기존 사용자 매뉴얼은 보호된 초기 관리자와 사용자 관리·콘솔 복구 절차를 제공한다", async () => {
  const [gettingStarted, userManual] = await Promise.all([
    readFile(new URL("docs/manual/daon-getting-started/index.md", root), "utf8"),
    readFile(new URL("docs/manual/daon-user-manual/index.md", root), "utf8"),
  ]);
  for (const markdown of [gettingStarted, userManual]) {
    for (const required of [
      /로그인 ID.*admin/u,
      /이메일.*없/u,
      /(?:최초 시작|최초).*console reset|console reset.*직후|서버 콘솔.*초기화.*직후/su,
      /(?:최초 시작|최초|서버 콘솔.*초기화).*직후에만 `admin\/admin`/su,
      /\/password-change/u,
      /Notebook.*보이지 않/u,
      /삭제.*중지.*(?:할 수 없|불가)/u,
      /시스템 관리자(?:에게)?만.*`사용자 관리`/u,
      /사용자 검색/u,
      /상태 필터/u,
      /중지/u,
      /재활성화/u,
      /보호됨/u,
      /일반 사용자.*\/admin.*403/u,
      /보호(?:된)? admin.*중지 요청.*HTTP 409.*`PROTECTED_ADMIN_ACCOUNT`/su,
      /python -m daon_user_api\.admin_cli reset-initial-password/u,
      /INITIAL_ADMIN_PASSWORD_RESET/u,
      /runtime 실패.*`INITIAL_ADMIN_PASSWORD_RESET_FAILED:<오류 코드>`.*(?:exit|종료 코드) 1/isu,
      /잘못된 인자.*usage.*(?:exit|종료 코드) 2/su,
      /비밀번호 변경 성공.*모든 기존 Session.*refresh family.*폐기.*`admin\/admin`.*즉시 무효/su,
      /Workspace.*Notebook.*Source.*Studio/su,
    ]) assert.match(markdown, required);
    assert.doesNotMatch(markdown, /보호(?:된)? admin.*(?:모든 )?상태 변경 요청.*HTTP 409/su);
    assert.doesNotMatch(markdown, /##+ .*조직\s*(?:가입|관리|관리자)|설정.{0,30}조직\s*가입|\/organization(?:-admin|\/join)|(?:조직|organization).{0,50}(?:초대\s*코드|가입\s*신청|가입하기|관리\s*(?:화면|콘솔))/isu);
    assert.doesNotMatch(markdown, /(?:[A-Za-z]:\\|\/srv\/|\/opt\/|\/var\/www\/|DAON_CLOUD_DATABASE_DSN\s*=|postgres(?:ql)?:\/\/|argon2(?:id)?\$|Bearer\s+[A-Za-z0-9._~-]+)/u);
  }
  assert.match(userManual, /Workspace 관리자.*Workspace 내부.*(?:정책|멤버 권한)/su);
  assert.match(userManual, /Workspace 관리자 역할만으로는.*`\/admin`.*(?:사용할 수 없|접근할 수 없)/su);
  assert.match(userManual, /시스템 관리자.*전체 계정.*검색.*중지.*재활성화/su);
  assert.match(userManual, /공동 작업자를 초대하거나.*Workspace 권한.*(?:변경|편집).*사용자 화면.*(?:없|제공하지 않)/su);
});

test("정본·dist·Web public copies는 3문서×3형식에서 byte-identical하다", async () => {
  for (const [id] of docs) {
    const [sourceMarkdown, publicMarkdown] = await Promise.all([
      readFile(new URL(`docs/manual/${id}/index.md`, root)),
      readFile(new URL(`apps/web/public/manual/${id}.md`, root)),
    ]);
    assert.deepEqual(publicMarkdown, sourceMarkdown, `${id}.md public copy must equal source bytes`);
    for (const extension of ["docx", "pdf"]) {
      const [dist, published] = await Promise.all([
        readFile(new URL(`docs/manual/dist/${id}.${extension}`, root)),
        readFile(new URL(`apps/web/public/manual/${id}.${extension}`, root)),
      ]);
      assert.deepEqual(published, dist, `${id}.${extension} public copy must equal dist bytes`);
    }
  }
});

test("Release/Web manifest는 source·dist·public 실제 bytes와 deterministic hash를 결속한다", async () => {
  const [releaseText, webText] = await Promise.all([
    readFile(new URL("docs/manual/release-manifest.json", root), "utf8"),
    readFile(new URL("apps/web/public/manual/manifest.json", root), "utf8"),
  ]);
  assert.equal(webText, releaseText);
  const manifest = JSON.parse(releaseText);
  assert.equal(manifest.schema_version, 1);
  assert.equal(manifest.language, "ko-KR");
  assert.equal(manifest.documents.length, 3);
  for (const [id] of docs) {
    const document = manifest.documents.find((entry) => entry.document_id === id);
    assert.ok(document);
    assert.equal(document.version, manifest.release_version);
    assert.match(document.auth_scope, /^(?:public|authenticated|public_and_authenticated)$/u);
    assert.deepEqual(Object.keys(document.assets).sort(), ["docx", "markdown", "pdf"]);
    for (const [kind, asset] of Object.entries(document.assets)) {
      assert.ok(Number.isInteger(asset.bytes) && asset.bytes > 0);
      assert.match(asset.sha256, /^[0-9a-f]{64}$/u);
      assert.match(asset.href, /^\/manual\/[a-z0-9-]+\.(?:md|docx|pdf)$/u);
      const publicBytes = await readFile(new URL(`apps/web/public${asset.href}`, root));
      const canonicalBytes = kind === "markdown"
        ? await readFile(new URL(`docs/manual/${id}/index.md`, root))
        : await readFile(new URL(`docs/manual/dist/${id}.${kind}`, root));
      assert.deepEqual(publicBytes, canonicalBytes);
      assert.equal(asset.bytes, canonicalBytes.byteLength);
      assert.equal(asset.sha256, createHash("sha256").update(canonicalBytes).digest("hex"));
    }
  }
});

test("Evidence manifest의 Network 계수는 실제 JSONL row와 unique path를 결속한다", {
  skip: !existsSync(privateNetworkEvidence) || !existsSync(privateEvidenceManifest),
}, async () => {
  const [networkText, evidenceText] = await Promise.all([
    readFile(privateNetworkEvidence, "utf8"),
    readFile(privateEvidenceManifest, "utf8"),
  ]);
  const rows = networkText.trim().split(/\r?\n/u).map((line) => JSON.parse(line));
  const uniquePaths = new Set(rows.map((row) => row.path));
  const evidence = JSON.parse(evidenceText);
  assert.equal(evidence.network.captured_requests, rows.length);
  assert.equal(evidence.network.unique_request_paths, uniquePaths.size);
  assert.equal(rows.length, 11);
  assert.equal(uniquePaths.size, 4);
});
