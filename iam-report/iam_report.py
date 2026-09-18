import argparse
import csv
import json
import sys
from pathlib import Path
import boto3
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound

PERM_FIELDS = ["Subject", "SubjectType", "Source", "Policy", "PolicyVersion", "ARN", "Sid", "Effect", "Action", "Resource", "Principal", "Condition"]
PROFILE_FIELDS = ["Subject", "SubjectType", "ARN", "Path", "Created", "Tags", "RoleLastUsed", "Groups", "ConsoleAccess", "MFAActive", "AccessKeys", "TrustedBy"]


def name_from_arn(value):
    if not value.startswith("arn:"):
        return value
    return value.split(":", 5)[-1].rsplit("/", 1)[-1]


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return list(value)


def is_aws_managed(arn):
    return arn.startswith("arn:aws:iam::aws:policy/")


def fetch_policy(iam, arn):
    meta = iam.get_policy(PolicyArn=arn)["Policy"]
    version = iam.get_policy_version(PolicyArn=arn, VersionId=meta["DefaultVersionId"])
    doc = version["PolicyVersion"]["Document"]
    source = "ManagedAWS" if is_aws_managed(arn) else "ManagedCustomer"
    return source, meta["PolicyName"], meta["DefaultVersionId"], arn, doc


def fetch_inline(iam, ptype, name, source):
    list_op = {"user": "list_user_policies", "role": "list_role_policies", "group": "list_group_policies"}[ptype]
    get_op = {"user": iam.get_user_policy, "role": iam.get_role_policy, "group": iam.get_group_policy}[ptype]
    key = {"user": "UserName", "role": "RoleName", "group": "GroupName"}[ptype]
    out = []
    for page in iam.get_paginator(list_op).paginate(**{key: name}):
        for policy_name in page["PolicyNames"]:
            doc = get_op(**{key: name, "PolicyName": policy_name})["PolicyDocument"]
            out.append((source, policy_name, "", "", doc))
    return out


def fetch_attached(iam, ptype, name, source_prefix):
    list_op = {"user": "list_attached_user_policies", "role": "list_attached_role_policies",
               "group": "list_attached_group_policies"}[ptype]
    key = {"user": "UserName", "role": "RoleName", "group": "GroupName"}[ptype]
    out = []
    for page in iam.get_paginator(list_op).paginate(**{key: name}):
        for pol in page["AttachedPolicies"]:
            source, pname, version, arn, doc = fetch_policy(iam, pol["PolicyArn"])
            label = source if not source_prefix else f"Group{source}"
            out.append((label, pname, version, arn, doc))
    return out


def fetch_tags(iam, ptype, name):
    op = {"user": "list_user_tags", "role": "list_role_tags"}[ptype]
    key = {"user": "UserName", "role": "RoleName"}[ptype]
    tags = []
    for page in iam.get_paginator(op).paginate(**{key: name}):
        tags.extend(page["Tags"])
    return ", ".join(f"{t['Key']}={t['Value']}" for t in tags)


def fetch_user_security(iam, name):
    console = False
    try:
        iam.get_login_profile(UserName=name)
        console = True
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise

    mfa = len(iam.list_mfa_devices(UserName=name)["MFADevices"]) > 0

    keys = iam.list_access_keys(UserName=name)["AccessKeyMetadata"]
    key_parts = []
    for k in keys:
        used = iam.get_access_key_last_used(AccessKeyId=k["AccessKeyId"]).get("AccessKeyLastUsed", {})
        last = used.get("LastUsedDate")
        key_parts.append(f"{k['AccessKeyId']}:{k['Status']}:last_used={last or 'never'}")

    return console, mfa, key_parts


def collect(iam, ptype, name):
    if ptype == "role":
        meta = iam.get_role(RoleName=name)["Role"]
    else:
        meta = iam.get_user(UserName=name)["User"]

    blocks = []
    blocks += fetch_inline(iam, ptype, name, "Inline")
    blocks += fetch_attached(iam, ptype, name, "")

    boundary = meta.get("PermissionsBoundary")
    if boundary:
        _, pname, version, arn, doc = fetch_policy(iam, boundary["PermissionsBoundaryArn"])
        blocks.append(("PermissionsBoundary", pname, version, arn, doc))

    groups, console, mfa, keys = [], False, False, []
    if ptype == "user":
        for page in iam.get_paginator("list_groups_for_user").paginate(UserName=name):
            for g in page["Groups"]:
                gname = g["GroupName"]
                groups.append(gname)
                blocks += fetch_inline(iam, "group", gname, "GroupInline")
                blocks += fetch_attached(iam, "group", gname, "Group")
        console, mfa, keys = fetch_user_security(iam, name)

    trust = None
    trusted_by = ""
    if ptype == "role" and meta.get("AssumeRolePolicyDocument"):
        trust = meta["AssumeRolePolicyDocument"]
        principals = [json.dumps(stmt["Principal"]) for stmt in trust.get("Statement", [])
                      if stmt.get("Effect") == "Allow" and "Principal" in stmt]
        trusted_by = "; ".join(dict.fromkeys(principals))

    return {
        "type": ptype,
        "name": name,
        "arn": meta["Arn"],
        "path": meta.get("Path"),
        "created": meta.get("CreateDate"),
        "tags": fetch_tags(iam, ptype, name),
        "role_last_used": meta.get("RoleLastUsed", {}).get("LastUsedDate"),
        "groups": groups,
        "console": console,
        "mfa": mfa,
        "keys": keys,
        "trust": trust,
        "trusted_by": trusted_by,
        "blocks": blocks,
    }


