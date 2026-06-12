from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

@dataclass(frozen=True, kw_only=True)
class Url:
    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("URL cannot be empty.")
        try:
            parsed = urlparse(self.value)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(f"Invalid URL structure: {self.value}")
            if parsed.scheme not in {"http", "https"}:
                raise ValueError(f"Unsupported URL scheme: {parsed.scheme}")
        except Exception as e:
            raise ValueError(f"Failed to parse URL: {self.value}") from e

    def __str__(self) -> str:
        return self.value

    @property
    def domain(self) -> str:
        return urlparse(self.value).netloc

    @property
    def is_secure(self) -> bool:
        return urlparse(self.value).scheme == "https"

    @property
    def query_params(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.value).query)
