"""Server-console-only recovery commands for the Daon User API."""

from __future__ import annotations

import sys
from collections.abc import Sequence

from .identity import IdentityError
from .runtime import RuntimeSettings, build_dependencies


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments != ["reset-initial-password"]:
        print(
            "usage: python -m daon_user_api.admin_cli reset-initial-password",
            file=sys.stderr,
        )
        return 2
    dependencies = None
    try:
        settings = RuntimeSettings.from_env()
        dependencies = build_dependencies(settings)
        dependencies.identity_service.reset_initial_admin_password(
            trace_id="trace-server-console-admin-recovery",
            policy_version=settings.policy_version,
        )
    except IdentityError as error:
        print(f"INITIAL_ADMIN_PASSWORD_RESET_FAILED:{error.code}", file=sys.stderr)
        return 1
    except Exception:
        print("INITIAL_ADMIN_PASSWORD_RESET_FAILED:RUNTIME_UNAVAILABLE", file=sys.stderr)
        return 1
    finally:
        if dependencies is not None:
            dependencies.close()
    print("INITIAL_ADMIN_PASSWORD_RESET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
