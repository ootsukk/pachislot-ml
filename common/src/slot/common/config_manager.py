import logging.config
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class AppConfig(BaseModel):
    name: str
    environment: str = Field(pattern="^(development|staging|production)$")
    timeout_seconds: int = Field(gt=0, le=300)

class Settings(BaseSettings):
    app: AppConfig

    logging: dict

    @classmethod
    def load_from_yaml(cls, yaml_path: Path) -> Settings:
        with open(yaml_path, encoding="utf-8") as file:
            raw_config = yaml.safe_load(file)

        return cls(**raw_config)


def initialize_system(base_dir: Path) -> None:
    config_path = base_dir / Path("config") / Path("app_config.yaml")

    settings = Settings.load_from_yaml(config_path)

    logging.config.dictConfig(settings.logging)

    logger = logging.getLogger(__name__)
    logger.info("System initialized in %s mode", settings.app.environment)
