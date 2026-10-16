import assert from "node:assert/strict";
import { mkdtemp, readdir, rm } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { MinimalEvent, buttonByText, findElements, installMinimalDom } from "./product-studio-dom.mjs";
import { createStudioAction, createStudioGeneration, createStudioVersion, listStudioVersions } from "../../apps/web/lib/product-workspace-api.js";
import { createProductStudioState } from '../../packages/ui/src/product-studio-model.js';

function s23Version(number, target = 'target', content = `정본 ${target} V${number}`) {
  return { output_version_id: `version-${target}-${number}`, content_version: number,
    previous_version_id: number > 1 ? `version-${target}-${number - 1}` : null, status: 'draft', content,
    revision_type: number > 1 ? 'user_edit' : 'initial', change_reason: number > 1 ? `수정 사유 ${number}` : '최초 생성',
    settings_snapshot_id: `settings-${target}`, output_format: 'csv',
    citations: [{ citation_id: 'citation-1', source_version_id: 'source-version-1', evidence_span_id: 'span-1',
      origin: 'raw_source', locator: { kind: 'section', value: '시험 근거' } }],
    review_request_id: null, approval_request_id: null, approval_id: null, delivery_id: null, knowledge_registration_id: null };
}
function s23Read(versions, target = 'target', workspace = 'workspace-1') {
  return Response.json({ data: { output_id: `output-${target}`, versions }, meta: { trace_id: 'trace-1', workspace_id: workspace } });
}
function s23Receipt(number = 2) {
  return Response.json({ data: { output_version_id: `version-target-${number}`, status: 'draft', content: `접수된 편집 ${number}`,
    settings_snapshot_id: 'settings-target' }, meta: { trace_id: 'trace-1' } }, { status: 201 });
}
function s23State(workspaceId = 'workspace-1') {
  return { ...createProductStudioState({ workspaceId, outputs: ['target', 'other'].map(target => ({ ...s23Version(1, target),
    studio_output_id: `output-${target}`, output_type: 'comparison_table', title: target === 'target' ? '대상 비교표' : '다른 산출물',
    source_version_ids: ['source-version-1'], source_count: 1 })) }), selectedOutputId: 'output-target' };
}
async function withS23Studio(fetchImpl, run) {
  const rootPath = path.resolve(import.meta.dirname, '../..');
  const output = await mkdtemp(path.join(rootPath, '.product-studio-s23-'));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { createElement, act } = await import('react'); const { createRoot } = await import('react-dom/client');
    const { build } = await import('vite');
    await build({ configFile: false, logLevel: 'silent', root: rootPath, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(rootPath, 'packages/ui/src/product-studio-pane.jsx'), formats: ['es'], fileName: 'product-studio' },
      rollupOptions: { external: ['react', 'react-dom', 'react-dom/client'] } } });
    const entry = (await readdir(output)).find(name => name.startsWith('product-studio') && /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(pathToFileURL(path.join(output, entry)).href);
    const container = dom.document.createElement('div'); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    const adapterFor = (workspace = 'workspace-1') => ({
      createStudioVersion: (id, input, options) => createStudioVersion(workspace, id, input, { ...options, notebookId: 'notebook-1', fetchImpl }),
      listStudioVersions: (id, options) => listStudioVersions(workspace, id, { ...options, notebookId: 'notebook-1', fetchImpl }),
    });
    const render = (state, adapter) => act(async () => reactRoot.render(createElement(ProductStudioPane, { state, adapter })));
    await render(s23State(), adapterFor());
    const edit = async (text = '미저장 편집', reason = '미저장 사유') => act(async () => {
      s22Props(s22Field(container, '편집 내용', 'TEXTAREA')).onChange({ currentTarget: { value: text } });
      s22Props(s22Field(container, '변경 사유', 'INPUT')).onChange({ currentTarget: { value: reason } });
    });
    const click = label => act(async () => buttonByText(container, label).dispatchEvent(new MinimalEvent('click')));
    const detail = () => findElements(container, node => node.getAttribute?.('aria-label') === '선택 산출물 상세')[0];
    await run({ container, act, render, adapterFor, edit, click, detail });
  } finally {
    if (reactRoot) { const { act } = await import('react'); await act(async () => reactRoot.unmount()); }
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
}

test('S23 different output selection separates unsaved content and reason', async () => {
  await withS23Studio(async () => s23Read([s23Version(1, 'other')], 'other'), async ({ container, edit, click, detail }) => {
    await edit(); await click('다른 산출물');
    assert.match(detail().textContent, /정본 other V1/);
    assert.equal(s22Field(container, '편집 내용', 'TEXTAREA').value, '');
    assert.equal(s22Field(container, '변경 사유', 'INPUT').value, '');
    assert.equal(buttonByText(container, '편집 새 Version').disabled, true);
  });
});

test('S23 same-turn opening handlers share one pending canonical read', async () => {
  const pending = s22Deferred(); let reads = 0;
  await withS23Studio(async () => { reads++; await pending.promise; return s23Read([s23Version(1)]); },
    async ({ container, act }) => {
      const handler = s22Props(buttonByText(container, '대상 비교표')).onClick;
      await act(async () => { handler(); handler(); });
      try { assert.equal(reads, 1); } finally { await act(async () => pending.resolve()); }
    });
});

test('S23 adapter and workspace replacement separate drafts and saved receipts', async t => {
  for (const workspace of ['workspace-1', 'workspace-2']) await t.test(workspace, async () => {
    await withS23Studio(async (_url, init) => init.method === 'POST' ? s23Receipt() : s23Read([s23Version(2), s23Version(1)]),
      async ({ container, edit, click, detail, render, adapterFor }) => {
        await edit(); await click('편집 새 Version'); assert.match(detail().textContent, /새 Version이 저장되었습니다/);
        await edit('다음 미저장 편집', '다음 미저장 사유');
        await render(s23State(workspace), adapterFor(workspace));
        assert.equal(s22Field(container, '편집 내용', 'TEXTAREA').value, '');
        assert.equal(s22Field(container, '변경 사유', 'INPUT').value, '');
        assert.doesNotMatch(detail().textContent, /새 Version이 저장되었습니다/);
        assert.match(detail().textContent, /정본 target V1/);
      });
  });
});

test('S23 same-turn revision handlers write once and resolve the returned V2 rather than V3', async () => {
  const pending = s22Deferred(); const writes = [];
  await withS23Studio(async (_url, init) => {
    if (init.method === 'POST') { writes.push({ ...init, input: JSON.parse(init.body) }); await pending.promise; return s23Receipt(); }
    return s23Read([s23Version(3), s23Version(2), s23Version(1)]);
  }, async ({ container, edit, act, detail }) => {
    await edit('편집한 본문', '검토 반영');
    const handler = s22Props(buttonByText(container, '편집 새 Version')).onClick;
    await act(async () => { handler(); handler(); });
    try {
      assert.equal(writes.length, 1);
      assert.deepEqual(writes[0].input, { workspace_id: 'workspace-1', notebook_id: 'notebook-1', previous_version_id: 'version-target-1',
        revision_type: 'user_edit', change_reason: '검토 반영', content: '편집한 본문' });
      assert.equal(s22Field(container, '편집 내용', 'TEXTAREA').disabled, true);
      assert.equal(s22Field(container, '변경 사유', 'INPUT').disabled, true);
    } finally { await act(async () => pending.resolve()); }
    assert.match(detail().textContent, /정본 target V2/);
    assert.doesNotMatch(detail().textContent, /정본 target V3/);
    assert.match(detail().textContent, /Version 3.*Version 2.*Version 1/);
    assert.match(detail().textContent, /원본 지식 · 시험 근거/);
    assert.match(detail().textContent, /새 Version이 저장되었습니다. \(version-target-2\)/);
  });
});

