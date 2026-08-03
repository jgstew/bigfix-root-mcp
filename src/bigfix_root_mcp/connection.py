"""BigFix connection configuration and a cached BESConnection.

Config reading is reimplemented here instead of calling
besapi.besapi.get_bes_conn_using_config_file() because that helper writes
to stdout (which corrupts the MCP stdio transport) and hardcodes
verify=False. See docs/besapi-proposals.md for the proposed upstream fix;
once besapi ships it this module shrinks to a call into besapi.

This package never uses BESConnection.set_current_site_path /
get_current_site_path or reads conn.site_path: that mutable "current site"
state is a bescli convenience, and every tool here takes an explicit
site_path parameter instead.
"""

import configparser
import dataclasses
import logging
import os
from typing import Optional, Union

import besapi.besapi

logger = logging.getLogger(__name__)

# same search order as besapi.besapi.get_bes_conn_using_config_file:
CONFIG_PATHS = [
    "/etc/besapi.conf",
    os.path.expanduser("~/besapi.conf"),
    os.path.expanduser("~/.besapi.conf"),
    "besapi.conf",
]


class ConnectionConfigError(RuntimeError):
    """No usable BigFix connection configuration was found."""


@dataclasses.dataclass(frozen=True)
class BESConfig:
    """Resolved BigFix REST API connection settings."""

    rootserver: str
    username: str
    password: str
    # False (besapi default) | True | path to a CA bundle:
    verify: Union[bool, str] = False


def _parse_verify(value: str) -> Union[bool, str]:
    lowered = value.strip().lower()
    if lowered in ("", "0", "false", "no"):
        return False
    if lowered in ("1", "true", "yes"):
        return True
    # anything else is treated as a CA bundle path, passed through to requests
    return value.strip()


def load_config() -> BESConfig:
    """Resolve connection settings: env vars win, then besapi.conf files.

    Env vars: BES_ROOT_SERVER, BES_USER_NAME, BES_PASSWORD, BES_SSL_VERIFY.
    Config files (first found wins): [besapi] section with the same keys.
    """
    rootserver = os.environ.get("BES_ROOT_SERVER", "").strip()
    username = os.environ.get("BES_USER_NAME", "").strip()
    password = os.environ.get("BES_PASSWORD", "")
    verify_raw = os.environ.get("BES_SSL_VERIFY", "").strip()

    if not (rootserver and username and password):
        for path in CONFIG_PATHS:
            if not os.path.isfile(path):
                continue
            parser = configparser.ConfigParser()
            try:
                parser.read(path)
            except configparser.Error as err:
                logger.warning("Skipping unparsable config file %s: %s", path, err)
                continue
            if not parser.has_section("besapi"):
                continue
            section = parser["besapi"]
            rootserver = rootserver or section.get("BES_ROOT_SERVER", "").strip()
            username = username or section.get("BES_USER_NAME", "").strip()
            password = password or section.get("BES_PASSWORD", "")
            verify_raw = verify_raw or section.get("BES_SSL_VERIFY", "").strip()
            if rootserver and username and password:
                logger.info("Loaded BigFix connection config from %s", path)
                break

    if not (rootserver and username and password):
        raise ConnectionConfigError(
            "No BigFix connection configuration found. Set BES_ROOT_SERVER, "
            "BES_USER_NAME and BES_PASSWORD environment variables, or create a "
            "besapi.conf file with a [besapi] section in one of: "
            + ", ".join(CONFIG_PATHS)
        )

    return BESConfig(
        rootserver=rootserver,
        username=username,
        password=password,
        verify=_parse_verify(verify_raw),
    )


_conn: Optional[besapi.besapi.BESConnection] = None


def get_connection() -> besapi.besapi.BESConnection:
    """Lazy module-level singleton BESConnection.

    Cached because the constructor performs a login round-trip, `with`
    support is broken upstream (no __exit__), and bool(conn) re-triggers
    login(). Construction raises requests.HTTPError on bad credentials.
    """
    global _conn
    if _conn is None:
        config = load_config()
        _conn = besapi.besapi.BESConnection(
            config.username,
            config.password,
            config.rootserver,
            verify=config.verify,
        )
    return _conn


def reset_connection() -> None:
    """Drop the cached connection (tests; recovery after auth errors)."""
    global _conn
    _conn = None
