# 📋 IAM Report

Extracts AWS IAM policy data for one or more users/roles and writes it to
CSV. Read-only (`List*`/`Get*` only), no plaintext credentials — uses
boto3's standard credential chain (`--profile`, env vars, SSO).

---

## 📦 What you get

For every user/role given, three CSV files in `export/` (semicolon-delimited):

| File | Contents |
|---|---|
| `<base>_profile.csv` | one row per principal: ARN, path, creation date, tags, and (for users) groups, console access, MFA, access keys. |
| `<base>_permissions.csv` | one row per statement/action: source (inline, managed, group-inherited, permission boundary, trust policy), policy name/version, effect, action, resource, principal, condition. |
| `<base>_matrix.csv` | only when comparing 2+ principals: one row per permission, one column per principal — granted or not, open or resource-restricted, flagged if a `Deny` complicates things (see below). Not generated for a single principal. |

---

## 📊 How to read the results

Just double-click a CSV to open it in Excel — columns split automatically
(semicolon-delimited, UTF-8 with BOM).

**`_profile.csv`** — one row per principal, the identity/hygiene view:

| Column | Meaning |
|---|---|
| `ARN`, `Path`, `Created` | identity basics |
| `Tags` | `Key=Value` pairs, comma-separated |
| `Groups` | (users only) IAM groups the user belongs to |
| `ConsoleAccess` / `MFAActive` | (users only) — `ConsoleAccess=True` + `MFAActive=False` is the classic red flag |
| `AccessKeys` | `KeyId:Status:last_used=...`, one entry per key — `last_used=never` on an `Active` key is a cleanup candidate |
| `RoleLastUsed` | (roles only) when the role was last assumed |
| `TrustedBy` | (roles only) unique `Principal` values from the trust policy's `Allow` statements, semicolon-separated — quick "who can assume this role" view. Full per-statement detail (including `Deny` and any `Condition`) is still in `_permissions.csv` under `Source = TrustPolicy`. |

**`_permissions.csv`** — one row per statement/action, the full detail:

| Column | Meaning |
|---|---|
| `Source` | where the grant comes from: `Inline`, `ManagedAWS`/`ManagedCustomer` (attached policy), `GroupInline`/`Group...` (inherited via group membership), `PermissionsBoundary`, or `TrustPolicy` |
| `Policy`, `PolicyVersion`, `ARN` | which policy the statement lives in |
| `Effect` | `Allow` or `Deny` — a `Deny` here always overrides a matching `Allow`, even one from a different policy |
| `Action`, `Resource` | the actual permission and what it's scoped to (`*` = unrestricted) |
| `Principal`, `Condition` | filled in for trust-policy rows (who can assume the role) and conditional statements |

This file has the ground truth for everything the matrix compresses — if
you need the exact resource ARN(s) behind a permission, filter this file
by `Subject` + `Action`.

**`_matrix.csv`** (2+ principals only) — quick comparison, one row per
action, one column per principal:

- `X*` = granted, no resource restriction (`Resource: "*"`)
- `X` = granted, but only on specific resource(s) — the ARNs aren't shown
  here (they'd make the sheet unreadable); look them up in
  `_permissions.csv` (filter `Subject` = that column, `Action` = that row)
- `X*!` / `X!` = same, but an explicit `Deny` also exists for that
  subject/action — since `Deny` always wins, real access may be narrower
  than shown; check `_permissions.csv` (`Effect = Deny`) for what's carved out
- blank = not granted

Trust policies (`Source = TrustPolicy`) also don't appear in the matrix,
since they grant *assuming the role*, not an action on a resource — check
them directly in `_permissions.csv`.

---

## 🔑 Setup (once per terminal session)

Log in and select the AWS profile to use, *before* running the script —
it always uses whichever credentials/profile are currently active, it
never asks:

```bash
aws sso login --profile YourProfileName
export AWS_PROFILE=YourProfileName
```

---

## ▶️ Usage

```bash
bash iam_report.sh
```

Asks: type (user or role), how many principals (default 1), each
name/ARN, and an optional output file name. 1 principal = plain extract; 2+ = extract + comparison matrix.

Direct call, equivalent to the above:

```bash
python iam_report.py --type role --name GucciGDIDeveloper --name GucciGDIOperator --output my_report
```

`--name` is repeatable (name or full ARN). `--output` is optional. A
`--profile` flag also exists for direct/scripted calls if you'd rather not
export `AWS_PROFILE`.

> First run creates a local virtualenv (`.venv/`) and installs `boto3`
> automatically — nothing else to set up. Needs read-only IAM access
> (`IAMReadOnlyAccess` covers it).
