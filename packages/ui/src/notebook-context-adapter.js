const SAFE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$/u;
const CONTEXT_KEYS = Object.freeze([
  "notebook_id", "sources", "knowledge_context_ids", "conversation_thread_ids",
  "studio_output_ids", "output_version_ids", "generation_settings_ids",
  "conversation",
]);

function exact(value, keys) {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const actual = Object.keys(value).sort();
  const expected = [...keys].sort();
  return actual.length === expected.length && actual.every((key, index) => key === expected[index]);
}

function safeId(value) {
  return typeof value === "string" && SAFE_ID.test(value);
}

function safeIds(value) {
  return Array.isArray(value) && value.length <= 1_000
    && new Set(value).size === value.length && value.every(safeId);
}

function validCitation(value) {
  return exact(value, ["citation_id", "source_id", "source_version_id", "evidence_span_id", "page", "origin", "context_item_id", "locator"])
    && ["citation_id", "source_id", "source_version_id", "evidence_span_id", "context_item_id"].every((key) => safeId(value[key]))
    && Number.isSafeInteger(value.page) && value.page >= 1
    && ["raw_source", "daon_knowledge"].includes(value.origin)
    && exact(value.locator, ["kind", "value"])
    && ["page", "section"].includes(value.locator.kind)
    && typeof value.locator.value === "string" && value.locator.value.length >= 1
    && value.locator.value.length <= 255
    && (value.locator.kind !== "page" || value.locator.value === String(value.page));
}

function projectCitation(value) {
  return Object.freeze({
    citation_id: value.citation_id, source_id: value.source_id,
    source_version_id: value.source_version_id, evidence_span_id: value.evidence_span_id,
    page: value.page, origin: value.origin, context_item_id: value.context_item_id,
    locator: Object.freeze({ kind: value.locator.kind, value: value.locator.value }),
  });
}

export function projectNotebookSelectedContext(value) {
  const conversationValid = value?.conversation === null || (
    validConversation(value?.conversation, value?.conversation_thread_ids?.[0])
  );
  const valid = exact(value, CONTEXT_KEYS)
    && safeId(value.notebook_id)
    && Array.isArray(value.sources) && value.sources.length <= 1_000
    && value.sources.every((item) => exact(item, ["source_id", "source_version_id"])
      && safeId(item.source_id) && safeId(item.source_version_id))
    && new Set(value.sources.map((item) => `${item.source_id}\u0000${item.source_version_id}`)).size === value.sources.length
    && safeIds(value.knowledge_context_ids)
    && safeIds(value.conversation_thread_ids)
    && safeIds(value.studio_output_ids)
    && safeIds(value.output_version_ids)
    && safeIds(value.generation_settings_ids)
    && conversationValid
    && ((value.conversation === null) === (value.conversation_thread_ids.length === 0));
  if (!valid) throw new Error("NOTEBOOK_CONTEXT_INVALID");
  return Object.freeze({
    notebook_id: value.notebook_id,
    sources: Object.freeze(value.sources.map((item) => Object.freeze({ ...item }))),
    knowledge_context_ids: Object.freeze([...value.knowledge_context_ids]),
    conversation_thread_ids: Object.freeze([...value.conversation_thread_ids]),
    studio_output_ids: Object.freeze([...value.studio_output_ids]),
    output_version_ids: Object.freeze([...value.output_version_ids]),
    generation_settings_ids: Object.freeze([...value.generation_settings_ids]),
    conversation: value.conversation === null ? null : Object.freeze({
      conversation_thread_id: value.conversation.conversation_thread_id,
      answer: Object.freeze({
        run_id: value.conversation.answer.run_id,
        run_result_id: value.conversation.answer.run_result_id,
        answer: value.conversation.answer.answer,
        insufficient: value.conversation.answer.insufficient,
        citations: Object.freeze(value.conversation.answer.citations.map(projectCitation)),
      }),
    }),
  });
}

function validConversation(value, expectedId) {
  return exact(value, ["conversation_thread_id", "answer"])
    && value.conversation_thread_id === expectedId
    && exact(value.answer, ["run_id", "run_result_id", "answer", "insufficient", "citations"])
    && safeId(value.answer.run_id) && safeId(value.answer.run_result_id)
    && typeof value.answer.answer === "string" && value.answer.answer.length >= 1
    && value.answer.answer.length <= 8_000 && typeof value.answer.insufficient === "boolean"
    && Array.isArray(value.answer.citations) && value.answer.citations.length <= 20
    && value.answer.citations.every(validCitation);
}

