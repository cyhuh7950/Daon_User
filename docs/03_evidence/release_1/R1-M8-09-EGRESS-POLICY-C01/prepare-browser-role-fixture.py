from __future__ import annotations

import os
from pathlib import Path
import sqlite3


database = Path(os.environ["DAON_BROWSER_SQLITE"])
tenant_id = os.environ["DAON_BROWSER_TENANT_ID"]
with sqlite3.connect(database) as connection:
    connection.execute(
        "UPDATE memberships SET role='organization_admin' WHERE tenant_id=?",
        (tenant_id,),
    )
    connection.execute(
        "UPDATE auth_tenant_roles SET role='organization_admin',version=version+1 "
        "WHERE tenant_id=?",
        (tenant_id,),
    )
    connection.execute(
        "UPDATE auth_workspaces SET workspace_kind='organization',version=version+1 "
        "WHERE tenant_id=?",
        (tenant_id,),
    )
print("TEST_ORG_ADMIN_FIXTURE_READY")
