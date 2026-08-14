# Foundation A5 Actual Provider Gate

Date: 2026-08-15 (Asia/Seoul)

## Scope

- Representative Provider: `UPSTAGE` only. The approved nine-provider matrix is covered by common contract tests; no claim is made that all nine generated content in this Gate.
- Product adapter: `OpenAICompatibleTextGenerationAdapter` from the current local source tree.
- Production environment: existing healthy `daon_user-api-1` container on `ysna-server`.
- Model: configured `solar-pro4` text model.

## Result

1. Current source was copied to a unique server/container temporary directory without replacing the deployed application files or restarting a service.
2. `ServerProviderCredentialResolver` read the existing Upstage credential inside the API process boundary. The value was not printed, persisted, or supplied on a command line.
3. A bounded grounded generation request used one synthetic evidence chunk with one allowed Citation ID.
4. The product adapter returned `A5_UPSTAGE_GROUNDED_GENERATION_PASS`, `citation_count=1`, and three integer usage fields.
5. Generated answer text and Provider response body were not written to this evidence.
6. The unique temporary directory was removed from both the container and host; `A5_TEMP_CLEANUP_PASS` was observed.

## Interpretation

This closes the representative Provider transport, strict response schema, and Citation validation portion of Foundation A5. Automated PostgreSQL/Studio contracts separately verify that the originating Routing/Egress Decision and exact selected Provider/Deployment/Model are frozen into `GenerationSettingsSnapshot`, then retained through Output versioning, review, approval and export. It does not claim production deployment of the uncommitted A5 adapter changes.
