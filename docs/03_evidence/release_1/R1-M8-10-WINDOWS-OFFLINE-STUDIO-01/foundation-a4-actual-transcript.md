# Foundation A4 actual mixed-context transcript

- Date: 2026-08-15 Asia/Seoul
- Checkout: `dbe67f9bfe778b1ffa10b31f1e3e0faf807dd42b`
- Scope: disposable encrypted Local Store only; production DB, Provider, credential and network writes: 0

## Actual product path

`LocalEncryptedStore.open` → `import_knowledge_copy` → `RawSourceService.import_source` → `build_production_offline_studio` → `prepare_context(mode=mixed)` → store close/reopen → same-key replay.

## Result

```json
{"context_schema_version":1,"daon_registration_state":"registered","daon_review_state":"approved","origins":["daon_knowledge","raw_source"],"plaintext_at_rest_count":0,"raw_conflict_state":"none","raw_evidence_span_count":1,"raw_processing_state":"completed","raw_review_state":"unverified","restart_replay_exact":true,"status":"PASS"}
```

- Daon3 Knowledge Package lineage preserved: producer version, registration ID/state, OutputVersion, authority/review, effective/expiry and digest.
- Explicit Raw Source lineage preserved: Source/SourceVersion, IndexVersion, EvidenceSpan, processing/review/conflict and digest.
- Context Snapshot schema/version and digest survived encrypted-store restart with exact idempotency replay.
- Persisted files contained neither Knowledge Package evidence text nor Raw Source plaintext.
- Temporary directory was owned by `TemporaryDirectory` and removed at process exit.

## Automated regressions after correction

- Focused RED: registration state was validated but absent from persisted Context/Citation lineage (`2 failed`).
- Focused GREEN: Knowledge Context plus production Offline Studio `16 passed`.
- Local Service full: `166 passed, 2 skipped`.
- API full: `384 passed, 28 skipped, 134 subtests passed`.
- Guarded Rust full: exit 0; lib 30, Local Service contract 5, Native Session 22, Offline Studio 3, Offline Sync 7, Recovery 44 and Workspace 2 passed. Unique test credential was revoked.
