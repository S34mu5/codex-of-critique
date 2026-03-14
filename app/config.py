from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "local"
    log_level: str = "INFO"

    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_db: str = "github_reviews"
    mysql_user: str = "app"
    mysql_password: str = "app_password"

    github_owner: str = ""
    github_repo: str = ""
    github_token: str = ""

    sync_cron: str = "*/15 * * * *"
    snippet_context_lines: int = 10

    github_graphql_url: str = "https://api.github.com/graphql"
    github_rest_url: str = "https://api.github.com"
    github_request_timeout_seconds: int = 30
    github_max_retries: int = 5
    github_min_remaining_budget: int = 200

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False)

    @property
    def database_url(self) -> str:
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_db}"
            "?charset=utf8mb4"
        )


settings = Settings()