test('S23 active revision blocks type entry after a reread and permits generation only after revision completion', async () => {
  const write = s22Deferred(), generation = s22Deferred(); let committed = false, generationCalls = 0;
  const fetchImpl = async (url, init) => {
    if (url === '/bff/api/studio-generation-requests') {
      generationCalls++; await generation.promise;
      return Response.json({ data: { job_id: 'job-after-revision', status: 'completed', studio_output_id: 'output-after-revision',
        output_version_id: 'version-after-revision', title: '편집 종료 후 생성', output_type: 'comparison_table', content: { rows: [] } } }, { status: 201 });
    }
    if (init.method === 'POST') { await write.promise; committed = true; return s23Receipt(); }
    return s23Read(committed ? [s23Version(2), s23Version(1)] : [s23Version(1)]);
  };
  await withS23Studio(fetchImpl, async ({ container, render, adapterFor, edit, click, act }) => {
    const adapter = { ...adapterFor(), createGeneration: (input, options) => createStudioGeneration('workspace-1', input,
      { ...options, notebookId: 'notebook-1', fetchImpl }) };
    await render({ ...s23State(), sources: s22Sources, settings: { sourceVersionIds: ['source-version-1'] } }, adapter);
    try {
      await edit(); await click('편집 새 Version'); await click('대상 비교표');
      const tile = buttonByText(container, '비교·데이터 표');
      assert.equal(tile.disabled, true, 'a completed reread does not release an active revision');
      await act(async () => s22Props(tile).onClick());
      assert.equal(findElements(container, node => node.getAttribute?.('data-studio-view') === 'config').length, 0);
      assert.equal(generationCalls, 0);
      await act(async () => write.resolve());
      assert.equal(buttonByText(container, '비교·데이터 표').disabled, false);
      await click('비교·데이터 표');
      for (const [label, tag, value] of [
        ['목적', 'INPUT', '후속 비교'], ['독자', 'INPUT', '담당자'], ['분량', 'SELECT', 'short'],
        ['구성', 'SELECT', 'summary-body-conclusion'], ['출력 형식', 'SELECT', 'csv'], ['검토 조건', 'SELECT', 'review_required'],
      ]) await act(async () => s22Props(s22Field(container, label, tag)).onChange({ currentTarget: { value } }));
      await click('설정 확인');
      await act(async () => findElements(container, node => node.tagName === 'FORM')[0].dispatchEvent(new MinimalEvent('submit')));
      assert.equal(generationCalls, 1);
      assert.match(container.textContent, /산출물을 생성하고 있습니다/);
      assert.equal(buttonByText(container, '생성').disabled, true);
      await act(async () => generation.resolve());
      assert.match(container.textContent, /편집 종료 후 생성/);
    } finally { await act(async () => { write.resolve(); generation.resolve(); }); }
  });
});

test('S23 stale confirmed submit handler cannot start generation while a revision owns the request', async () => {
  const write = s22Deferred(); let generationCalls = 0;
  const fetchImpl = async (_url, init) => {
    if (init.method === 'POST') { await write.promise; return s23Receipt(); }
    return s23Read([s23Version(2), s23Version(1)]);
  };
  await withS23Studio(fetchImpl, async ({ container, render, adapterFor, edit, click, act }) => {
    const adapter = { ...adapterFor(), createGeneration: async () => { generationCalls++; return {
      status: 'completed', studio_output_id: 'forbidden-generation', title: '금지된 동시 생성' }; } };
    await render({ ...s22Confirmed(), outputs: s23State().outputs, selectedOutputId: 'output-target' }, adapter);
    const submit = s22Props(findElements(container, node => node.tagName === 'FORM')[0]).onSubmit;
    await edit(); await click('편집 새 Version');
    try {
      await act(async () => submit({ preventDefault() {} }));
      assert.equal(generationCalls, 0);
      assert.doesNotMatch(container.textContent, /금지된 동시 생성/);
    } finally { await act(async () => write.resolve()); }
  });
});

test('S23 stale opening success or failure cannot overwrite a newly saved canonical version', async t => {
  for (const outcome of ['resolve', 'reject']) await t.test(outcome, async () => {
    const stale = s22Deferred(); let targetReads = 0;
    await withS23Studio(async (url, init) => {
      if (init.method === 'POST') return s23Receipt();
      if (url.includes('output-other/')) return s23Read([s23Version(1, 'other')], 'other');
      targetReads++;
      if (targetReads === 1) return stale.promise;
      return s23Read(targetReads === 2 ? [s23Version(1)] : [s23Version(3), s23Version(2), s23Version(1)]);
    }, async ({ container, edit, click, act, detail }) => {
      await click('대상 비교표'); await click('다른 산출물'); await click('대상 비교표');
      await edit(); await click('편집 새 Version');
      await act(async () => outcome === 'resolve' ? stale.resolve(s23Read([s23Version(1)])) : stale.reject(new Error('old private failure')));
      assert.match(detail().textContent, /정본 target V2/);
      assert.match(detail().textContent, /새 Version이 저장되었습니다/);
      assert.doesNotMatch(container.textContent, /저장된 산출물을 불러오지 못했습니다|old private failure/);
    });
  });
});

test('S23 old opening failure cannot release another pending detail read', async () => {
  const stale = s22Deferred(), current = s22Deferred();
  await withS23Studio(async url => url.includes('output-other/') ? current.promise : stale.promise,
    async ({ container, click, edit, act, detail }) => {
      await click('대상 비교표'); await click('다른 산출물');
      await act(async () => stale.reject(new Error('STUDIO_LIST_FAILED')));
      try {
        await edit();
        assert.equal(buttonByText(container, '편집 새 Version').disabled, true);
        assert.doesNotMatch(container.textContent, /저장된 산출물을 불러오지 못했습니다/);
      } finally { await act(async () => current.resolve(s23Read([s23Version(1, 'other')], 'other'))); }
      assert.match(detail().textContent, /정본 other V1/);
    });
});

test('S23 successful write keeps its library receipt but later read failure cannot affect another detail', async () => {
  const write = s22Deferred(); let targetReads = 0;
  await withS23Studio(async (url, init) => {
    if (init.method === 'POST') { await write.promise; return s23Receipt(); }
    if (url.includes('output-other/')) return s23Read([s23Version(1, 'other')], 'other');
    if (++targetReads === 1) throw new Error('STUDIO_LIST_FAILED');
    return s23Read([s23Version(2), s23Version(1)]);
  }, async ({ container, edit, click, act, detail }) => {
    await edit(); await click('편집 새 Version'); await click('다른 산출물');
    await act(async () => write.resolve());
    assert.match(detail().textContent, /정본 other V1/);
    assert.doesNotMatch(container.textContent, /저장된 산출물을 불러오지 못했습니다/);
    assert.doesNotMatch(detail().textContent, /새 Version이 저장되었습니다/);
    assert.equal(s22Field(container, '편집 내용', 'TEXTAREA').value, '');
    await click('대상 비교표'); assert.match(detail().textContent, /정본 target V2/);
  });
});

test('S23 old revision completion cannot release another pending detail read', async t => {
  for (const outcome of ['read-success', 'write-failure']) await t.test(outcome, async () => {
    const write = s22Deferred(), other = s22Deferred();
    await withS23Studio(async (url, init) => {
      if (init.method === 'POST') { await write.promise; if (outcome === 'write-failure') throw new Error('private write failure'); return s23Receipt(); }
      if (url.includes('output-other/')) return other.promise;
      return s23Read([s23Version(2), s23Version(1)]);
    }, async ({ container, edit, click, act, detail }) => {
      await edit(); await click('편집 새 Version'); await click('다른 산출물');
      await act(async () => write.resolve());
      try {
        assert.equal(s22Field(container, '편집 내용', 'TEXTAREA').disabled, true);
        assert.doesNotMatch(container.textContent, /새 버전을 저장하지 못했습니다|private write failure/);
      } finally { await act(async () => other.resolve(s23Read([s23Version(1, 'other')], 'other'))); }
      assert.match(detail().textContent, /정본 other V1/);
    });
  });
});

