import json

from sqlmodel import Session, SQLModel, create_engine, select

from app.core.secret_crypto import decrypt_secret
from app.models import InstanceProviderConfig, InstanceProviderEnvSuppression
from app.services.instance_providers import (
    delete_instance_provider,
    env_key_stored_in_db,
    is_env_import_suppressed,
    migrate_env_providers,
    provider_is_configured,
    resolve_instance_credentials,
)


class _SettingsProbe:
    openai_api_key: str = "sk-test-openai"
    openai_base_url: str = "https://api.openai.com/v1"


def test_migrate_env_providers_imports_openai_key(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(
        "app.services.instance_providers.settings",
        _SettingsProbe(),
    )

    with Session(engine) as session:
        migrated = migrate_env_providers(session)
        assert migrated == ["openai"]

        record = session.exec(
            select(InstanceProviderConfig).where(
                InstanceProviderConfig.provider == "openai"
            )
        ).one()
        assert record.migrated_from_env is True
        assert decrypt_secret(record.api_key) == "sk-test-openai"

        creds = resolve_instance_credentials(session, "openai")
        assert creds.api_key == "sk-test-openai"
        assert provider_is_configured(creds, "openai")
        assert env_key_stored_in_db(session, "OPENAI_API_KEY")


def test_migrate_env_providers_skips_when_already_configured(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(
        "app.services.instance_providers.settings",
        _SettingsProbe(),
    )

    with Session(engine) as session:
        session.add(
            InstanceProviderConfig(
                provider="openai",
                provider_type="builtin",
                api_key="enc:v1:existing",
                is_enabled=True,
            )
        )
        session.commit()

        migrated = migrate_env_providers(session)
        assert migrated == []


def test_vertex_config_json_is_encrypted_at_rest(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    vertex_json = json.dumps(
        {
            "type": "service_account",
            "project_id": "demo-project",
            "private_key": "secret-key-material",
            "client_email": "demo@demo.iam.gserviceaccount.com",
        }
    )

    class _VertexSettings:
        gemini_vertex_json = vertex_json
        google_vertex_project = None
        google_vertex_location = "global"

    monkeypatch.setattr(
        "app.services.instance_providers.settings",
        _VertexSettings(),
    )

    with Session(engine) as session:
        from app.services.instance_providers import _parse_stored_config_json

        migrated = migrate_env_providers(session)
        assert migrated == ["vertex"]

        record = session.exec(
            select(InstanceProviderConfig).where(
                InstanceProviderConfig.provider == "vertex"
            )
        ).one()
        assert record.config_json.startswith("enc:v1:")
        parsed = _parse_stored_config_json(record.config_json)
        assert parsed is not None
        assert parsed.get("private_key") == "secret-key-material"

        creds = resolve_instance_credentials(session, "vertex")
        assert creds.config is not None
        assert creds.config.get("project_id") == "demo-project"


def test_delete_instance_provider_suppresses_env_reimport(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)

    monkeypatch.setattr(
        "app.services.instance_providers.settings",
        _SettingsProbe(),
    )

    with Session(engine) as session:
        migrate_env_providers(session)
        delete_instance_provider(session, "openai")
        assert (
            session.exec(select(InstanceProviderConfig)).first() is None
        )
        assert is_env_import_suppressed(session, "openai")

        migrated_again = migrate_env_providers(session)
        assert migrated_again == []
