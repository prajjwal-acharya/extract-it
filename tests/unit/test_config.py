from config.settings import Env, Settings, settings


def test_settings_defaults_are_valid() -> None:
    s = Settings()
    assert s.DATABASE_URL
    assert s.MINIO_BUCKET


def test_env_enum_accepts_local() -> None:
    s = Settings(ENV=Env.LOCAL)
    assert s.ENV == Env.LOCAL


def test_env_enum_accepts_gcp() -> None:
    s = Settings(ENV=Env.GCP)
    assert s.ENV == Env.GCP


def test_gemini_model_default() -> None:
    # Must be a non-empty Gemini model name (shell env may override the .env default)
    assert settings.GEMINI_MODEL.startswith("gemini-")
