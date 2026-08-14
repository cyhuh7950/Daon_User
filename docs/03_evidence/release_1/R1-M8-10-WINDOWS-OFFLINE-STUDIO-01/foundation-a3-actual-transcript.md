# Foundation A3 actual Gate transcript

- Date: 2026-08-14 (Asia/Seoul)
- Scope: Provider Profile, Deployment, role binding, connection status and safe server transport
- Product migration revision: `0016`

## PostgreSQL 15.18 disposable Gate

1. Created exact disposable DB `daon_a3_provider_it_20260814` from `template0`.
2. Applied Alembic `0001 -> 0016`.
3. Used a PostgreSQL app-role session (`role=daon_app`) for `PostgresProviderSettingsRepository`.
4. Persisted one UPSTAGE Profile, one `solar-pro4` text Deployment and one text role binding.
5. Verified a second tenant/workspace sees zero stored deployments.
6. Attempted an app-role cross-tenant Profile write and received RLS denial; matching rows remained zero.
7. Verified current revision `0016`.
8. Dropped the exact disposable DB; matching DB remaining count is zero.

The first test attempt used a superuser DSN and therefore bypassed forced RLS. It was rejected as an invalid test configuration, cleaned up, and rerun with the app-role session above. It is not counted as a product failure or PASS.

## Server-side Provider connection Gate

1. Copied the current product `provider_settings.py` and its required module into an isolated `/tmp/daon-a3-provider-check` package inside the existing ysna API container.
2. Read the existing UPSTAGE credential only through `ServerCredentialPresenceResolver`; the value was not printed, persisted or passed on the command line.
3. Ran `HttpProviderConnectionChecker` once against the fixed approved endpoint `https://api.upstage.ai/v1/models`.
4. Observed only `A3_UPSTAGE_CONNECTION_READY_PASS`; Provider response content was ignored and not recorded.
5. Removed the exact host/container temporary directory and verified both are absent.

No Provider Profile, Deployment, credential, server configuration, model or service was changed by the live connection Gate. No generation-quality claim is made here; representative generation belongs to Foundation A5.
