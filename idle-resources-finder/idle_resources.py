import argparse
import csv
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError, NoCredentialsError, ProfileNotFound

EC2_FIELDS = ["Region", "InstanceId", "Name", "State", "StoppedSince", "IdleDays", "ConsoleLink"]
EBS_FIELDS = ["Region", "VolumeId", "Name", "SizeGiB", "VolumeType", "CreatedOn", "IdleDays", "ConsoleLink"]
EIP_FIELDS = ["Region", "AllocationId", "PublicIp", "Name", "ConsoleLink"]
ELB_FIELDS = ["Region", "Type", "LoadBalancerName", "Name", "RegisteredTargets", "HealthyTargets", "ConsoleLink"]
SNAP_FIELDS = ["Region", "SnapshotId", "Name", "SourceVolumeId", "CreatedOn", "IdleDays", "SizeGiB", "ConsoleLink"]
RDS_FIELDS = ["Region", "DBInstanceIdentifier", "Name", "Engine", "Status", "ConsoleLink"]

CATEGORIES = [
    ("ec2", "EC2 stopped instances", EC2_FIELDS),
    ("ebs", "Unattached EBS volumes", EBS_FIELDS),
    ("eips", "Unassociated Elastic IPs", EIP_FIELDS),
    ("elb", "Load balancers without healthy targets", ELB_FIELDS),
    ("snapshots", "Orphaned EBS snapshots", SNAP_FIELDS),
    ("rds", "Stopped RDS instances", RDS_FIELDS),
]


def warn(msg):
    print(f"[WARN] {msg}", file=sys.stderr)


def now_utc():
    return datetime.now(timezone.utc)


def days_since(dt):
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (now_utc() - dt).days


def tag_name(tags):
    for t in tags or []:
        if t.get("Key") == "Name":
            return t.get("Value", "")
    return ""


def parse_stopped_since(reason):
    # reason format: "User initiated (2024-01-02 03:04:05 GMT)"
    m = re.search(r"\(([^)]+)\)", reason or "")
    if not m:
        return None
    try:
        dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S %Z")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def collect_ec2(ec2, region, min_days, rows):
    try:
        paginator = ec2.get_paginator("describe_instances")
        for page in paginator.paginate(Filters=[{"Name": "instance-state-name", "Values": ["stopped"]}]):
            for res in page["Reservations"]:
                for inst in res["Instances"]:
                    stopped_since = parse_stopped_since(inst.get("StateTransitionReason", ""))
                    idle_days = days_since(stopped_since)
                    if idle_days == "" or idle_days < min_days:
                        continue
                    iid = inst["InstanceId"]
                    rows.append({
                        "Region": region, "InstanceId": iid, "Name": tag_name(inst.get("Tags")),
                        "State": inst["State"]["Name"],
                        "StoppedSince": stopped_since.isoformat() if stopped_since else "",
                        "IdleDays": idle_days,
                        "ConsoleLink": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#InstanceDetails:instanceId={iid}",
                    })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: EC2 describe_instances failed - {e}")


def collect_ebs(ec2, region, rows):
    try:
        paginator = ec2.get_paginator("describe_volumes")
        for page in paginator.paginate(Filters=[{"Name": "status", "Values": ["available"]}]):
            for vol in page["Volumes"]:
                created = vol.get("CreateTime")
                vid = vol["VolumeId"]
                rows.append({
                    "Region": region, "VolumeId": vid, "Name": tag_name(vol.get("Tags")),
                    "SizeGiB": vol.get("Size", ""), "VolumeType": vol.get("VolumeType", ""),
                    "CreatedOn": created.isoformat() if created else "",
                    "IdleDays": days_since(created),
                    "ConsoleLink": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#VolumeDetails:volumeId={vid}",
                })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: EC2 describe_volumes failed - {e}")


def collect_eip(ec2, region, rows):
    try:
        resp = ec2.describe_addresses()
        for addr in resp.get("Addresses", []):
            if addr.get("AssociationId") or addr.get("InstanceId") or addr.get("NetworkInterfaceId"):
                continue
            ip = addr.get("PublicIp", "")
            rows.append({
                "Region": region, "AllocationId": addr.get("AllocationId", ""), "PublicIp": ip,
                "Name": tag_name(addr.get("Tags")),
                "ConsoleLink": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#Addresses:search={ip}",
            })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: EC2 describe_addresses failed - {e}")


