"""B1 PostgreSQL policy adapter. Migration 0074 is a draft, never applied here."""
from __future__ import annotations
from contextlib import nullcontext
from dataclasses import replace
import hashlib
from uuid import uuid4

from psycopg.types.json import Jsonb
from .authorization import Action, Permission
from .cloud_storage import CloudAccessContext, CloudDatabaseError
from .open_law_source_access import (_current_auth_grant, OpenLawAccessError,
    _AuthReadConnection, _CurrentAuthorizationRules)
from .routing_policy import (RoutingPolicyError, RoutingPolicyBody, VerifiedCandidate,
    RoutingPolicyService, VerifiedUpperBudget, digest)
from .data_canon import canonical_json_bytes
from .provider_credentials import PERSONAL_CREDENTIAL_SCHEMA_VERSION


def _cloud(context, operation):
    return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, operation)


def _authorize(connection, context, action):
    try:
        return _current_auth_grant(context, connection, action)
    except OpenLawAccessError:
        raise RoutingPolicyError('ROUTING_POLICY_ACCESS_DENIED', 403) from None


def _empty():
    return dict(version=0, policy_version_id=None, digest_sha256=None, body=None,
        etag='"routing-policy-0"', execution_enabled=False, readiness='policy_absent')


def _view(row):
    if row is None:
        return _empty()
    record_id, version, payload, stored_digest = row
    if digest(payload) != stored_digest or type(version) is not int or version < 1:
        raise RoutingPolicyError('ROUTING_POLICY_UNAVAILABLE', 503)
    body = RoutingPolicyBody.model_validate(payload)
    return dict(version=version, policy_version_id=record_id, body=body.model_dump(),
        digest_sha256=stored_digest, etag='"routing-policy-' + stored_digest + '"',
        execution_enabled=False, readiness='observations_unavailable' if body.enabled else 'disabled')