test('S23 consecutive explicit edits use canonical V2 then V3 with previous-version linkage', async () => {
  const previous = []; let number = 1;
  await withS23Studio(async (_url, init) => {
    if (init.method === 'POST') { previous.push(JSON.parse(init.body).previous_version_id); number++; return s23Receipt(number); }
    return s23Read(number === 2 ? [s23Version(2), s23Version(1)] : [s23Version(3), s23Version(2), s23Version(1)]);
  }, async ({ edit, click, detail }) => {
    await edit('V2 편집', '첫 수정'); await click('편집 새 Version');
    assert.match(detail().textContent, /정본 target V2/);
    await edit('V3 편집', '두 번째 수정'); await click('편집 새 Version');
    assert.match(detail().textContent, /정본 target V3/);
    assert.match(detail().textContent, /Version 3.*수정 사유 3.*Version 2.*수정 사유 2.*Version 1/);
    assert.deepEqual(previous, ['version-target-1', 'version-target-2']);
    assert.match(detail().textContent, /원본 지식 · 시험 근거/);
  });
});

test('S23 late revision after adapter replacement cannot change the new scope', async t => {
  for (const outcome of ['resolve', 'reject']) await t.test(outcome, async () => {
    const write = s22Deferred(); let signal;
    await withS23Studio(async (_url, init) => {
      if (init.method === 'POST') { signal = init.signal; return write.promise; }
      return s23Read([s23Version(1)]);
    }, async ({ container, edit, click, render, adapterFor, act, detail }) => {
      await edit(); await click('편집 새 Version');
      await render(s23State('workspace-2'), adapterFor('workspace-2'));
      await act(async () => outcome === 'resolve' ? write.resolve(s23Receipt()) : write.reject(new Error('STUDIO_VERSION_FAILED')));
      assert.equal(signal?.aborted, true);
      assert.match(detail().textContent, /정본 target V1/);
      assert.doesNotMatch(container.textContent, /새 Version이 저장되었습니다|새 버전을 저장하지 못했습니다/);
      assert.equal(s22Field(container, '변경 사유', 'INPUT').value, '');
    });
  });
});

test('S23 committed write with failed canonical read retains receipt and permits explicit reentry', async () => {
  let reads = 0, writes = 0;
  await withS23Studio(async (_url, init) => {
    if (init.method === 'POST') { writes++; return s23Receipt(); }
    if (++reads === 1) throw new Error('private canonical read failure');
    return s23Read([s23Version(2), s23Version(1)]);
  }, async ({ container, edit, click, detail }) => {
    await edit(); await click('편집 새 Version');
    assert.equal(writes, 1);
    assert.match(detail().textContent, /새 Version이 저장되었습니다. \(version-target-2\)/);
    assert.match(detail().textContent, /정본 조회가 아직 확인되지 않았습니다/);
    assert.match(container.textContent, /저장된 산출물을 불러오지 못했습니다/);
    assert.doesNotMatch(container.textContent, /새 버전을 저장하지 못했습니다|private canonical read failure/);
    await click('대상 비교표');
    assert.match(detail().textContent, /정본 target V2/); assert.equal(writes, 1);
    assert.doesNotMatch(detail().textContent, /정본 조회가 아직 확인되지 않았습니다/);
    assert.match(detail().textContent, /Version 2 · 근거 1 · 설정 settings-target/);
    assert.match(detail().textContent, /원본 지식 · 시험 근거/);
  });
});

test('S23 partial receipt must not present inherited content or citations as the new canonical version', async t => {
  for (const shape of ['id-only', 'content-only']) await t.test(shape, async () => {
    await withS23Studio(async (_url, init) => {
      if (init.method === 'POST') return Response.json({ data: { output_version_id: 'version-target-2',
        ...(shape === 'content-only' ? { content: 'POST가 반환한 새 본문', status: 'draft', settings_snapshot_id: 'settings-target' } : {}) },
        meta: { trace_id: 'trace-1' } }, { status: 201 });
      throw new Error('STUDIO_LIST_FAILED');
    }, async ({ edit, click, detail }) => {
      await edit(); await click('편집 새 Version');
      assert.match(detail().textContent, /새 Version이 저장되었습니다. \(version-target-2\)/);
      assert.doesNotMatch(detail().textContent, /정본 target V1/);
      assert.equal(findElements(detail(), node => node.getAttribute?.('aria-label') === '근거 Citation').length, 0,
        'the previous version citations are not canonical proof for this new receipt');
      assert.match(detail().textContent, /정본 조회가 아직 확인되지 않았습니다/);
      assert.match(detail().textContent, /근거 미확인/);
      assert.doesNotMatch(detail().textContent, /Version 1|Version 이력/);
      if (shape === 'id-only') {
        assert.match(detail().textContent, /설정 미확인/);
        assert.doesNotMatch(detail().textContent, /settings-target|초안/);
      } else {
        assert.match(detail().textContent, /POST가 반환한 새 본문/);
        assert.match(detail().textContent, /설정 settings-target/);
      }
    });
  });
});

const s22Sources = [{ sourceId: 'source-1', sourceVersionId: 'source-version-1', filename: 'QA 시험 문서.txt', ready: true }];
const s22Settings = { purpose: '시험 비교', audience: '검토자', language: 'ko', sourceVersionIds: ['source-version-1'],
  length: 'short', structure: 'summary-body-conclusion', outputFormat: 'csv', reviewCondition: 'review_required' };
function s22Confirmed(workspaceId = 'workspace-1') {
  return { ...createProductStudioState({ workspaceId, sources: s22Sources, selectedSourceVersionIds: ['source-version-1'] }),
    selectedOutputType: 'comparison_table', settings: { ...s22Settings }, settingsSnapshot: { ...s22Settings }, settingsConfirmed: true };
}
function s22Deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function s22Props(element) { return element[Object.keys(element).find(key => key.startsWith('__reactProps'))]; }
function s22Field(container, text, tag) {
  const label = findElements(container, node => node.tagName === 'LABEL' && node.textContent.startsWith(text))[0];
  assert.ok(label, text);
  return findElements(label, node => node.tagName === tag)[0];
}
async function withS22Studio(state, adapter, run) {
  const rootPath = path.resolve(import.meta.dirname, '../..');
  const output = await mkdtemp(path.join(rootPath, '.product-studio-s22-'));
  const dom = installMinimalDom(); let reactRoot;
  try {
    const { createElement, act } = await import('react');
    const { createRoot } = await import('react-dom/client'); const { build } = await import('vite');
    await build({ configFile: false, logLevel: 'silent', root: rootPath, build: { outDir: output, emptyOutDir: false,
      lib: { entry: path.join(rootPath, 'packages/ui/src/product-studio-pane.jsx'), formats: ['es'], fileName: 'product-studio' },
      rollupOptions: { external: ['react', 'react-dom', 'react-dom/client'] } } });
    const entry = (await readdir(output)).find(name => name.startsWith('product-studio') && /\.m?js$/u.test(name));
    const { ProductStudioPane } = await import(pathToFileURL(path.join(output, entry)).href);
    const container = dom.document.createElement('div'); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    const render = (nextState, nextAdapter) => act(async () => reactRoot.render(createElement(ProductStudioPane, { state: nextState, adapter: nextAdapter })));
    await render(state, adapter);
    await run({ container, act, render });
  } finally {
    if (reactRoot) { const { act } = await import('react'); await act(async () => reactRoot.unmount()); }
    dom.restore(); await rm(output, { recursive: true, force: true });
  }
}

test('S22 React incomplete settings cannot confirm or submit', async () => {
  let calls = 0;
  await withS22Studio(createProductStudioState({ workspaceId: 'workspace-1', sources: s22Sources }),
    { createGeneration: async () => { calls++; } }, async ({ container, act }) => {
      await act(async () => buttonByText(container, '비교·데이터 표').dispatchEvent(new MinimalEvent('click')));
      assert.equal(buttonByText(container, '설정 확인').disabled, true);
      await act(async () => buttonByText(container, '설정 확인').dispatchEvent(new MinimalEvent('click')));
      assert.equal(buttonByText(container, '생성').disabled, true);
      assert.match(container.textContent, /설정을 확인해 주세요/);
      assert.equal(calls, 0);
    });
});

