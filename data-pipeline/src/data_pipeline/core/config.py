from dataclasses import dataclass

from pydantic import HttpUrl


@dataclass(frozen=True, kw_only=True)
class Site:
    name: str
    top_page_url: HttpUrl
    site_map: dict[str, HttpUrl]