class PostgresRoutingMetadata:
    """Reads the actual system catalog and the actor's own credential metadata only.

    No Secret decryption, network probes, or invented metrics. A metrics producer
    is not installed by B1; preparation therefore excludes every unobserved candidate.
    """
    def __init__(self, store, *, budget_source=None, clock=None):
        from datetime import datetime, timezone
        self.store = store
        self.budget_source = budget_source
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def candidates(self, context, policy, *, connection=None):
        try:
            with (nullcontext(connection) if connection is not None else
                    self.store._transaction(_cloud(context, 'routing_policy.metadata'))) as conn:
                grant = _authorize(conn, context, Action.VIEW)
                workspace = conn.execute('SELECT data_area FROM identity_auth_workspaces '
                    'WHERE tenant_id=%s AND workspace_id=%s FOR SHARE',
                    (context.tenant_id, context.workspace_id)).fetchone()
                if workspace is None:
                    raise RoutingPolicyError('ROUTING_UPPER_POLICY_DENIED', 403)
                result = []
                for candidate in policy.candidates:
                    row = conn.execute(
                        'SELECT c.version,m.catalog_version,c.credential_version,c.access_mode,c.enabled,'
                        'c.verification_status,m.catalog_status,m.effective_capabilities,c.provider_code,'
                        'c.credential_requirement,c.encrypted_credential IS NOT NULL,c.base_url '
                        'FROM system_provider_connections c JOIN system_provider_models m '
                        'ON m.connection_id=c.connection_id JOIN system_provider_allowed_models a '
                        'ON a.connection_id=m.connection_id AND a.model_id=m.model_id '
                        'WHERE c.connection_id=%s AND m.model_id=%s FOR SHARE OF c,m,a',
                        (candidate.connection_id, candidate.model_id)).fetchone()
                    if (row is None or row[4] is not True or row[6] != 'ready'
                            or policy.capability not in row[7]):
                        raise RoutingPolicyError('ROUTING_CANDIDATE_UNAVAILABLE', 409)
                    owner, credential_version = None, row[2]
                    if candidate.credential_source == 'personal':
                        if row[3] != 'personal':
                            raise RoutingPolicyError('ROUTING_CREDENTIAL_DENIED', 403)
                        personal = conn.execute(
                            'SELECT credential_version FROM user_provider_credentials WHERE tenant_id=%s '
                            'AND user_id=%s AND connection_id=%s AND verification_status=\'verified\' '
                            'AND credential_schema_version=%s AND encrypted_credential IS NOT NULL FOR SHARE',
                            (context.tenant_id, context.actor_id, candidate.connection_id,
                             PERSONAL_CREDENTIAL_SCHEMA_VERSION)).fetchone()
                        if personal is None:
                            raise RoutingPolicyError('ROUTING_CREDENTIAL_DENIED', 403)
                        owner, credential_version = context.actor_id, personal[0]
                    elif (row[3] != 'public' or row[5] != 'verified'
                            or (row[9] == 'required' and not row[10])):
                        raise RoutingPolicyError('ROUTING_CREDENTIAL_DENIED', 403)
                    if (row[0], row[1], credential_version, row[0]) != (
                            candidate.connection_version, candidate.catalog_version,
                            candidate.credential_version, candidate.endpoint_version):
                        raise RoutingPolicyError('ROUTING_CANDIDATE_CHANGED', 409)
                    # Enforce existing current permission and upper egress policies, not a new permission.
                    permission = Permission.LOCAL_INTERNAL_LLM if row[8] == 'OLLAMA' else Permission.EXTERNAL_LLM
                    effective = _CurrentAuthorizationRules()._effective_permission(_AuthReadConnection(conn),
                        role=grant.role, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        permission=permission, requested=True)
                    if not effective.effective:
                        raise RoutingPolicyError('ROUTING_UPPER_POLICY_DENIED', 403)
                    if permission == Permission.EXTERNAL_LLM:
                        self._external_policy(conn, context, row[11])
                        if workspace[0] == 'local_private':
                            raise RoutingPolicyError('ROUTING_UPPER_POLICY_DENIED', 403)
                    result.append(VerifiedCandidate(candidate.connection_id, candidate.model_id,
                        'deployment-' + hashlib.sha256(canonical_json_bytes({
                            'connection_id': candidate.connection_id, 'model_id': candidate.model_id})).hexdigest(),
                        row[0], row[1], credential_version, row[0], candidate.credential_source, owner))
                if policy.enabled:
                    budget = self._check_budget(conn, context, policy)
                    budget_reference = digest(dict(tenant_id=budget.tenant_id, workspace_id=budget.workspace_id,
                        actor_id=budget.actor_id, version=budget.version, source_id=budget.source_id,
                        currency=budget.currency, run_limit=budget.run_limit,
                        starts_at=budget.starts_at.isoformat(), expires_at=budget.expires_at.isoformat()))
                    result = [replace(candidate, upper_budget_reference=budget_reference) for candidate in result]
                return tuple(result)
        except CloudDatabaseError:
            raise RoutingPolicyError('ROUTING_METADATA_UNAVAILABLE', 503) from None

    def _check_budget(self, conn, context, policy):
        from decimal import Decimal, InvalidOperation
        budget = None if self.budget_source is None else self.budget_source(conn, context)
        now = self.clock()
        try:
            if (not isinstance(budget, VerifiedUpperBudget)
                    or (budget.tenant_id,budget.workspace_id,budget.actor_id) !=
                        (context.tenant_id,context.workspace_id,context.actor_id)
                    or not budget.version or not budget.source_id
                    or budget.starts_at.tzinfo is None or budget.expires_at.tzinfo is None
                    or not budget.starts_at <= now < budget.expires_at
                    or budget.currency != policy.budget.currency
                    or not Decimal(budget.run_limit).is_finite() or Decimal(budget.run_limit) < 0):
                raise ValueError
            if Decimal(policy.budget.run_limit) > Decimal(budget.run_limit):
                raise RoutingPolicyError('ROUTING_UPPER_POLICY_DENIED', 403)
            return budget
        except (ValueError, TypeError, AttributeError, InvalidOperation):
            raise RoutingPolicyError('ROUTING_BUDGET_UNVERIFIED', 503) from None

    @staticmethod
    def _external_policy(conn, context, base_url):
        from .egress_policy import EgressPolicyContext, EgressPolicyService, EgressPolicyError
        from .egress_policy_postgres import PostgresEgressPolicyRepository
        class CurrentRepository:
            def current(self, ctx, scope):
                row = PostgresEgressPolicyRepository._select_current(conn, ctx, scope,
                    None if scope == 'organization' else ctx.workspace_id)
                return PostgresEgressPolicyRepository._view(row)
        ctx = EgressPolicyContext(context.tenant_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version)
        try:
            policy = EgressPolicyService(CurrentRepository()).get_effective(ctx)
            from urllib.parse import urlparse
            host = urlparse(base_url).hostname
            if (policy.mode != 'allow_approved_external' or 'external_api' not in policy.allowed_provider_kinds
                    or not host or host not in policy.allowed_destinations or policy.max_bytes <= 0):
                raise RoutingPolicyError('ROUTING_UPPER_POLICY_DENIED', 403)
        except EgressPolicyError:
            raise RoutingPolicyError('ROUTING_UPPER_POLICY_DENIED', 403) from None

    def observations(self, context, request, candidates):
        return ()


