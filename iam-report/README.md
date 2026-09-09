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
| `<base>_matrix.csv` | only when comparing 2+ principals: one row per permission, one column per principal, `X` where they have it. Not generated for a single principal. |

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
