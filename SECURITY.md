# Security policy

SentinelAI is a research and portfolio project that happens to be about
security, so it would be embarrassing to handle its own vulnerabilities badly.

## Supported versions

Only the latest tagged release receives fixes. There is no long-term support
branch and no backporting.

| Version | Supported |
| --- | --- |
| 3.0.x | yes |
| < 3.0 | no |

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Use GitHub's private reporting: **Security -> Report a vulnerability** on the
repository. If that is unavailable, email tusharentheoria@gmail.com with
`SENTINELAI SECURITY` in the subject.

Include the commit SHA, the affected file and line, what an attacker gains, and
a reproduction if you have one. A proof of concept is welcome but not required;
a clear description of the flaw is worth more than a fragile exploit.

Expect an acknowledgement within 7 days and an assessment within 30. If a fix
is warranted you will be credited in the CHANGELOG unless you ask otherwise.

## Scope

In scope, and genuinely interesting:

- The authentication layer in `sentinelai/api.py`: token forgery, signature
  bypass, `alg` confusion, audience or expiry checks that can be skipped,
  timing leaks in comparison.
- Role escalation across `viewer < analyst < responder < admin`.
- Anything that lets the hash-chained audit log in `sentinelai/store.py` be
  rewritten, truncated or replayed without `verify_chain()` noticing. The
  append-only triggers are meant to be load bearing.
- SQL injection. Every query should be parameterised; `executescript` calls
  interpolate module constants only, never input. A counterexample is a bug.
- Idempotency key handling that allows a containment action to fire twice.
- Secret leakage through logs. `obs.py` redacts in the formatter precisely so
  that a careless call site cannot defeat it.

Out of scope:

- The synthetic data generator producing unrealistic traffic. That is a
  modelling limitation, documented in README section 7, not a vulnerability.
- Model evasion. Adversarial robustness is an open research problem and the
  README already states the detector can be evaded; see the per-family recall
  table for how badly.
- Denial of service by pointing the pipeline at an enormous corpus.
- Anything requiring a compromised local machine, since the threat model
  assumes the operator's host is trusted.

## Known weaknesses, already disclosed

These are documented rather than hidden, and are not accepted as new reports:

- The stdlib HTTP server is not hardened for internet exposure. Production
  deployment assumes a reverse proxy terminating TLS; `config.py` refuses to
  bind `0.0.0.0` in prod without an explicit override for this reason.
- Tokens are bearer tokens with no revocation list. Short TTLs are the only
  mitigation.
- Rate limiting is per-process and in-memory, so it resets on restart and does
  not coordinate across replicas.