test('S22 React initial locked review is disabled with RuleSet value and reason', async () => {
  const state = createProductStudioState({ workspaceId: 'workspace-1', sources: s22Sources, locks: [
    { field: 'reviewCondition', value: 'review_required', reason: 'WORKSPACE_POLICY' },
    { field: 'rulesetVersionId', value: 'ruleset-v3', reason: 'RULESET_BINDING' },
  ] });
  await withS22Studio(state, {}, async ({ container, act }) => {
    await act(async () => buttonByText(container, '비교·데이터 표').dispatchEvent(new MinimalEvent('click')));
    const review = s22Field(container, '검토 조건', 'SELECT');
    assert.equal(s22Props(review).value, 'review_required'); assert.equal(review.disabled, true);
    assert.equal(s22Field(container, '목적', 'INPUT').disabled, false);
    assert.match(container.textContent, /RuleSet.*ruleset-v3/);
    assert.match(container.textContent, /검토 조건.*review_required.*WORKSPACE_POLICY/);
    assert.match(container.textContent, /RULESET_BINDING/);
    await act(async () => s22Props(review).onChange({ currentTarget: { value: 'approval_required' } }));
    assert.equal(s22Props(s22Field(container, '검토 조건', 'SELECT')).value, 'review_required');
  });
});

test('S22 React editing confirmed settings requires confirmation again and submits the frozen selection', async () => {
  const inputs = [];
  const state = createProductStudioState({ workspaceId: 'workspace-1', sources: s22Sources,
    selectedSourceVersionIds: ['source-version-1'], locks: [
      { field: 'reviewCondition', value: 'review_required', reason: 'WORKSPACE_POLICY' },
      { field: 'rulesetVersionId', value: 'ruleset-v3', reason: 'RULESET_BINDING' },
    ] });
  const adapter = { createGeneration: async input => { inputs.push(input); return {
    status: 'completed', studio_output_id: 'configured-output', output_version_id: 'configured-version',
    title: '설정된 비교표', output_type: 'comparison_table', content: { rows: [] },
  }; } };
  await withS22Studio(state, adapter, async ({ container, act }) => {
    await act(async () => buttonByText(container, '비교·데이터 표').dispatchEvent(new MinimalEvent('click')));
    for (const [label, tag, value] of [
      ['목적', 'INPUT', '시험 비교'], ['독자', 'INPUT', '검토자'], ['분량', 'SELECT', 'short'],
      ['구성', 'SELECT', 'summary-body-conclusion'], ['출력 형식', 'SELECT', 'csv'],
    ]) await act(async () => s22Props(s22Field(container, label, tag)).onChange({ currentTarget: { value } }));
    assert.equal(buttonByText(container, '설정 확인').disabled, false);
    assert.equal(buttonByText(container, '생성').disabled, true);
    await act(async () => buttonByText(container, '설정 확인').dispatchEvent(new MinimalEvent('click')));
    assert.equal(buttonByText(container, '생성').disabled, false);
    await act(async () => s22Props(s22Field(container, '독자', 'INPUT')).onChange({ currentTarget: { value: '업무 담당자' } }));
    assert.equal(buttonByText(container, '생성').disabled, true);
    await act(async () => buttonByText(container, '설정 확인').dispatchEvent(new MinimalEvent('click')));
    await act(async () => findElements(container, node => node.tagName === 'FORM')[0].dispatchEvent(new MinimalEvent('submit')));
    assert.deepEqual(inputs, [{ workspace_id: 'workspace-1', output_type: 'comparison_table', source_only: true,
      source_id: 'source-1', source_version_ids: ['source-version-1'], run_id: null, run_result_id: null,
      settings: { purpose: '시험 비교', audience: '업무 담당자', language: 'ko', source_version_ids: ['source-version-1'],
        ruleset_version_id: 'ruleset-v3', length: 'short', structure: 'summary-body-conclusion', output_format: 'csv', review_condition: 'review_required' } }]);
    assert.match(container.textContent, /설정된 비교표/);
  });
});

test('S22 React new config does not announce the previous library output as saved', async () => {
  const output = { studio_output_id: 'saved-output', title: '이전 비교표', output_type: 'comparison_table', status: 'approved', content: { rows: [] } };
  await withS22Studio({ ...createProductStudioState({ sources: s22Sources, outputs: [output] }), selectedOutputId: 'saved-output' }, {},
    async ({ container, act }) => {
      await act(async () => buttonByText(container, '비교·데이터 표').dispatchEvent(new MinimalEvent('click')));
      assert.equal(findElements(container, node => node.getAttribute?.('data-generation-state') === 'idle').length, 1);
      assert.doesNotMatch(container.textContent, /산출물이 저장되었습니다/);
      assert.equal(findElements(container, node => node.getAttribute?.('aria-label') === '선택 산출물 상세').length, 0);
      await act(async () => buttonByText(container, '뒤로').dispatchEvent(new MinimalEvent('click')));
      assert.ok(buttonByText(container, '이전 비교표'));
    });
});

test('S22 React summary describes actual structuring without claiming a model call', async t => {
  for (const [kind, grounded, expected] of [
    ['source-only', null, /선택 Source 구조화.*추가 LLM 호출 없음/],
    ['grounded', { sourceId: 'source-1', sourceVersionIds: ['source-version-1'], runId: 'run-1', runResultId: 'result-1' }, /저장된 답변·근거 구조화.*추가 LLM 호출 없음/],
  ]) await t.test(kind, async () => {
    await withS22Studio({ ...s22Confirmed(), grounded }, {}, async ({ container }) => {
      assert.match(container.textContent, expected);
      assert.doesNotMatch(container.textContent, /Workspace 기본 모델|생성 시 현재 선택을 검증합니다/);
    });
  });
});

test('S22 React same-turn submits enqueue only once and freeze pending settings', async () => {
  const pending = s22Deferred(); const calls = [];
  const adapter = { createGeneration: (input, options) => { calls.push({ input, options }); return pending.promise; } };
  await withS22Studio(s22Confirmed(), adapter, async ({ container, act }) => {
    const form = findElements(container, node => node.tagName === 'FORM')[0];
    await act(async () => { form.dispatchEvent(new MinimalEvent('submit')); form.dispatchEvent(new MinimalEvent('submit')); });
    try {
      assert.equal(calls.length, 1);
      assert.equal(calls[0].input.output_type, 'comparison_table');
      assert.deepEqual(calls[0].input.source_version_ids, ['source-version-1']);
      assert.equal(calls[0].input.source_only, true);
      assert.ok(calls[0].options.signal instanceof AbortSignal);
      assert.equal(buttonByText(container, '생성').disabled, true);
      assert.equal(buttonByText(container, '설정 확인').disabled, true);
      assert.equal(buttonByText(container, '뒤로').disabled, true);
      for (const field of findElements(form, node => ['INPUT', 'SELECT'].includes(node.tagName))) assert.equal(field.disabled, true);
      assert.match(container.textContent, /산출물을 생성하고 있습니다/);
    } finally {
      await act(async () => pending.resolve({ status: 'completed', studio_output_id: 'new-output', output_version_id: 'new-version',
        output_type: 'comparison_table', title: '새 비교표', content: { rows: [] }, source_version_ids: ['source-version-1'] }));
    }
    assert.match(container.textContent, /산출물이 저장되었습니다/);
    await act(async () => buttonByText(container, '뒤로').dispatchEvent(new MinimalEvent('click')));
    assert.equal(findElements(container, node => node.tagName === 'BUTTON' && node.textContent === '새 비교표').length, 1);
  });
});

