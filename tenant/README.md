# Demo tenant tooling

Scripts that set up the "Northwind Parking" Microsoft 365 demo tenant described in [Tech.MD §10](../docs/demo/Tech.MD#10-demo-tenant-reset-and-safe-mode). They need [uv](https://docs.astral.sh/uv/). Each script declares its own dependencies.

| Script | What it does | Runs as |
|---|---|---|
| `setup_app.py` | Registers the `northwind-ai-tech` app with a self-signed certificate. Grants the §7 application permissions (admin consent) and turns on the Temporary Access Pass policy. Then checks that an app-only token works. | You, signed in as Global Admin (browser or `--device-code`) |
| `seed.py` | Creates about 15 users with mixed email formats, managers, the `reports@` service account, licenses, and placeholder seat-holders so 0 seats are free. Also creates the groups and the team site, and uploads versioned files. | The app (certificate), no sign-in |
| `graph.py` | A small shared Graph client that handles retries, paging and replication lag | |
| `reset.py` | Undoes a demo. Soft-deletes users the demo created, re-enables accounts, and restores group members and owners. Clears auto-replies, inbox rules and TAPs. Puts the spare seat back on `seat-holder` and rebuilds the file versions. Checks that Dennis still has Authenticator. `--dry-run` shows what it would change. | The app |

```sh
uv run tenant/setup_app.py   # once
uv run tenant/seed.py        # safe to re-run
uv run tenant/reset.py       # before every rehearsal (--dry-run to preview)
```

### Before a real demo

The restore scenario needs file versions from **yesterday** and an overwrite from **this morning**. Graph stamps each version with the time it was uploaded, so split the reset across two days:

```sh
uv run tenant/reset.py --files base        # the evening before: full reset, files get 2 versions
uv run tenant/reset.py --files overwrite   # the morning of: full reset, adds this morning's overwrite
```

Reset never touches users created before the seed (your admin) or any UPN listed in `RESET_KEEP=` in `.env`. If the MFA demo removed Dennis's Authenticator, re-register it at https://aka.ms/mysecurityinfo; reset will remind you.

Both scripts are idempotent. Secrets stay local in `.env` and `.secrets/`, both gitignored. The secrets are the app certificate and the seeded users' passwords.

## Manual steps the scripts can't do

- **Dennis's Authenticator.** Sign in as `dennis@<domain>` on your phone and register Microsoft Authenticator. His password is in `.secrets/seed-passwords.json`. The MFA reset scenario deletes this real method.
- **Distribution list.** Graph can't create DLs. In the Exchange admin center, create `accounting-announce` and add Marcus. `seed.py` checks for it.

## Known limitations

- **Seat-holders.** During the Business Premium trial, all 14 staff are licensed. Disabled `seat-holder` users fill the remaining seats so that 0 are free. On the paid mix, only Marcus, Kevin, Taylor and Dennis are licensed, and one seat-holder is enough.
- **Dennis-suspicious variant.** This is not seeded. Mock its sign-in data, as Tech.MD allows.
- **TAP policy.** It is enabled for all users rather than a demo group. That's fine for a demo tenant.
- **App-side state.** Reset doesn't clear learned playbooks, autonomy settings or runs yet, because the app doesn't exist. There's a TODO in `reset.py`.
