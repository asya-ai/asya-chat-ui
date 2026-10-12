from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine

from app.services import whitelabel as whitelabel_service


def test_whitelabel_logo_roundtrip(tmp_path: Path, monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr("app.services.file_storage.settings.files_base_dir", str(tmp_path))

    with Session(engine) as session:
        row = whitelabel_service.update_app_title(session, "Acme Chat")
        assert row.app_title == "Acme Chat"

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        row = whitelabel_service.store_logo(
            session,
            data=png,
            content_type="image/png",
            file_name="logo.png",
        )
        assert row.logo_path
        assert row.logo_content_type == "image/png"

        payload = whitelabel_service.read_logo(session)
        assert payload is not None
        data, content_type = payload
        assert data == png
        assert content_type == "image/png"

        row = whitelabel_service.clear_logo(session)
        assert row.logo_path is None
        assert whitelabel_service.read_logo(session) is None