class PostgresRoutingPolicyRepository:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _current(conn, context):
        ready = conn.execute("SELECT to_regclass('public.workspace_routing_policy_current')").fetchone()
        if ready is None or ready[0] is None:
            raise RoutingPolicyError('ROUTING_POLICY_UNAVAILABLE', 503)
        return conn.execute('SELECT v.record_id,v.version,v.canonical_json,v.digest_sha256 '
            'FROM workspace_routing_policy_current p JOIN routing_policy_versions v '
            'ON v.tenant_id=p.tenant_id AND v.workspace_id=p.workspace_id AND v.record_id=p.policy_version_id '
            'WHERE p.tenant_id=%s AND p.workspace_id=%s', (context.tenant_id, context.workspace_id)).fetchone()

    def get(self, context):
        try:
            with self.store._transaction(_cloud(context, 'routing_policy.read')) as conn:
                _authorize(conn, context, Action.VIEW)
                return _view(self._current(conn, context))
        except CloudDatabaseError:
            raise RoutingPolicyError('ROUTING_POLICY_UNAVAILABLE', 503) from None

    def put(self, context, body, *, if_match, if_none_match, idempotency_key, validate):
        operation = 'routing_policy.put'
        fingerprint = digest(dict(body=body.model_dump(), if_match=if_match, if_none_match=if_none_match))
        scope = (context.tenant_id, context.workspace_id)
        replay_scope = (*scope, context.actor_id, operation, idempotency_key)
        try:
            with self.store._transaction(_cloud(context, operation)) as conn:
                # One workspace lock protects absent creation and updates under different keys.
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',
                    ('routing-policy:' + digest(dict(tenant_id=scope[0], workspace_id=scope[1])),))
                _authorize(conn, context, Action.POLICY_MANAGE)
                current = _view(self._current(conn, context))
                replay = conn.execute('SELECT request_fingerprint,result FROM idempotency_records '
                    'WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s',
                    replay_scope).fetchone()
                if replay is not None:
                    if replay[0] != fingerprint:
                        raise RoutingPolicyError('IDEMPOTENCY_KEY_REUSED', 409)
                    # Recheck current actor and candidate ownership even for an old result.
                    if body.enabled:
                        validate(context, body, connection=conn)
                    return dict(replay[1]), True
                if current['version'] != body.expected_version or (
                        current['version'] > 0 and current['etag'] != if_match):
                    raise RoutingPolicyError('VERSION_CONFLICT', 409)
                payload = body.model_dump()
                prior_body = current['body']
                safe_disable = prior_body is not None and not body.enabled and all(
                    payload[key] == prior_body[key] for key in payload if key not in {'expected_version','enabled'})
                if not safe_disable:
                    validate(context, body, connection=conn)
                record_id, version = 'routing-policy-' + uuid4().hex, current['version'] + 1
                canonical = canonical_json_bytes(payload).decode('utf-8')
                policy_digest = digest(payload)
                aggregate = 'routing-policy-' + digest(dict(tenant_id=scope[0], workspace_id=scope[1]))
                conn.execute('INSERT INTO routing_policy_versions(tenant_id,workspace_id,record_id,aggregate_id,'
                    'version,schema_version,previous_version_id,canonical_json,canonical_text,digest_sha256,created_by,trace_id) '
                    'VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s)',
                    (*scope, record_id, aggregate, version, current['policy_version_id'], Jsonb(payload), canonical,
                     policy_digest, context.actor_id, context.trace_id))
                if current['version'] == 0:
                    conn.execute('INSERT INTO workspace_routing_policy_current(tenant_id,workspace_id,policy_version_id) '
                        'VALUES(%s,%s,%s)', (*scope, record_id))
                else:
                    conn.execute('UPDATE workspace_routing_policy_current SET policy_version_id=%s '
                        'WHERE tenant_id=%s AND workspace_id=%s', (record_id, *scope))
                result = _view((record_id, version, payload, policy_digest))
                conn.execute('INSERT INTO idempotency_records(tenant_id,workspace_id,actor_id,operation,idempotency_key,'
                    "request_fingerprint,result,status,expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s,'completed','infinity'::timestamptz)", (*replay_scope, fingerprint, Jsonb(result)))
                return result, False
        except CloudDatabaseError:
            raise RoutingPolicyError('ROUTING_POLICY_UNAVAILABLE', 503) from None


def postgres_routing_policy_service(store):
    return RoutingPolicyService(PostgresRoutingPolicyRepository(store), PostgresRoutingMetadata(store))
