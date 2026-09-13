"""Project configuration loader."""
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field
from dotenv import dotenv_values


class AppConfig(BaseModel):
    baserow_api_url: str = Field(default="https://api.baserow.io")
    baserow_api_token: Optional[str] = None
    baserow_mcp_url: Optional[str] = None
    baserow_media_table_id: Optional[str] = None
    baserow_category_table_id: Optional[str] = None
    baserow_database_id: Optional[str] = None
    youtube_api_key_kf: Optional[str] = None
    youtube_api_key_kksblog: Optional[str] = None
    media_archive_path: Optional[Path] = None
    media_archive_output_path: Optional[Path] = None
    
    # Tool 1 operational settings
    registry_path: Path = Field(default=Path(".renamer/registry.db"))
    log_dir: Path = Field(default=Path(".renamer/logs"))


def load_config(env_path: Optional[Path] = None) -> AppConfig:
    """Load configuration from .env file and environment variables."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    target_env = env_path or (root_dir / ".env")
    env_vars = {}
    if target_env.exists():
        env_vars = dotenv_values(target_env)
    
    return AppConfig(
        baserow_api_url=env_vars.get("BASEROW_API_URL") or "https://api.baserow.io",
        baserow_api_token=env_vars.get("BASEROW_API_TOKEN"),
        baserow_mcp_url=env_vars.get("BASEROW_MCP_URL"),
        baserow_media_table_id=env_vars.get("BASEROW_MEDIA_TABLE_ID"),
        baserow_category_table_id=env_vars.get("BASEROW_CATEGORY_TABLE_ID"),
        baserow_database_id=env_vars.get("BASEROW_DATABASE_ID"),
        youtube_api_key_kf=env_vars.get("YOUTUBE_API_KEY_KF"),
        youtube_api_key_kksblog=env_vars.get("YOUTUBE_API_KEY_KKSBLOG"),
        media_archive_path=Path(env_vars["MEDIA_ARCHIVE_PATH"]) if env_vars.get("MEDIA_ARCHIVE_PATH") else None,
        media_archive_output_path=Path(env_vars["MEDIA_ARCHIVE_OUTPUT_PATH"]) if env_vars.get("MEDIA_ARCHIVE_OUTPUT_PATH") else None,
    )
