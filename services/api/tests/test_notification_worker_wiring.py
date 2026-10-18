"""기존 worker 실제 설정/CloudStore 생성 연결. DB·모델·Secret 파일 I/O는 실행하지 않는다."""
from dataclasses import replace

import pytest

from daon_user_api.cloud_storage import PostgresCloudStore
from daon_user_api import document_processing_worker as document
from daon_user_api import studio_generation_worker as studio


def _environment(monkeypatch, value):
    monkeypatch.setenv('DAON_CLOUD_DATABASE_DSN', 'dbname=s42-no-connection')
    monkeypatch.setenv('DAON_OBJECT_STORAGE_ENDPOINT', 'object.invalid')
    monkeypatch.setenv('DAON_OBJECT_STORAGE_BUCKET', 's42-no-io')
    for name in ('DAON_OBJECT_ACCESS_KEY_FILE', 'DAON_OBJECT_SECRET_KEY_FILE', 'DAON_PROVIDER_CREDENTIAL_KEY_FILE'):
        monkeypatch.setenv(name, '/synthetic-not-read')
    if value is None:
        monkeypatch.delenv('DAON_NOTIFICATION_INBOX_ENABLED', raising=False)
    else:
        monkeypatch.setenv('DAON_NOTIFICATION_INBOX_ENABLED', value)


@pytest.mark.parametrize('module', [document, studio], ids=['document', 'studio'])
@pytest.mark.parametrize('value,expected', [(None, False), ('false', False), ('true', True)])
def test_worker_settings_preserve_strict_existing_notification_flag(monkeypatch, module, value, expected):
    _environment(monkeypatch, value)
    settings_type = module.DocumentWorkerSettings if module is document else module.StudioWorkerSettings
    settings = settings_type.from_env()
    assert type(getattr(settings, 'notification_inbox_enabled', None)) is bool
    assert settings.notification_inbox_enabled is expected


@pytest.mark.parametrize('module', [document, studio], ids=['document', 'studio'])
@pytest.mark.parametrize('value', ['', 'TRUE', '1', 'yes', ' false '])
def test_worker_invalid_notification_environment_is_rejected(monkeypatch, module, value):
    _environment(monkeypatch, value)
    settings_type = module.DocumentWorkerSettings if module is document else module.StudioWorkerSettings
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        settings_type.from_env()


@pytest.mark.parametrize('module', [document, studio], ids=['document', 'studio'])
@pytest.mark.parametrize('value', [None, 0, 1, 'true'])
def test_worker_settings_do_not_coerce_non_boolean_flag(monkeypatch, module, value):
    _environment(monkeypatch, 'true')
    settings_type = module.DocumentWorkerSettings if module is document else module.StudioWorkerSettings
    with pytest.raises(ValueError, match='^NOTIFICATION_INBOX_FLAG_INVALID$'):
        replace(settings_type.from_env(), notification_inbox_enabled=value)


class _StopAfterStore(Exception):
    pass


class _ObjectKeyReference:
    def read_text(self, **_options):
        return 'synthetic-reference-only'


class _ProviderReference:
    def read_bytes(self):
        raise _StopAfterStore


@pytest.mark.parametrize('module', [document, studio], ids=['document', 'studio'])
@pytest.mark.parametrize('value,expected', [(None, False), ('false', False), ('true', True)])
def test_actual_worker_start_constructs_real_cloud_store_with_flag(monkeypatch, module, value, expected):
    _environment(monkeypatch, value)
    created = []

    def construct(*args, **kwargs):
        # 실제 제품 constructor/엄격 flag/ConnectionPool 설정을 실행한다. pool은 아직 open=False다.
        store = PostgresCloudStore(*args, **kwargs)
        created.append(store)
        return store

    monkeypatch.setattr(module, 'PostgresCloudStore', construct)
    try:
        if module is document:
            settings = replace(document.DocumentWorkerSettings.from_env(),
                object_access_key_file=_ObjectKeyReference(), object_secret_key_file=_ObjectKeyReference(),
                provider_credential_key_file=_ProviderReference())
            with pytest.raises(_StopAfterStore):
                document.build_worker(settings)
        else:
            def stop_before_queue(*_args, **_kwargs):
                raise _StopAfterStore
            monkeypatch.setattr(studio, 'PostgresStudioGenerationQueue', stop_before_queue)
            with pytest.raises(_StopAfterStore):
                studio.main()
        assert len(created) == 1 and type(created[0]) is PostgresCloudStore
        assert created[0]._notification_inbox_enabled is expected
        assert created[0]._pool.closed is True
    finally:
        for store in created:
            store.close()
