# Security policy

## Report a problem

Do not open a public issue. Send a
[private report](https://github.com/andrewkomkov/metabase-cube-driver/security/advisories/new).

Tell what the problem is, how to reproduce it and which versions it has an effect on.

## Supported versions

Security fixes go to the latest release only. See the
[latest release](https://github.com/andrewkomkov/metabase-cube-driver/releases/latest).

## Verify a release

Each release has a SHA-256 file, an SBOM and a build provenance attestation. To verify a jar:

```sh
gh attestation verify cube.metabase-driver.jar --repo andrewkomkov/metabase-cube-driver
```

## Per-person data

[docs/security.md](docs/security.md) tells how a Metabase person reaches the Cube security context, and the limits.

## Demo credentials

The files in `dev/` contain throwaway credentials for fake data. Their ports bind to the loopback
interface only. These values are not secrets. Do not use them anywhere else.
