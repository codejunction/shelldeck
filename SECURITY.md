# Security policy

shelldeck gives a browser full shell access to the machine it runs on, so security reports matter a lot.

## Reporting a vulnerability

Please **don't open a public issue**. Report it privately through [GitHub security advisories](https://github.com/codejunction/shelldeck/security/advisories/new).

Include the version (`sd --help` or `pip show shelldeck`), your OS, and steps to reproduce. You'll get an answer within a week. Fixes ship in a patch release, and you'll be credited unless you'd rather not be.

## Supported versions

Only the latest release gets security fixes.

## Security model

- **Local by default.** The server binds to `127.0.0.1` and rejects requests whose `Host` or `Origin` isn't the app itself.
- **Password required.** Every API call and terminal socket needs a login. Passwords are stored as PBKDF2-SHA256.
- **Remote use.** Remote access goes over an SSH tunnel or HTTPS. Plain HTTP on a non-local address is refused unless you pass `--insecure-http`.

The [README](README.md#security) has the details.
