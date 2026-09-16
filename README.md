# 🧰 AWS Utility Tools

Collection of standalone, read-only AWS utility scripts. Each tool lives
in its own subfolder with its own virtualenv, dependencies, and README.

---

## 🛠️ Tools

| Folder | What it does |
|---|---|
| [`iam-report`](iam-report/README.md) | Extracts AWS IAM policy data for one or more users/roles into CSV, with an optional permission-comparison matrix. |
| [`idle-resources-finder`](idle-resources-finder/README.md) | Scans one or more regions (or all enabled regions) for idle/unused EC2, EBS, Elastic IP, load balancer, snapshot, and RDS resources, and writes the evidence to CSV. |
