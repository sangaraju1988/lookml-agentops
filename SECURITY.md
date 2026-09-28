# Security policy

## Reporting a vulnerability

Please report vulnerabilities privately through GitHub Security Advisories ("Report a
vulnerability" on the repository's Security tab). Don't open a public issue. We aim to
acknowledge reports within 5 business days.

## Design commitments

* Credentials are read **only** from environment variables. They are never logged, printed,
  or written to disk.
* Run history and reports store summaries and query *structures* (explore, fields, filters,
  aggregated results for the synthetic demo). They never store raw row-level data from a real
  Looker instance. Real runners store only aggregate result fingerprints.
* All bundled data is synthetic and generated with fixed seeds.
