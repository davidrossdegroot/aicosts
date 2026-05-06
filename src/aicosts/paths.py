from pathlib import Path

from platformdirs import user_config_dir, user_data_dir

APP = "aicosts"


def data_dir() -> Path:
    p = Path(user_data_dir(APP))
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    p = Path(user_config_dir(APP))
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return data_dir() / "db.sqlite"


def raw_dir(provider: str) -> Path:
    p = data_dir() / "raw" / provider
    p.mkdir(parents=True, exist_ok=True)
    return p


def projects_toml() -> Path:
    return config_dir() / "projects.toml"
