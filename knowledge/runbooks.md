# EC2 instance status check failed

An instance is reachable in `describe-instances` but `describe-instance-status`
reports `impaired` or `insufficient-data`.

1. Confirm which check failed: `system` (AWS host/network) or `instance`
   (the guest OS).
2. A failed **system** check is AWS-side. Recovery is a stop/start, which moves
   the instance to a new host. This agent cannot do that — escalate to whoever
   holds write access.
3. A failed **instance** check is guest-side: full disk, kernel panic, a network
   config change. Check CloudWatch logs and the console screenshot.
4. Note the instance id and AZ in memory so the next report can flag repeats.

# High CloudWatch alarm volume

More than five alarms in `ALARM` at once usually means one upstream cause, not
five incidents.

1. Sort alarms by `StateUpdatedTimestamp` — the earliest one is normally the
   trigger.
2. Group by dimension: many alarms on the same ASG, target group or RDS
   instance point at one resource.
3. Alarms in `INSUFFICIENT_DATA` for more than an hour usually mean the metric
   stopped being published — the resource may be gone, not unhealthy.

# RDS storage running low

`FreeStorageSpace` below 10% of allocated storage.

1. Check whether storage autoscaling is enabled
   (`describe-db-instances` → `MaxAllocatedStorage`).
2. Look for a runaway table or unrotated logs before adding storage.
3. Storage scale-up is a write operation — this agent reports, a human acts.

# Cost spike triage

A day-over-day increase above 20% in `ce get-cost-and-usage`.

1. Break the spike down by `SERVICE` first, then by `USAGE_TYPE` inside the
   top service.
2. Data transfer and NAT gateway processing are the usual surprises.
3. Compare against the resource inventory: a jump in EC2 cost with a flat
   instance count means a size change or a new region.

# Security posture review

Run `make security-scan` weekly.

1. Security groups open to `0.0.0.0/0` on ports other than 80/443 are findings.
2. S3 buckets without a public access block are findings.
3. IAM users with console access but no MFA are findings.
4. Access keys older than 90 days should be rotated.