def permission_rows(data):
    rows = []
    subject, subject_type = data["name"], data["type"]
    for source, policy, version, arn, doc in data["blocks"]:
        for stmt in doc.get("Statement", []):
            for action in as_list(stmt.get("Action")) or as_list(stmt.get("NotAction")):
                rows.append({
                    "Subject": subject, "SubjectType": subject_type, "Source": source,
                    "Policy": policy, "PolicyVersion": version, "ARN": arn,
                    "Sid": stmt.get("Sid", ""), "Effect": stmt.get("Effect", ""),
                    "Action": action,
                    "Resource": "; ".join(as_list(stmt.get("Resource")) or as_list(stmt.get("NotResource"))),
                    "Principal": "", "Condition": json.dumps(stmt["Condition"]) if "Condition" in stmt else "",
                })
    if data["trust"]:
        for stmt in data["trust"].get("Statement", []):
            for action in as_list(stmt.get("Action")):
                rows.append({
                    "Subject": subject, "SubjectType": subject_type, "Source": "TrustPolicy",
                    "Policy": "", "PolicyVersion": "", "ARN": data["arn"],
                    "Sid": stmt.get("Sid", ""), "Effect": stmt.get("Effect", ""),
                    "Action": action, "Resource": "",
                    "Principal": json.dumps(stmt["Principal"]) if "Principal" in stmt else "",
                    "Condition": json.dumps(stmt["Condition"]) if "Condition" in stmt else "",
                })
    return rows


def profile_row(data):
    return {
        "Subject": data["name"], "SubjectType": data["type"], "ARN": data["arn"],
        "Path": data["path"], "Created": data["created"], "Tags": data["tags"],
        "RoleLastUsed": data["role_last_used"] or "", "Groups": ", ".join(data["groups"]),
        "ConsoleAccess": data["console"] if data["type"] == "user" else "",
        "MFAActive": data["mfa"] if data["type"] == "user" else "",
        "AccessKeys": "; ".join(data["keys"]),
        "TrustedBy": data["trusted_by"],
    }


def matrix_rows(all_perm_rows):
    subjects = []
    resources_by_key = {}
    denied_keys = set()
    for row in all_perm_rows:
        if row["Source"] == "TrustPolicy":
            continue
        subject = row["Subject"]
        if subject not in subjects:
            subjects.append(subject)
        key = (subject, row["Action"])
        if row["Effect"] == "Allow":
            resources_by_key.setdefault(key, set()).update(r for r in row["Resource"].split("; ") if r)
        elif row["Effect"] == "Deny":
            denied_keys.add(key)

    union = sorted({action for _, action in resources_by_key}, key=lambda a: (a.split(":")[0], a))
    rows = []
    for action in union:
        row = {"Permission": action}
        for s in subjects:
            key = (s, action)
            resources = resources_by_key.get(key)
            if not resources:
                row[s] = ""
            elif "*" in resources:
                row[s] = "X*"
            else:
                row[s] = "X"
            if row[s] and key in denied_keys:
                row[s] += "!"
        rows.append(row)
    return subjects, rows


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Extracts AWS IAM policy data for one or more users/roles into CSV.")
    parser.add_argument("--type", choices=["user", "role"], required=True)
    parser.add_argument("--name", action="append", required=True, dest="names",
                         help="Principal name or ARN. Repeat for multiple; more than one triggers a comparison matrix.")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--output", default=None, help="Base output file name (no extension)")
    parser.add_argument("--output-dir", default="export")
    args = parser.parse_args()

    names = [name_from_arn(n) for n in args.names]

    try:
        session = boto3.Session(profile_name=args.profile)
    except ProfileNotFound as e:
        sys.exit(f"AWS profile not found: {e}")
    iam = session.client("iam")

    datasets = []
    try:
        for name in names:
            datasets.append(collect(iam, args.type, name))
    except NoCredentialsError:
        sys.exit("No AWS credentials found. Configure a profile (--profile) or the AWS_* environment variables.")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code == "NoSuchEntity":
            sys.exit(f"No {args.type} named '{name}' found in the account.")
        sys.exit(f"AWS error ({code}): {e}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = args.output or f"iam_{args.type}"

    all_perm_rows = []
    profile_rows = []
    for data in datasets:
        all_perm_rows += permission_rows(data)
        profile_rows.append(profile_row(data))

    profile_path = out_dir / f"{base}_profile.csv"
    permissions_path = out_dir / f"{base}_permissions.csv"
    write_csv(profile_path, PROFILE_FIELDS, profile_rows)
    write_csv(permissions_path, PERM_FIELDS, all_perm_rows)
    written = [profile_path, permissions_path]

    if len(datasets) > 1:
        subjects, rows = matrix_rows(all_perm_rows)
        matrix_path = out_dir / f"{base}_matrix.csv"
        write_csv(matrix_path, ["Permission"] + subjects, rows)
        written.append(matrix_path)

    for path in written:
        print(f"Written: {path}")


if __name__ == "__main__":
    main()