test('S22 React safe failure releases only its request and permits explicit submission', async () => {
  let calls = 0;
  const adapter = { createGeneration: async () => { calls++; if (calls === 1) throw new Error('private response text');
    return { status: 'completed', studio_output_id: 'retry-output', output_version_id: 'retry-version', title: '명시 재제출 결과', content: '저장 내용' }; } };
  await withS22Studio(s22Confirmed(), adapter, async ({ container, act }) => {
    const submit = () => findElements(container, node => node.tagName === 'FORM')[0].dispatchEvent(new MinimalEvent('submit'));
    await act(async () => submit());
    assert.equal(calls, 1); assert.match(container.textContent, /생성을 완료하지 못했습니다/);
    assert.doesNotMatch(container.textContent, /private response text/);
    assert.equal(buttonByText(container, '생성').disabled, false);
    await act(async () => submit());
    assert.equal(calls, 2); assert.match(container.textContent, /명시 재제출 결과/);
  });
});

test('S22 React adapter change ignores late success and failure without releasing a new request', async t => {
  for (const outcome of ['resolve', 'reject']) await t.test(outcome, async () => {
    const old = s22Deferred(), next = s22Deferred(); let oldSignal;
    const first = { createGeneration: (_input, options) => { oldSignal = options?.signal; return old.promise; } };
    const second = { createGeneration: () => next.promise };
    await withS22Studio(s22Confirmed(), first, async ({ container, act, render }) => {
      const submit = () => findElements(container, node => node.tagName === 'FORM')[0].dispatchEvent(new MinimalEvent('submit'));
      await act(async () => submit());
      await render(s22Confirmed('workspace-2'), second);
      try {
        assert.equal(oldSignal?.aborted, true);
        assert.equal(buttonByText(container, '생성').disabled, false);
        await act(async () => submit());
        await act(async () => outcome === 'resolve'
          ? old.resolve({ status: 'completed', studio_output_id: 'old-output', title: '이전 Notebook 응답', content: '이전 본문' })
          : old.reject(new Error('STUDIO_CREATE_FAILED')));
        assert.match(container.textContent, /산출물을 생성하고 있습니다/);
        assert.doesNotMatch(container.textContent, /이전 Notebook 응답|생성을 완료하지 못했습니다/);
      } finally {
        await act(async () => { old.resolve({ status: 'completed', studio_output_id: 'old-output', title: '이전 Notebook 응답' });
          next.resolve({ status: 'completed', studio_output_id: 'current-output', title: '현재 Notebook 결과', content: '현재 본문' }); });
      }
      assert.match(container.textContent, /현재 Notebook 결과/);
    });
  });
});

test('S21 React marks only audio/video file generation unavailable while saved media remains readable',async()=>{
  const rootPath=path.resolve(import.meta.dirname,'../..');
  const output=await mkdtemp(path.join(rootPath,'.product-studio-s21-'));
  const dom=installMinimalDom();let reactRoot;
  try{
    const {createElement,act}=await import('react'),{createRoot}=await import('react-dom/client'),{build}=await import('vite');
    await build({configFile:false,logLevel:'silent',root:rootPath,build:{outDir:output,emptyOutDir:false,
      lib:{entry:path.join(rootPath,'packages/ui/src/product-studio-pane.jsx'),formats:['es'],fileName:'product-studio'},
      rollupOptions:{external:['react','react-dom','react-dom/client']}}});
    const entry=(await readdir(output)).find(name=>name.startsWith('product-studio')&&/\.m?js$/u.test(name));
    const {ProductStudioPane}=await import(pathToFileURL(path.join(output,entry)).href);
    const outputs=['audio','video'].map(type=>({studio_output_id:`output-${type}`,output_version_id:`version-${type}`,
      output_type:type,title:`저장된 ${type}`,status:'draft',content:`기존 ${type} JSON 내용`,content_version:1,
      source_version_ids:['source-version-1'],source_count:1,settings_snapshot_id:`settings-${type}`,citations:[],output_format:'json'}));
    const reads=[];let creates=0;
    const adapter={createGeneration:async()=>{creates++;throw Error('MUST_NOT_GENERATE');},
      listStudioVersions:async id=>{reads.push(id);const saved=outputs.find(value=>value.studio_output_id===id);
        return [{output_version_id:saved.output_version_id,content_version:1,previous_version_id:null,status:'draft',
          content:saved.content,revision_type:'initial',change_reason:'기존 자료',settings_snapshot_id:saved.settings_snapshot_id,
          citations:[],review_request_id:null,approval_request_id:null,approval_id:null,delivery_id:null,knowledge_registration_id:null,output_format:'json'}];}};
    const container=dom.document.createElement('div');dom.document.body.appendChild(container);reactRoot=createRoot(container);
    await act(async()=>reactRoot.render(createElement(ProductStudioPane,{state:createProductStudioState({workspaceId:'workspace-1',outputs}),adapter})));
    const tiles=()=>findElements(container,node=>node.tagName==='BUTTON'&&node.getAttribute?.('class')?.split(' ').includes('studio-type-tile'));
    assert.equal(tiles().length,11);
    for(const label of ['AI 오디오','동영상']){
      const tile=tiles().find(node=>node.textContent.includes(label));assert.ok(tile);
      assert.equal(tile.disabled,true,label);
      assert.match(tile.textContent,/현재 파일 생성 미지원/);
      await act(async()=>tile.dispatchEvent(new MinimalEvent('click')));
      assert.equal(findElements(container,node=>node.getAttribute?.('data-studio-view')==='config').length,0);
    }
    for(const label of ['근거 기반 보고서','제약·준수 점검표','비교·데이터 표','지식 구조도','업무 문서 초안','슬라이드','인포그래픽','플래시카드','퀴즈']){
      const tile=tiles().find(node=>node.textContent===label);assert.ok(tile,label);assert.equal(tile.disabled,false);
      await act(async()=>tile.dispatchEvent(new MinimalEvent('click')));
      assert.match(container.textContent,new RegExp(`${label} 설정`));
      await act(async()=>buttonByText(container,'뒤로').dispatchEvent(new MinimalEvent('click')));
    }
    for(const type of ['audio','video']){
      await act(async()=>buttonByText(container,`저장된 ${type}`).dispatchEvent(new MinimalEvent('click')));
      const detail=findElements(container,node=>node.getAttribute?.('aria-label')==='선택 산출물 상세')[0];
      assert.ok(detail);assert.match(detail.textContent,new RegExp(`기존 ${type} JSON 내용`));
    }
    assert.deepEqual(reads,['output-audio','output-video']);assert.equal(creates,0);
  }finally{
    if(reactRoot){const {act}=await import('react');await act(async()=>reactRoot.unmount());}
    dom.restore();await rm(output,{recursive:true,force:true});
  }
});