def collect_elbv2(elbv2, region, rows):
    try:
        paginator = elbv2.get_paginator("describe_load_balancers")
        for page in paginator.paginate():
            for lb in page["LoadBalancers"]:
                arn, name, lb_type = lb["LoadBalancerArn"], lb["LoadBalancerName"], lb["Type"]
                lb_name_tag = ""
                try:
                    tag_resp = elbv2.describe_tags(ResourceArns=[arn])
                    for td in tag_resp.get("TagDescriptions", []):
                        lb_name_tag = tag_name(td.get("Tags"))
                except (ClientError, BotoCoreError, EndpointConnectionError) as e:
                    warn(f"{region}: elbv2 describe_tags({name}) failed - {e}")

                total, healthy = 0, 0
                try:
                    tg_resp = elbv2.describe_target_groups(LoadBalancerArn=arn)
                    for tg in tg_resp["TargetGroups"]:
                        health = elbv2.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                        for t in health["TargetHealthDescriptions"]:
                            total += 1
                            if t["TargetHealth"]["State"] == "healthy":
                                healthy += 1
                except (ClientError, BotoCoreError, EndpointConnectionError) as e:
                    warn(f"{region}: elbv2 target health for {name} failed - {e}")
                    continue

                if total == 0 or healthy == 0:
                    rows.append({
                        "Region": region, "Type": lb_type.upper(), "LoadBalancerName": name, "Name": lb_name_tag,
                        "RegisteredTargets": total, "HealthyTargets": healthy,
                        "ConsoleLink": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#LoadBalancers:search={name}",
                    })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: elbv2 describe_load_balancers failed - {e}")


def collect_elb_classic(elb, region, rows):
    try:
        paginator = elb.get_paginator("describe_load_balancers")
        for page in paginator.paginate():
            for lb in page["LoadBalancerDescriptions"]:
                name = lb["LoadBalancerName"]
                instances = lb.get("Instances", [])
                total, healthy = len(instances), 0
                if total:
                    try:
                        health = elb.describe_instance_health(LoadBalancerName=name)
                        healthy = sum(1 for s in health["InstanceStates"] if s["State"] == "InService")
                    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
                        warn(f"{region}: elb describe_instance_health({name}) failed - {e}")
                        continue
                if total == 0 or healthy == 0:
                    rows.append({
                        "Region": region, "Type": "CLASSIC", "LoadBalancerName": name, "Name": "",
                        "RegisteredTargets": total, "HealthyTargets": healthy,
                        "ConsoleLink": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#LoadBalancers:search={name}",
                    })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: elb describe_load_balancers failed - {e}")


def collect_snapshots(ec2, region, rows):
    volume_ids = set()
    try:
        paginator = ec2.get_paginator("describe_volumes")
        for page in paginator.paginate():
            for v in page["Volumes"]:
                volume_ids.add(v["VolumeId"])
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: EC2 describe_volumes (for snapshot check) failed - {e}")
        return

    try:
        paginator = ec2.get_paginator("describe_snapshots")
        for page in paginator.paginate(OwnerIds=["self"]):
            for snap in page["Snapshots"]:
                vol_id = snap.get("VolumeId", "")
                if vol_id and vol_id in volume_ids:
                    continue
                created = snap.get("StartTime")
                sid = snap["SnapshotId"]
                rows.append({
                    "Region": region, "SnapshotId": sid, "Name": tag_name(snap.get("Tags")),
                    "SourceVolumeId": vol_id, "CreatedOn": created.isoformat() if created else "",
                    "IdleDays": days_since(created), "SizeGiB": snap.get("VolumeSize", ""),
                    "ConsoleLink": f"https://{region}.console.aws.amazon.com/ec2/home?region={region}#SnapshotDetails:snapshotId={sid}",
                })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: EC2 describe_snapshots failed - {e}")


