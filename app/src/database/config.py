import os

from dotenv import load_dotenv

load_dotenv()


def _int_env(name: str, default: int) -> int:
    return int(os.getenv(name) or default)


class DatabaseConfig:
    user: str | None = os.getenv("POSTGRES_USER")
    password: str | None = os.getenv("POSTGRES_PASSWORD")
    host: str | None = os.getenv("POSTGRES_HOST")
    port: int = _int_env("POSTGRES_PORT", 5432)
    name: str | None = os.getenv("POSTGRES_DB")

    pool_min_size: int = _int_env("DB_POOL_MIN_SIZE", 10)
    pool_max_size: int = _int_env("DB_POOL_MAX_SIZE", 50)

    @property
    def connect_kwargs(self) -> dict[str, object]:
        # Separate kwargs, not a DSN string: a password with '@', ':' or '/' breaks a DSN.
        return {
            "user": self.user,
            "password": self.password,
            "host": self.host,
            "port": self.port,
            "database": self.name,
        }


db_config = DatabaseConfig()