test("새 Version 저장 후 정식 조회는 생성 ID·이력·Region을 복원하고 저장/조회 실패를 분리한다", async (t) => {
  const rootPath = path.resolve(import.meta.dirname, "../..");
  const output = await mkdtemp(path.join(rootPath, ".product-studio-version-refresh-"));
  try {
    for (const scenario of ["success", "read-failure", "invalid-region", "invalid-cell", "invalid-docx", "missing-created", "write-failure", "no-getter", "selection-change", "read-selection-change"]) {
      await t.test(scenario, async () => {
        const dom = installMinimalDom(); let reactRoot;
        try {
          const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
          const { build } = await import("vite");
          await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
          const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name));
          const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?case=${scenario}`);
          const citations = [
            { citation_id: "citation-image", source_version_id: "source-version-image", evidence_span_id: "span-image", origin: "raw_source", locator: { kind: "image_region", value: "10,20,30,40,100,200" } },
            { citation_id: "citation-page", source_version_id: "source-version-pdf", evidence_span_id: "span-page", origin: "raw_source", locator: { kind: "page", value: "2" } },
            { citation_id: "citation-section", source_version_id: "source-version-text", evidence_span_id: "span-section", origin: "daon_knowledge", locator: { kind: "section", value: "핵심 요약" } },
            { citation_id: "citation-cell", source_version_id: "source-version-xlsx", evidence_span_id: "span-cell", origin: "raw_source", locator: { kind: "sheet_cell", value: '{"sheet":"납품 계획","range":"B2:C3"}' } },
            { citation_id: "citation-docx", source_version_id: "source-version-docx", evidence_span_id: "span-docx", origin: "raw_source", locator: { kind: "docx_block", value: '{"block":"p:2","start":1,"end":2}' } },
          ];
          const v1 = { output_version_id: "version-1", content_version: 1, previous_version_id: null, status: "draft", content: { body: "이전 본문" }, revision_type: "initial", change_reason: "최초", settings_snapshot_id: "settings-1", citations, review_request_id: null, approval_request_id: null, approval_id: null, delivery_id: null, knowledge_registration_id: null, output_format: "pdf" };
          const v2 = { ...v1, output_version_id: "version-2", content_version: 2, previous_version_id: "version-1", content: { body: "정식 새 본문" }, revision_type: "user_edit", change_reason: "편집 반영", settings_snapshot_id: "settings-2" };
          // A newer history head must not replace the ID returned by this POST.
          const v3 = { ...v2, output_version_id: "version-3", content_version: 3, previous_version_id: "version-2", content: { body: "다른 요청 본문" } };
          const other = { ...v1, output_version_id: "version-other", content: { body: "다른 산출물 본문" }, citations: [] };
          const calls = []; let releaseWrite;
          const waitForWrite = new Promise((resolve) => { releaseWrite = resolve; });
          const fetchImpl = async (url, init) => {
            calls.push({ url, method: init.method, body: init.body ? JSON.parse(init.body) : null });
            if (init.method === "POST") {
              if (scenario === "write-failure") return Response.json({ error: { code: "STUDIO_VERSION_FAILED" } }, { status: 503 });
              if (scenario === "selection-change") await waitForWrite;
              return Response.json({ data: { output_version_id: "version-2", status: "draft", content: "편집 입력", settings_snapshot_id: "settings-2" }, meta: { trace_id: "trace-1" } }, { status: 201 });
            }
            if (url.includes("/output-other/")) return Response.json({ data: { output_id: "output-other", versions: [other] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
            if (scenario === "read-failure") return Response.json({ error: { code: "STUDIO_LIST_FAILED" } }, { status: 503 });
            if (scenario === "read-selection-change") await waitForWrite;
            if (scenario === "invalid-region") return Response.json({ data: { output_id: "output-1", versions: [{ ...v2, citations: [{ ...citations[0], locator: { kind: "image_region", value: "90,20,30,40,100,200" } }] }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
            if (scenario === "invalid-cell") return Response.json({ data: { output_id: "output-1", versions: [{ ...v2, citations: [{ ...citations[3], locator: { kind: "sheet_cell", value: '{"sheet":"납품 계획","range":"A1:Q16"}' } }] }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
            if (scenario === "invalid-docx") return Response.json({ data: { output_id: "output-1", versions: [{ ...v2, citations: [{ ...citations[4], locator: { kind: "docx_block", value: '{"block":"p:2","start":true,"end":2}' } }] }] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
            return Response.json({ data: { output_id: "output-1", versions: scenario === "missing-created" ? [v1] : [v3, v2, v1] }, meta: { trace_id: "trace-1", workspace_id: "workspace-1" } });
          };
          const adapter = {
            createStudioVersion: (id, input) => createStudioVersion("workspace-1", id, input, { notebookId: "notebook-1", fetchImpl, idempotencyKey: "revision-key-0001" }),
            ...(scenario === "no-getter" ? {} : { listStudioVersions: (id) => listStudioVersions("workspace-1", id, { notebookId: "notebook-1", fetchImpl }) }),
          };
          const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: "evidence_report", settings: {}, settingsConfirmed: false, outputs: [
            { ...v1, studio_output_id: "output-1", title: "대상 보고서", output_type: "evidence_report", version: v1, versions: [v1] },
            { ...other, studio_output_id: "output-other", title: "다른 보고서", output_type: "evidence_report" },
          ], selectedOutputId: "output-1", pending: false, safeError: null };
          const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
          await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
          const change = (labelText, tag, value) => {
            const label = findElements(container, (node) => node.tagName === "LABEL" && node.textContent.includes(labelText))[0];
            const input = findElements(label, (node) => node.tagName === tag)[0];
            input[Object.keys(input).find((key) => key.startsWith("__reactProps"))].onChange({ currentTarget: { value } });
          };
          await act(async () => { change("변경 사유", "INPUT", "편집 반영"); change("편집 내용", "TEXTAREA", "편집 입력"); });
          await act(async () => { buttonByText(container, "편집 새 Version").dispatchEvent(new MinimalEvent("click")); });
          if (["selection-change", "read-selection-change"].includes(scenario)) {
            await act(async () => { buttonByText(container, "뒤로").dispatchEvent(new MinimalEvent("click")); });
            await act(async () => { buttonByText(container, "다른 보고서").dispatchEvent(new MinimalEvent("click")); });
            await act(async () => { releaseWrite(); });
          }
          const detail = findElements(container, (node) => node.getAttribute?.("aria-label") === "선택 산출물 상세")[0];
          const posts = calls.filter((call) => call.method === "POST");
          assert.equal(posts.length, 1);
          assert.equal(posts[0].body.previous_version_id, "version-1");
          if (scenario === "write-failure") {
            assert.equal(calls.length, 1); assert.match(detail.textContent, /Version 1 · 근거 5 · 설정 settings-1/);
            assert.doesNotMatch(detail.textContent, /새 Version이 저장되었습니다/);
            assert.match(container.textContent, /새 버전을 저장하지 못했습니다/);
          } else if (["selection-change", "read-selection-change"].includes(scenario)) {
            assert.match(detail.textContent, /다른 산출물 본문/); assert.doesNotMatch(detail.textContent, /정식 새 본문|10,20,30,40,100,200/);
            assert.doesNotMatch(detail.textContent, /새 Version이 저장되었습니다/);
            const library = findElements(container, (node) => node.getAttribute?.("class") === "studio-library")[0];
            const row = findElements(library, (node) => node.tagName === "LI" && node.textContent.includes("대상 보고서"))[0];
            assert.match(row.textContent, /Version 2/);
          } else if (scenario === "success") {
            assert.match(detail.textContent, /새 Version이 저장되었습니다. \(version-2\)/);
            assert.match(detail.textContent, /Version 2 · 근거 5 · 설정 settings-2/); assert.match(detail.textContent, /정식 새 본문/);
            assert.doesNotMatch(detail.textContent, /다른 요청 본문/);
            const history = findElements(detail, (node) => node.getAttribute?.("aria-label") === "Version 이력")[0];
            for (const number of [3, 2, 1]) assert.match(history.textContent, new RegExp(`Version ${number}`));
            for (const locator of ["10,20,30,40,100,200", "2쪽", "핵심 요약", "납품 계획 · B2:C3", "문단·표 p:2 · 1–2"]) assert.ok(detail.textContent.includes(locator));
            assert.deepEqual(calls[1].method,"GET");assert.equal(posts[0].body.previous_version_id,"version-1");
          } else {
            assert.match(detail.textContent, /새 Version이 저장되었습니다. \(version-2\)/);
            assert.match(detail.textContent, /Version version-2 · 근거 미확인 · 설정 settings-2/);
            assert.match(detail.textContent, /정본 조회가 아직 확인되지 않았습니다/);
            assert.match(detail.textContent, /편집 입력/);
            assert.doesNotMatch(detail.textContent, /이전 본문|10,20,30,40,100,200|핵심 요약|납품 계획 · B2:C3|문단·표 p:2 · 1–2/);
            assert.equal(findElements(detail, (node) => node.getAttribute?.("aria-label") === "근거 Citation").length, 0);
            assert.equal(findElements(detail, (node) => node.getAttribute?.("aria-label") === "Version 이력").length, 0);
            if (scenario !== "no-getter") assert.match(container.textContent, /저장된 산출물을 불러오지 못했습니다/);
            assert.doesNotMatch(container.textContent, /새 버전을 저장하지 못했습니다/);
            assert.doesNotMatch(container.textContent, /생성을 완료하지 못했습니다/);
            const contentLabel = findElements(container, (node) => node.tagName === "LABEL" && node.textContent.includes("편집 내용"))[0];
            assert.equal(findElements(contentLabel, (node) => node.tagName === "TEXTAREA")[0].value, "");
          }
          if (!["write-failure", "no-getter"].includes(scenario)) assert.equal(calls.filter((call) => call.method === "GET" && call.url.includes("/output-1/")).length, 1);
        } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); }
      });
    }
  } finally { await rm(output, { recursive: true, force: true }); }
});

test("생성 클릭은 queued·leased·generating 동안 진행을 유지하고 completed 산출물을 표시한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-generating-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const settings = { purpose: "근거 요약", audience: "검토자", language: "ko", sourceVersionIds: ["source-version-1"], length: "short", structure: "summary-body-conclusion", outputFormat: "pdf", reviewCondition: "review_required" };
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, sources: [{ sourceId: "source-1", sourceVersionId: "source-version-1", filename: "자료.pdf", ready: true }], locks: [], selectedOutputType: "evidence_report", settings, settingsConfirmed: true, settingsSnapshot: settings, outputs: [], selectedOutputId: null, pending: false, safeError: null };
    let poll = 0; let sawGenerating;
    const generatingSeen = new Promise((resolve) => { sawGenerating = resolve; });
    const adapter = {
      createGeneration: async () => ({ job_id: "job-1", status: "queued" }),
      getGenerationJob: async () => {
        poll += 1;
        if (poll === 3) { sawGenerating(); return { job_id: "job-1", status: "generating" }; }
        if (poll < 3) return { job_id: "job-1", status: poll === 1 ? "queued" : "leased" };
        return { job_id: "job-1", status: "completed", output_type: "evidence_report", title: "생성된 보고서", studio_output_id: "output-1", output_version_id: "version-1", lineage: { source_version_ids: [] }, output: { status: "approved", content: { summary: "완성된 근거 요약", body: "실제 보고서 본문", conclusion: "근거 결론", lineage: { request_id: "internal-reference" } }, citations: [{ source_version_id: "source-version-1" }, { source_version_id: "source-version-2" }], settings_snapshot_id: "settings-1" } };
      },
    };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
    assert.equal(buttonByText(container, "생성").disabled, false);
    const form = findElements(container, (element) => element.tagName === "FORM")[0];
    await act(async () => { buttonByText(container, "생성").dispatchEvent(new MinimalEvent("click")); form.dispatchEvent(new MinimalEvent("submit")); await Promise.resolve(); });
    await act(async () => { await generatingSeen; await Promise.resolve(); });
    assert.equal(findElements(container, (element) => element.getAttribute?.("data-generation-state") === "pending").length, 1);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 600)); });
    assert.match(container.textContent, /생성된 보고서/);
    assert.match(container.textContent, /완성된 근거 요약/);
    assert.match(container.textContent, /승인됨/);
    assert.equal(findElements(container, (element) => element.getAttribute?.("aria-label") === "근거 보고서 결과").length, 1);
    const report = findElements(container, (element) => element.getAttribute?.("aria-label") === "근거 보고서 결과")[0];
    const sections = findElements(report, (element) => element.tagName === "ARTICLE");
    assert.equal(sections.length, 3);
    for (const [index, section] of sections.entries()) {
      const columns = section.childNodes.filter((node) => node.nodeType === 1);
      assert.equal(columns.length, 2, "report body must occupy the flexible second grid column");
      assert.equal(columns[0].getAttribute("class"), "document-section-number");
      assert.equal(columns[0].textContent, String(index + 1).padStart(2, "0"));
      assert.equal(columns[1].tagName, "DIV");
      assert.equal(findElements(columns[1], (element) => element.tagName === "P").length, 1);
    }
    assert.match(container.textContent, /요약.*완성된 근거 요약.*본문.*실제 보고서 본문.*결론.*근거 결론/);
    assert.doesNotMatch(container.textContent, /internal-reference/);
    assert.equal(findElements(container, (element) => element.getAttribute?.("data-generation-state") === "completed").length, 1);
    assert.doesNotMatch(container.textContent, /생성을 완료하지 못했습니다/);
    await act(async () => { buttonByText(container, "뒤로").dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /Source 2/);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("실제 React click은 저장 구조 산출물을 선택하고 검토·승인요청 adapter를 호출한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-click-react-")); const dom = installMinimalDom(); let reactRoot;
  const originalCrypto = Object.getOwnPropertyDescriptor(globalThis, "crypto");
  try {
    Object.defineProperty(globalThis, "crypto", { configurable: true, value: { getRandomValues(bytes) { bytes.fill(7); return bytes; } } });
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const calls = []; const requestKeys = []; const fetchImpl = async (url, init) => {
      requestKeys.push(init.headers["Idempotency-Key"]);
      return Response.json({ data: String(url).endsWith("/versions")
        ? { output_version_id: "version-2", status: "draft" }
        : { record_id: "action-1" }, meta: { trace_id: "trace-1", workspace_id: "workspace-1", replayed: false } }, { status: 201 });
    };
    const adapter = { createStudioVersion: async (id, payload) => { calls.push(["version", payload.revision_type]); return createStudioVersion("workspace-1", id, payload, { notebookId: "notebook-1", fetchImpl }); }, createStudioAction: async (action, payload) => { calls.push([action, payload.decision]); return createStudioAction("workspace-1", action, payload, { notebookId: "notebook-1", fetchImpl }); }, issueStudioStepUp: async (_group, _target, password) => { calls.push(["step-up", password]); return "grant"; } };
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "반려 보고서", status: "revision_requested", citations: 1, review_request_id: "review-old", approval_request_id: "approval-old", content: { summary: "반려" } }], selectedOutputId: "output-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
    await act(async () => { buttonByText(container, "반려 보고서").dispatchEvent(new MinimalEvent("click")); });
    assert.match(container.textContent, /반려/);
    assert.doesNotMatch(container.textContent, /추가 인증 비밀번호/);
    const reason = findElements(container, (node) => node.tagName === "LABEL" && node.textContent.includes("변경 사유"))[0];
    const reasonInput = findElements(reason, (node) => node.tagName === "INPUT")[0];
    const contentLabel = findElements(container, (node) => node.tagName === "LABEL" && node.textContent.includes("편집 내용"))[0];
    const contentInput = findElements(contentLabel, (node) => node.tagName === "TEXTAREA")[0];
    const reactProps = (element) => element[Object.keys(element).find((key) => key.startsWith("__reactProps"))];
    await act(async () => { reactProps(reasonInput).onChange({ currentTarget: { value: "내용 수정" } }); reactProps(contentInput).onChange({ currentTarget: { value: "새 내용" } }); });
    await act(async () => { buttonByText(container, "편집 새 Version").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await act(async () => { buttonByText(container, "검토 요청").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); }); await act(async () => { buttonByText(container, "승인 요청").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    await act(async () => { buttonByText(container, "승인").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls.map((item) => item[0]), ["version", "reviews", "approval-requests"]);
    const passwordLabel = findElements(container, (node) => node.tagName === "LABEL" && node.textContent.includes("추가 인증 비밀번호"))[0];
    const passwordInput = findElements(passwordLabel, (node) => node.tagName === "INPUT")[0];
    await act(async () => { reactProps(passwordInput).onChange({ currentTarget: { value: "fixture password only" } }); });
    await act(async () => { buttonByText(container, "추가 인증 확인").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.deepEqual(calls.map((item) => item[0]), ["version", "reviews", "approval-requests", "step-up", "approvals"]);
    assert.equal(requestKeys.length, 4);
    for (const key of requestKeys) assert.match(key, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); if (originalCrypto) Object.defineProperty(globalThis, "crypto", originalCrypto); else delete globalThis.crypto; dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Library 재진입은 Version 이력과 Raw·Daon Citation을 실제 DOM에 복원한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-history-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const adapter = { listStudioVersions: async () => [{
      output_version_id: "version-2", content_version: 2, previous_version_id: "version-1", status: "approved",
      content: { body: "승인된 혼합 근거 보고서" }, revision_type: "user_edit", change_reason: "검토 반영",
      settings_snapshot_id: "settings-2", output_format: "pdf", review_request_id: "review-1",
      approval_request_id: "approval-request-1", approval_id: "approval-1", delivery_id: null, knowledge_registration_id: null,
      citations: [
        { citation_id: "citation-daon", source_version_id: "source-version-daon", evidence_span_id: "span-daon", origin: "daon_knowledge", locator: { kind: "section", value: "핵심 요약" } },
        { citation_id: "citation-raw", source_version_id: "source-version-raw", evidence_span_id: "span-raw", origin: "raw_source", locator: { kind: "page", value: "2" } },
      ],
    }] };
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{ studio_output_id: "output-1", output_version_id: "version-1", title: "혼합 보고서", status: "draft" }], selectedOutputId: null, pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter })); });
    await act(async () => { buttonByText(container, "혼합 보고서").dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); await Promise.resolve(); });
    assert.match(container.textContent, /승인된 혼합 근거 보고서/);
    assert.match(container.textContent, /Version 2/);
    assert.match(container.textContent, /Daon 생성 지식 · 핵심 요약/);
    assert.match(container.textContent, /원본 지식 · 2쪽/);
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("제약·준수 점검표 상세는 항목·판정·근거·조치를 구조화 표로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-compliance-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-check-1", output_version_id: "version-check-1", output_type: "compliance_checklist",
      title: "제약·준수 점검표", status: "draft", settings_snapshot_id: "settings-check-1", citations: 1,
      content: { items: [{ item_id: "check-1", judgement: "needs_review", evidence: "citation-1 page 2", ruleset_id: "ruleset-v3", action: "전문가 검토" }], warnings: [], lineage: { ruleset_id: "ruleset-v3" } },
    }], selectedOutputId: "output-check-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /제약·준수 점검 결과/);
    for (const label of ["항목", "판정", "근거", "조치", "check-1", "검토 필요", "citation-1 page 2", "전문가 검토"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("비교·데이터 표 상세는 기준·현재·차이 상태·양쪽 근거를 구조화 표로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-comparison-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-table-1", output_version_id: "version-table-1", output_type: "comparison_table",
      title: "비교·데이터 표", status: "draft", settings_snapshot_id: "settings-table-1", citations: 2,
      content: { rows: [{ key: "시장 규모", baseline: "100", current: "120", difference: ["100", "120"], state: "changed", evidence: ["citation-a page 1", "citation-b page 2"], baseline_version: "evidence", current_version: "result-1" }] },
    }], selectedOutputId: "output-table-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /비교·데이터 결과/);
    for (const label of ["항목", "기준", "현재", "상태", "근거", "시장 규모", "100", "120", "변경", "citation-a page 1", "citation-b page 2"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("지식 구조도 상세는 근거 노드와 관계를 배타적인 구조 뷰로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-map-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-map-1", output_version_id: "version-map-1", output_type: "knowledge_map",
      title: "지식 구조도", status: "draft", settings_snapshot_id: "settings-map-1", citations: 2,
      content: { nodes: [{ id: "node-1", label: "승인 지식", confidence: "verified", evidence: "citation-daon section" }, { id: "node-2", label: "원본 PDF", confidence: "verified", evidence: "citation-raw page 2" }], edges: [{ id: "edge-1", source: "node-1", target: "node-2", condition: "근거 순서" }] },
    }], selectedOutputId: "output-map-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /지식 구조 결과/);
    for (const label of ["승인 지식", "검증됨", "citation-daon section", "원본 PDF", "citation-raw page 2", "승인 지식 → 원본 PDF", "근거 순서"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("업무 문서 초안 상세는 섹션 본문·근거·검토 상태를 문서 구조로 렌더한다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-draft-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs: [{
      studio_output_id: "output-draft-1", output_version_id: "version-draft-1", output_type: "business_draft",
      title: "업무 문서 초안", status: "draft", settings_snapshot_id: "settings-draft-1", citations: 2,
      content: { template_id: "letter", review_state: "draft", sections: [{ title: "요약", body: "승인 지식과 원본을 결합한 업무 요약", evidence: ["citation-daon page 1", "citation-raw page 2"] }], warnings: [], lineage: { request_id: "generation-1" } },
    }], selectedOutputId: "output-draft-1", pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container); await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: {} })); });
    assert.match(container.textContent, /업무 문서 결과/);
    for (const label of ["초안", "요약", "승인 지식과 원본을 결합한 업무 요약", "citation-daon page 1", "citation-raw page 2"]) assert.match(container.textContent, new RegExp(label));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});

test("Library는 다섯 산출물 유형을 구분하고 선택한 산출물의 통합 관리 화면을 연다", async () => {
  const rootPath = path.resolve(import.meta.dirname, "../.."); const output = await mkdtemp(path.join(rootPath, ".product-studio-library-react-")); const dom = installMinimalDom(); let reactRoot;
  try {
    const { build } = await import("vite"); const { createElement, act } = await import("react"); const { createRoot } = await import("react-dom/client");
    await build({ configFile: false, logLevel: "silent", root: rootPath, build: { outDir: output, emptyOutDir: false, lib: { entry: path.join(rootPath, "packages/ui/src/product-studio-pane.jsx"), formats: ["es"], fileName: "product-studio" }, rollupOptions: { external: ["react", "react-dom", "react-dom/client"] } } });
    const entry = (await readdir(output)).find((name) => name.startsWith("product-studio") && /\.m?js$/u.test(name)); const { ProductStudioPane } = await import(`${pathToFileURL(path.join(output, entry)).href}?v=${Date.now()}`);
    const outputs = [
      ["evidence_report", "근거 보고서", "pdf"], ["compliance_checklist", "준수 점검표", "xlsx"],
      ["comparison_table", "비교표", "xlsx"], ["knowledge_map", "지식 구조도", "json"],
      ["business_draft", "업무 문서", "docx"],
    ].map(([output_type, title, output_format], index) => ({
      studio_output_id: `output-${index + 1}`, output_version_id: `version-${index + 1}`,
      output_type, output_format, title, status: index === 0 ? "approved" : "draft",
      content_version: index + 1, source_count: index + 1, created_at: `2026-08-15T0${index}:00:00Z`,
      citations: [], content: { body: title },
    }));
    const state = { status: "ready", workspaceId: "workspace-1", grounded: null, locks: [], selectedOutputType: null, settings: {}, settingsConfirmed: false, settingsSnapshot: null, outputs, selectedOutputId: null, pending: false, safeError: null };
    const container = dom.document.createElement("div"); dom.document.body.appendChild(container); reactRoot = createRoot(container);
    await act(async () => { reactRoot.render(createElement(ProductStudioPane, { state, adapter: { listStudioVersions: async () => [{ ...outputs[0], change_reason: "initial_generation" }] } })); });
    const library = findElements(container, (element) => element.getAttribute?.("class") === "studio-library")[0];
    for (const label of ["근거 기반 보고서", "제약·준수 점검표", "비교·데이터 표", "지식 구조도", "업무 문서 초안"]) assert.match(library.textContent, new RegExp(label));
    const reportButton = findElements(container, (element) => element.tagName === "BUTTON" && element.textContent.includes("근거 보고서"))[0];
    await act(async () => { reportButton.dispatchEvent(new MinimalEvent("click")); await Promise.resolve(); });
    assert.ok(findElements(container, (element) => element.getAttribute?.("aria-label") === "선택 산출물 상세").length);
    for (const action of ["편집 새 Version", "AI 재생성 새 Version", "검토 요청", "승인 요청", "내보내기"]) assert.match(container.textContent, new RegExp(action));
  } finally { if (reactRoot) await import("react").then(({ act }) => act(async () => reactRoot.unmount())); dom.restore(); await rm(output, { recursive: true, force: true }); }
});
