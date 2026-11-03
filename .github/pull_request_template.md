## What changed

<!-- one or two sentences -->

## Why

<!-- the problem this solves; link the issue -->

## How it was verified

```console
$ bash scripts/check.sh
...
OK: lint, types, tests and coverage (>= 85%) all passed.
```

## Checklist

- [ ] `bash scripts/check.sh` exits 0
- [ ] No official value, rate or legal figure is hard-coded (`NEEDS_SOURCE` where unsure)
- [ ] No secrets, real keys or personal data (fixtures are obviously synthetic)
- [ ] Runtime dependencies still at zero (or justified in this description)
- [ ] Turkish text is UTF-8 and missing cells remain `None`
- [ ] `CHANGELOG.md` updated for user-visible changes