export function createNotebookContextWorkspaceAdapter(baseAdapter, inputContext) {
  if (!baseAdapter || typeof baseAdapter !== "object") throw new Error("NOTEBOOK_CONTEXT_ADAPTER_INVALID");
  const context = projectNotebookSelectedContext(inputContext);
  const sourceKeys = new Set(context.sources.map((item) => `${item.source_id}\u0000${item.source_version_id}`));
  const studioIds = new Set(context.studio_output_ids);
  const outputVersionIds = new Set(context.output_version_ids);

  const listSources = async (options) => {
    if (!sourceKeys.size) return [];
    if (typeof baseAdapter.listSources !== "function") throw new Error("NOTEBOOK_CONTEXT_SOURCE_UNAVAILABLE");
    const values = await baseAdapter.listSources({ ...options, notebookId: context.notebook_id });
    if (!Array.isArray(values)) throw new Error("NOTEBOOK_CONTEXT_SOURCE_INVALID");
    return values.filter((item) => sourceKeys.has(`${item?.source_id}\u0000${item?.source_version_id}`));
  };

  const listKnowledgePackages = async (options) => {
    if (!context.knowledge_context_ids.length) return [];
    if (typeof baseAdapter.resolveKnowledgeContext !== "function" || typeof baseAdapter.listKnowledgePackages !== "function") {
      throw new Error("NOTEBOOK_CONTEXT_KNOWLEDGE_UNAVAILABLE");
    }
    const packageIds = new Set();
    for (const contextId of context.knowledge_context_ids) {
      const resolved = await baseAdapter.resolveKnowledgeContext(contextId, options);
      if (!exact(resolved, ["package_ids"]) || !safeIds(resolved.package_ids)) throw new Error("NOTEBOOK_CONTEXT_KNOWLEDGE_INVALID");
      for (const packageId of resolved.package_ids) packageIds.add(packageId);
    }
    const values = await baseAdapter.listKnowledgePackages(options);
    if (!Array.isArray(values)) throw new Error("NOTEBOOK_CONTEXT_KNOWLEDGE_INVALID");
    return values.filter((item) => packageIds.has(item?.package_id));
  };

  const loadNotebookConversation = async (options) => {
    if (!context.conversation_thread_ids.length) return null;
    if (context.conversation_thread_ids.length !== 1 || context.conversation === null) {
      throw new Error("NOTEBOOK_CONTEXT_CONVERSATION_UNAVAILABLE");
    }
    return context.conversation.answer;
  };

  const filterOutputs = (values) => {
    if (!Array.isArray(values)) throw new Error("NOTEBOOK_CONTEXT_OUTPUT_INVALID");
    return values.filter((item) => studioIds.has(item?.studio_output_id) && outputVersionIds.has(item?.output_version_id));
  };
  const listStudioOutputs = async (options) => {
    if (!studioIds.size && !outputVersionIds.size) return [];
    if (typeof baseAdapter.listStudioOutputs !== "function") throw new Error("NOTEBOOK_CONTEXT_OUTPUT_UNAVAILABLE");
    const values = await baseAdapter.listStudioOutputs({ ...options, notebookId: context.notebook_id });
    return Array.isArray(values) ? filterOutputs(values) : { ...values, outputs: filterOutputs(values?.outputs) };
  };
  const listProductStudioOutputs = async (options) => {
    if (!studioIds.size && !outputVersionIds.size) return { outputs: [], studioLocks: [] };
    if (typeof baseAdapter.listProductStudioOutputs !== "function") {
      throw new Error("NOTEBOOK_CONTEXT_OUTPUT_UNAVAILABLE");
    }
    const value = await baseAdapter.listProductStudioOutputs({
      ...options, notebookId: context.notebook_id,
    });
    if (!value || typeof value !== "object" || Array.isArray(value)
        || !Array.isArray(value.outputs) || !Array.isArray(value.studioLocks)) {
      throw new Error("NOTEBOOK_CONTEXT_OUTPUT_INVALID");
    }
    return { outputs: filterOutputs(value.outputs), studioLocks: [...value.studioLocks] };
  };

  return Object.freeze({
    ...baseAdapter,
    notebookContext: context,
    generationSettingsIds: [...context.generation_settings_ids],
    listSources,
    listKnowledgePackages,
    loadNotebookConversation,
    listStudioOutputs,
    listProductStudioOutputs: typeof baseAdapter.listProductStudioOutputs === "function"
      ? listProductStudioOutputs
      : undefined,
    createReport: typeof baseAdapter.createReport === "function"
      ? (input, options) => baseAdapter.createReport(input, { ...options, notebookId: context.notebook_id })
      : undefined,
    uploadPdf: typeof baseAdapter.uploadPdf === "function"
      ? (file, options) => baseAdapter.uploadPdf(file, { ...options, notebookId: context.notebook_id })
      : undefined,
    getProcessingStatus: typeof baseAdapter.getProcessingStatus === "function"
      ? (processingRunId, options) => baseAdapter.getProcessingStatus(processingRunId, { ...options, notebookId: context.notebook_id })
      : undefined,
    citationUrl: typeof baseAdapter.citationUrl === "function"
      ? (citation) => baseAdapter.citationUrl(citation, { notebookId: context.notebook_id })
      : undefined,
    askQuestion: typeof baseAdapter.askQuestion === "function"
      ? (input, options) => baseAdapter.askQuestion({ ...input, notebookId: context.notebook_id }, options)
      : undefined,
    createGeneration: typeof baseAdapter.createGeneration === "function"
      ? (input, options) => baseAdapter.createGeneration(input, { ...options, notebookId: context.notebook_id })
      : undefined,
    createStudioVersion: typeof baseAdapter.createStudioVersion === "function"
      ? (outputId, input, options) => baseAdapter.createStudioVersion(outputId, input, { ...options, notebookId: context.notebook_id })
      : undefined,
    listStudioVersions: typeof baseAdapter.listStudioVersions === "function"
      ? (outputId, options) => baseAdapter.listStudioVersions(outputId, { ...options, notebookId: context.notebook_id })
      : undefined,
    createStudioAction: typeof baseAdapter.createStudioAction === "function"
      ? (action, input, options) => baseAdapter.createStudioAction(action, input, { ...options, notebookId: context.notebook_id })
      : undefined,
    downloadStudioExport: typeof baseAdapter.downloadStudioExport === "function"
      ? (outputId, versionId, format, options) => baseAdapter.downloadStudioExport(outputId, versionId, format, { ...options, notebookId: context.notebook_id })
      : undefined,
  });
}
