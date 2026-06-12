import logging
from pathlib import Path

from slot.common import config_manager, utility


def main():

    utility.normalize_int("111")

    config_manager.initialize_system(Path.cwd())

    logger = logging.getLogger(__name__)
    logger.info("サンプル")


if __name__ == "__main__":
    main()
