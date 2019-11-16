# -*- coding: utf-8 -*-

import logging
import os
import requests
from pathlib import Path
import configparser


def init_logger():
    logger = logging.getLogger()
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s %(name)-12s %(levelname)-8s %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if os.getenv("WINGS_DEBUG", False) else logging.INFO)


def github_credentials(profile="default"):
    creds_file = Path(os.getenv("WCM_CREDENTIALS_FILE", "~/.wcm/credentials"))
    creds = creds_file.expanduser()

    config = configparser.ConfigParser()
    config.read(creds)

    outp_creds = []

    if profile in config:
        try:
            outp_creds.append(config[profile]["gitUsername"])
            outp_creds.append(config[profile]["gitToken"])
            return outp_creds
        except KeyError:
            return []
    else:
        return []


def get_latest_version():
    return requests.get("https://pypi.org/pypi/wcm/json").json()["info"]["version"]