def collect_rds(rds, region, rows):
    try:
        paginator = rds.get_paginator("describe_db_instances")
        for page in paginator.paginate():
            for db in page["DBInstances"]:
                if db.get("DBInstanceStatus") != "stopped":
                    continue
                did = db["DBInstanceIdentifier"]
                rows.append({
                    "Region": region, "DBInstanceIdentifier": did, "Name": did,
                    "Engine": db.get("Engine", ""), "Status": db["DBInstanceStatus"],
                    "ConsoleLink": f"https://{region}.console.aws.amazon.com/rds/home?region={region}#database:id={did};is-cluster=false",
                })
    except (ClientError, BotoCoreError, EndpointConnectionError) as e:
        warn(f"{region}: rds describe_db_instances failed - {e}")


def resolve_regions(session, args):
    if args.all_regions:
        bootstrap_region = session.region_name or "us-east-1"
        ec2 = session.client("ec2", region_name=bootstrap_region)
        try:
            resp = ec2.describe_regions(AllRegions=False)
        except (ClientError, BotoCoreError, EndpointConnectionError) as e:
            sys.exit(f"Unable to list regions via describe_regions: {e}")
        return sorted(r["RegionName"] for r in resp["Regions"])
    if args.regions:
        return args.regions
    if session.region_name:
        return [session.region_name]
    sys.exit("No region specified. Use --region (repeatable), --all-regions, or configure a default region.")


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(all_rows):
    print("\nIdle resources summary")
    print("-----------------------")
    total = 0
    for key, label, _ in CATEGORIES:
        rows = all_rows[key]
        total += len(rows)
        print(f"{label:<42} {len(rows):>5}")
    print("-----------------------")
    print(f"{'TOTAL':<42} {total:>5}")

    by_region = {}
    for key, _, _ in CATEGORIES:
        for row in all_rows[key]:
            by_region[row["Region"]] = by_region.get(row["Region"], 0) + 1
    if by_region:
        print("\nBy region")
        print("---------")
        for region in sorted(by_region):
            print(f"{region:<20} {by_region[region]:>5}")


def main():
    parser = argparse.ArgumentParser(description="Scans AWS regions for idle/unused resources (stopped EC2, unattached EBS, unused EIPs, unhealthy load balancers, orphaned snapshots, stopped RDS) and writes CSV reports. Read-only.")
    parser.add_argument("--region", action="append", dest="regions", help="Region to scan. Repeatable. Ignored if --all-regions is set.")
    parser.add_argument("--all-regions", action="store_true", help="Scan every region enabled on the account (via describe_regions).")
    parser.add_argument("--profile", default=None)
    parser.add_argument("--ec2-stopped-days", type=int, default=14, help="Minimum days an EC2 instance must have been stopped to be reported (default: 14).")
    parser.add_argument("--output", default="idle", help="Base file name prefix for the CSV reports (default: idle).")
    parser.add_argument("--output-dir", default="export")
    args = parser.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile)
    except ProfileNotFound as e:
        sys.exit(f"AWS profile not found: {e}")

    try:
        regions = resolve_regions(session, args)
    except NoCredentialsError:
        sys.exit("No AWS credentials found. Configure a profile (--profile) or the AWS_* environment variables.")

    all_rows = {key: [] for key, _, _ in CATEGORIES}

    for region in regions:
        print(f"Scanning {region}...", file=sys.stderr)
        try:
            ec2 = session.client("ec2", region_name=region)
            elbv2 = session.client("elbv2", region_name=region)
            elb = session.client("elb", region_name=region)
            rds = session.client("rds", region_name=region)
        except (ClientError, BotoCoreError, EndpointConnectionError) as e:
            warn(f"{region}: could not create clients, skipping region - {e}")
            continue

        collect_ec2(ec2, region, args.ec2_stopped_days, all_rows["ec2"])
        collect_ebs(ec2, region, all_rows["ebs"])
        collect_eip(ec2, region, all_rows["eips"])
        collect_elbv2(elbv2, region, all_rows["elb"])
        collect_elb_classic(elb, region, all_rows["elb"])
        collect_snapshots(ec2, region, all_rows["snapshots"])
        collect_rds(rds, region, all_rows["rds"])

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    written = []
    for key, _, fields in CATEGORIES:
        path = out_dir / f"{args.output}_{key}.csv"
        write_csv(path, fields, all_rows[key])
        written.append(path)

    for path in written:
        print(f"Written: {path}")

    print_summary(all_rows)


if __name__ == "__main__":
    main()
