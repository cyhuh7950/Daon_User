"""Run the product connection checker inside the API process boundary.

The credential is read from the server environment and is never printed or persisted.
"""

from daon_user_api.provider_settings import (
    HttpProviderConnectionChecker,
    ProviderProfileView,
    ServerCredentialPresenceResolver,
)


credential = ServerCredentialPresenceResolver().resolve("UPSTAGE")
assert credential is not None
profile = ProviderProfileView(
    "provider-upstage",
    "UPSTAGE",
    "external_api",
    "https://api.upstage.ai/v1",
    True,
    True,
    1,
)
status = HttpProviderConnectionChecker().check(profile, credential)
assert status.provider_code == "UPSTAGE"
assert status.status == "ready"
print("A3_UPSTAGE_CONNECTION_READY_PASS")
