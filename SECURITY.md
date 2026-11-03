# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 0.1.x | yes |

## Reporting a vulnerability

Please do **not** open a public issue for security problems. Use GitHub's private
vulnerability reporting (*Security → Report a vulnerability*) on
<https://github.com/YusufBeratZengin/tuik-data>, or email the maintainer listed in
`pyproject.toml`. You can expect an acknowledgement within a few days and a fix
or mitigation plan within 30 days for confirmed issues.

## How this project handles credentials

* The API key is read from the `TUIK_API_KEY` environment variable or the
  `api_key=` argument. It is never written to disk by the library.
* The bearer token lives in memory only (`TokenManager`), and is renewed shortly
  before expiry.
* Error messages pass through `tuik_data._http.redact()`, which masks bearer
  tokens, `api_key=` values and long opaque tokens.
* The HTTP response cache under `~/.cache/tuik-data` stores **public statistics**,
  never credentials. It is plain JSON and can be deleted at any time.
* The SQLite store under `~/.local/share/tuik-data` stores series you fetched; it
  contains no personal data.

## Scope notes

* This library only performs GET requests to the TÜİK SDMX service and the TÜİK
  login realm, plus one POST to the token endpoint. `base_url` / `token_url` are
  configurable, so treat configuration from untrusted sources with care.
* Downloaded data is decoded with `json`, never `eval`/`pickle`.
* CSV output is plain text; spreadsheet formula injection is the consumer's
  concern when values begin with `=`, `+`, `-` or `@`.

## Dependency surface

There are no runtime dependencies, so there is no third-party supply chain at
install time. Development dependencies (`pytest`, `pytest-cov`, `ruff`, `mypy`) and
the optional `polars` extra are tracked by Dependabot.
