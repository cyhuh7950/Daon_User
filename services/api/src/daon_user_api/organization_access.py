"""조직 가입의 명시적 Workspace 권한 연결. 전달 connection의 수명은 소유하지 않는다."""
from __future__ import annotations

import sqlite3

from .organization_membership import OrganizationWorkflowError, _id, _iso


class OrganizationAccessBridge:
    def __init__(self, *, postgres: bool = False) -> None:
        # 물리표 선택은 서버 어댑터가 고정한다. 요청에서 표 이름을 받지 않는다.
        self._postgres = postgres
        self._users = "public.identity_users" if postgres else "users"
        self._tenants = "public.identity_tenants" if postgres else "tenants"
        self._memberships = "public.identity_memberships" if postgres else "memberships"
        self._auth_memberships = "public.identity_auth_memberships" if postgres else "auth_memberships"
        self._tenant_roles = "public.identity_auth_tenant_roles" if postgres else "auth_tenant_roles"
        self._workspaces = "public.identity_auth_workspaces" if postgres else "auth_workspaces"
        self._workflow = "public.identity_org_tenant_memberships" if postgres else "tenant_memberships"

    def assert_organization_active(self, *, connection, tenant_id, user_id) -> None:
        """대상 조직의 현재 workflow만 검사한다. 소속 생성·auth 복구는 하지 않는다."""
        _id(tenant_id); _id(user_id)
        # 조직 workflow를 구성하지 않은 기존 SQLite 소비자는 기존 권한 검사를 유지한다.
        # PG에는 0037부터 실제 표가 있다. 접속/조회 오류를 허용으로 바꾸지 않는다.
        if not self._postgres and connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tenant_memberships'"
        ).fetchone() is None:
            return
        row = connection.execute(
            f"SELECT state FROM {self._workflow} WHERE tenant_id=? AND user_id=?", (tenant_id, user_id),
        ).fetchone()
        if row is not None and row["state"] != "active":
            raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_SUSPENDED", 403)

    def _workspace(self, connection, tenant_id, workspace_id):
        _id(tenant_id); _id(workspace_id)
        lock = " FOR UPDATE" if self._postgres else ""
        row = connection.execute(
            f"SELECT workspace_id,tenant_id,workspace_kind,acl_version FROM {self._workspaces} "
            f"WHERE tenant_id=? AND workspace_id=?{lock}", (tenant_id, workspace_id),
        ).fetchone()
        if row is None or row["workspace_kind"] != "organization":
            raise OrganizationWorkflowError("ORGANIZATION_INVITATION_SCOPE_REQUIRED", 409)
        return row

    def approve_join_access(self, *, connection, tenant_id, workspace_id, user_id, now) -> dict:
        _id(user_id)
        self._workspace(connection, tenant_id, workspace_id)
        if connection.execute(f"SELECT 1 FROM {self._tenants} WHERE tenant_id=?", (tenant_id,)).fetchone() is None:
            raise OrganizationWorkflowError("ORGANIZATION_INVITATION_SCOPE_REQUIRED", 409)
        lock = " FOR UPDATE" if self._postgres else ""
        user = connection.execute(
            f"SELECT state,password_change_required,issuer,email_verified_at FROM {self._users} WHERE user_id=?{lock}", (user_id,),
        ).fetchone()
        if (user is None or user["state"] != "active" or user["password_change_required"]
                or (user["issuer"] == "local" and user["email_verified_at"] is None)):
            raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_CONFLICT", 409)
        for table in (self._memberships, self._auth_memberships, self._tenant_roles):
            if connection.execute(f"SELECT user_id FROM {table} WHERE tenant_id=? AND user_id=?{lock}", (tenant_id, user_id)).fetchone() is not None:
                raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_CONFLICT", 409)
        # 기존 조직/정책만 사용한다. tenant·Step-up·다른 Workspace는 생성하지 않는다.
        try:
            connection.execute(f"INSERT INTO {self._memberships}(tenant_id,user_id,role) VALUES (?,?,'member')", (tenant_id, user_id))
            connection.execute(
                f"INSERT INTO {self._auth_memberships}(tenant_id,workspace_id,user_id,role,state,version,updated_at) VALUES (?,?,?,'viewer','active',1,?)",
                (tenant_id, workspace_id, user_id, _iso(now)),
            )
        except sqlite3.IntegrityError as error:
            # 잠금 뒤 별도 기존 경로가 먼저 만든 행도 덮어쓰지 않는다.
            raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_CONFLICT", 409) from error
        updated = connection.execute(f"UPDATE {self._workspaces} SET acl_version=acl_version+1,updated_at=? WHERE tenant_id=? AND workspace_id=?", (_iso(now), tenant_id, workspace_id))
        if updated.rowcount != 1:
            raise OrganizationWorkflowError("ORGANIZATION_MEMBERSHIP_CONFLICT", 409)
        row = connection.execute(
            f"SELECT role,state,version FROM {self._auth_memberships} WHERE tenant_id=? AND workspace_id=? AND user_id=?",
            (tenant_id, workspace_id, user_id),
        ).fetchone()
        return {"access_role": row["role"], "access_state": row["state"], "access_version": row["version"]}

    def read_join_access(self, *, connection, tenant_id, workspace_id, user_id) -> dict:
        _id(tenant_id); _id(workspace_id); _id(user_id)
        member = connection.execute(
            f"SELECT join_workspace_id FROM {self._workflow} WHERE tenant_id=? AND user_id=?",
            (tenant_id, user_id),
        ).fetchone()
        if member is None or member["join_workspace_id"] != workspace_id:
            raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
        row = connection.execute(
            f"SELECT role,state,version FROM {self._auth_memberships} WHERE tenant_id=? AND workspace_id=? AND user_id=?",
            (tenant_id, workspace_id, user_id),
        ).fetchone()
        # 별도 경로의 철회를 복구하거나 workflow 역할로 추론하지 않는다.
        if row is None:
            return {"access_role": None, "access_state": None, "access_version": None}
        return {"access_role": row["role"], "access_state": row["state"], "access_version": row["version"]}

    def change_join_role(self, *, connection, tenant_id, workspace_id, user_id, role, expected_auth_version, now) -> dict:
        _id(user_id)
        if role not in {"viewer", "editor", "reviewer", "approver"}:
            raise OrganizationWorkflowError("INVALID_ROLE", 422)
        if type(expected_auth_version) is not int or expected_auth_version < 1:
            raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
        self._workspace(connection, tenant_id, workspace_id)
        lock = " FOR UPDATE" if self._postgres else ""
        workflow = connection.execute(
            f"SELECT role,state,join_workspace_id FROM {self._workflow} WHERE tenant_id=? AND user_id=?{lock}",
            (tenant_id, user_id),
        ).fetchone()
        user = connection.execute(f"SELECT state FROM {self._users} WHERE user_id=?{lock}", (user_id,)).fetchone()
        identity = connection.execute(
            f"SELECT role FROM {self._memberships} WHERE tenant_id=? AND user_id=?{lock}", (tenant_id, user_id),
        ).fetchone()
        auth = connection.execute(
            f"SELECT role,state,version FROM {self._auth_memberships} WHERE tenant_id=? AND workspace_id=? AND user_id=?{lock}",
            (tenant_id, workspace_id, user_id),
        ).fetchone()
        if (workflow is None or workflow["join_workspace_id"] != workspace_id
                or workflow["role"] != "member" or workflow["state"] != "active"
                or user is None or user["state"] != "active" or identity is None or identity["role"] != "member"
                or auth is None or auth["state"] != "active" or auth["role"] not in {"viewer", "editor", "reviewer", "approver"}
                or auth["version"] != expected_auth_version):
            raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
        changed = connection.execute(
            f"UPDATE {self._auth_memberships} SET role=?,version=version+1,updated_at=? "
            "WHERE tenant_id=? AND workspace_id=? AND user_id=? AND version=? AND state='active'",
            (role, _iso(now), tenant_id, workspace_id, user_id, expected_auth_version),
        )
        if changed.rowcount != 1:
            raise OrganizationWorkflowError("MEMBERSHIP_CONFLICT", 409)
        connection.execute(f"UPDATE {self._workspaces} SET acl_version=acl_version+1,updated_at=? WHERE tenant_id=? AND workspace_id=?",
                           (_iso(now), tenant_id, workspace_id))
        return self.read_join_access(connection=connection, tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
