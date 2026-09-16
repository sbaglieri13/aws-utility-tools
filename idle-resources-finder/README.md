# 🧹 Idle Resources Finder

Scans one or more AWS regions (or every enabled region) for resources that
are allocated and billing but likely unused, and writes the evidence to
CSV. Read-only (`Describe*`/`List*` only), no plaintext credentials — uses
boto3's standard credential chain (`--profile`, env vars, SSO).

---

## 🔍 What it looks for

| Category | Criteria |
|---|---|
| EC2 instances | State `stopped` for more than N days (default 14, `--ec2-stopped-days`). Stop date is parsed from `StateTransitionReason`; instances where that can't be parsed are skipped rather than guessed at. |
| EBS volumes | State `available` (not attached to any instance). |
| Elastic IPs | Allocated but with no associated instance or network interface. |
| Load balancers (ALB/NLB/Classic) | No registered targets, or every registered target unhealthy. |
| EBS snapshots | Owned by the account, whose source `VolumeId` no longer exists in the region. |
| RDS instances | State `stopped`. |

---

## 📦 What you get

Six CSV files in `export/` (semicolon-delimited), one per category:

| File | Contents |
|---|---|
| `<prefix>_ec2.csv` | Region, InstanceId, Name tag, State, StoppedSince, IdleDays, console link. |
| `<prefix>_ebs.csv` | Region, VolumeId, Name tag, SizeGiB, VolumeType, CreatedOn, IdleDays (days since creation — used as a proxy for "unattached duration", since EC2 doesn't expose a detach timestamp), console link. |
| `<prefix>_eips.csv` | Region, AllocationId, PublicIp, Name tag, console link. (No age: AWS doesn't expose an allocation timestamp for Elastic IPs.) |
| `<prefix>_elb.csv` | Region, Type (APPLICATION/NETWORK/CLASSIC), LoadBalancerName, Name tag, RegisteredTargets, HealthyTargets, console link. |
| `<prefix>_snapshots.csv` | Region, SnapshotId, Name tag, SourceVolumeId, CreatedOn, IdleDays, SizeGiB, console link. |
| `<prefix>_rds.csv` | Region, DBInstanceIdentifier, Name, Engine, Status, console link. (No age: the RDS API doesn't expose a stop timestamp.) |

`<prefix>` defaults to `idle` (e.g. `idle_ec2.csv`), or whatever you pass
to `--output`. All six files are always written, even if empty, so
downstream/CI tooling can rely on their presence.

No cost estimates are included — that would require a Cost Explorer
integration, which is out of scope for this tool. Each row includes a
direct console link so you can inspect and act on it manually.

At the end, a summary table (count per category, count per region) is
printed to stdout.

---

## 📊 How to read the results

Just double-click a CSV to open it in Excel — columns split automatically.
Each row is a candidate to check, not a verdict: click `ConsoleLink` to
open it in the AWS console before deciding anything.

| File | A row means... | Check before acting |
|---|---|---|
| `idle_ec2.csv` | Instance stopped for ≥ threshold days | `IdleDays`, `Name` tag |
| `idle_ebs.csv` | Volume not attached to any instance | `IdleDays` is age since *creation*, not since detach — could be brand new |
| `idle_eips.csv` | Elastic IP with nothing attached | usually pure cost — no legit reason to keep one unattached |
| `idle_elb.csv` | Load balancer with 0 targets, or all targets unhealthy | `HealthyTargets` = 0 with targets registered could mean a real outage, not idle |
| `idle_snapshots.csv` | Source volume no longer exists | snapshot still usable to create a new volume — not automatically worthless |
| `idle_rds.csv` | RDS instance stopped | AWS auto-restarts stopped RDS instances after 7 days |

An empty file just means nothing was found — that's normal. Also check
the `[WARN] ...` lines printed during the scan: a region/category that
failed (permissions, throttling) won't show up as a warning in the CSV,
only on screen, so a clean file doesn't always mean "verified clean".

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
bash idle_resources.sh
```

Asks: region mode (specific region(s), or all enabled regions), the EC2
stopped-days threshold (default 14), and an optional output file prefix.

Direct call, equivalent to the above:

```bash
python idle_resources.py --region eu-west-1 --region us-east-1 --ec2-stopped-days 30 --output idle
```

or, to scan every enabled region on the account:

```bash
python idle_resources.py --all-regions
```

`--region` is repeatable (ignored if `--all-regions` is set). If neither
is given, the session's default region is used. `--ec2-stopped-days` and
`--output` are optional. A `--profile` flag also exists for direct/scripted
calls if you'd rather not export `AWS_PROFILE`.

Errors or missing permissions in a single region (throttling, access
denied, etc.) are logged as warnings to stderr and skipped — they don't
stop the scan of other regions or other resource categories.

> First run creates a local virtualenv (`.venv/`) and installs `boto3`
> automatically — nothing else to set up.

---

## 🔐 Required IAM permissions

There's no single AWS-managed policy that covers exactly this read-only
surface, so grant these actions explicitly (all `Describe*`/`List*`,
read-only):

```
ec2:DescribeRegions
ec2:DescribeInstances
ec2:DescribeVolumes
ec2:DescribeAddresses
ec2:DescribeSnapshots
elasticloadbalancing:DescribeLoadBalancers
elasticloadbalancing:DescribeTargetGroups
elasticloadbalancing:DescribeTargetHealth
elasticloadbalancing:DescribeInstanceHealth
elasticloadbalancing:DescribeTags
rds:DescribeDBInstances
```

The broad AWS-managed `ReadOnlyAccess` policy also covers this (and much
more) if you'd rather use an existing managed policy than a custom one.
