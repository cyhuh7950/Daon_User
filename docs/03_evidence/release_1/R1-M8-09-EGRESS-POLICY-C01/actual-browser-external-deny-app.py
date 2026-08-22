from __future__ import annotations

import os
from pathlib import Path

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api.document_index_postgres import PostgresDocumentIndex
from daon_user_api.egress_policy import EgressPolicyService
from daon_user_api.egress_policy_postgres import PostgresEgressPolicyRepository
from daon_user_api.provider_settings import PostgresProviderSettingsRepository, ProviderSettingsService
from daon_user_api.question_answering_postgres import PostgresQuestionAnsweringRepository
from daon_user_api.question_answering_service import QuestionAnsweringService, QuestionAdapterRegistry
from daon_user_api.question_egress import PostgresQuestionEgressAuthorizer
from daon_user_api.runtime import RuntimeSettings, build_dependencies, create_app


class MemoryObjectStorage:
    def get(self, _key: str) -> bytes:
        return b"%PDF-1.7\nfixture\n%%EOF"


class CredentialPresence:
    def configured(self, provider_code: str) -> bool:
        return provider_code == "UPSTAGE"


class MemoryCredential:
    def resolve(self, _provider_code: str) -> str:
        return "memory-only-fixture"


class BoundedTransportSpy:
    def __init__(self, counter: Path) -> None:
        self.counter = counter

    def _called(self) -> dict[str, object]:
        self.counter.write_text("1", encoding="ascii")
        raise RuntimeError("BOUNDED_TRANSPORT_SPY_INVOKED")

    def post_json(self, **_kwargs):  # type: ignore[no-untyped-def]
        return self._called()

    def post_json_no_auth(self, **_kwargs):  # type: ignore[no-untyped-def]
        return self._called()


settings = RuntimeSettings.from_env()
dependencies = build_dependencies(settings)
cloud = PostgresCloudStore(os.environ["DAON_CLOUD_DATABASE_DSN"])
storage = MemoryObjectStorage()
providers = ProviderSettingsService(PostgresProviderSettingsRepository(cloud), CredentialPresence())
policy = EgressPolicyService(PostgresEgressPolicyRepository(cloud))
dependencies.cloud_store = cloud
dependencies.object_storage = storage
dependencies.provider_settings_service = providers
dependencies.egress_policy_service = policy
dependencies.question_answering_service = QuestionAnsweringService(
    providers,
    PostgresQuestionAnsweringRepository(cloud, storage),
    PostgresDocumentIndex(cloud),
    MemoryCredential(),
    BoundedTransportSpy(Path(os.environ["DAON_TRANSPORT_COUNTER"])),
    PostgresQuestionEgressAuthorizer(cloud, policy),
    adapter_registry=QuestionAdapterRegistry(),
)
dependencies.citation_content_repository = PostgresQuestionAnsweringRepository(cloud, storage)
app = create_app(dependencies)
