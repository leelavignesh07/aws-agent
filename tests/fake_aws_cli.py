#!/usr/bin/env python3
"""Stand-in for the AWS CLI. Records the argv it was given and replies with
canned JSON, so tests can assert on the command the agent built."""

import json
import os
import sys

argv = sys.argv[1:]
log = os.environ.get("FAKE_AWS_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(argv) + "\n")

if argv and argv[0] == "--version":
    print("aws-cli/2.15.0 Python/3.11.8 Linux/x86_64")
    sys.exit(0)

if os.environ.get("FAKE_AWS_FAIL"):
    sys.stderr.write("An error occurred (AccessDenied) when calling the operation\n")
    sys.exit(254)

positional = [a for a in argv if not a.startswith("--")]
service = positional[0] if positional else ""
operation = positional[1] if len(positional) > 1 else ""

canned = {
    ("sts", "get-caller-identity"): {
        "Account": "123456789012",
        "Arn": "arn:aws:iam::123456789012:user/monitor",
        "UserId": "AIDAEXAMPLE",
    },
    ("ec2", "describe-instances"): {
        "Reservations": [
            {
                "Instances": [
                    {"InstanceId": "i-aaa", "State": {"Name": "running"}},
                    {"InstanceId": "i-bbb", "State": {"Name": "stopped"}},
                ]
            }
        ]
    },
    ("ec2", "describe-volumes"): {"Volumes": [{"VolumeId": "vol-1", "Encrypted": False}]},
    ("s3api", "list-buckets"): {"Buckets": [{"Name": "bucket-one"}, {"Name": "bucket-two"}]},
    ("cloudwatch", "describe-alarms"): {
        "MetricAlarms": [
            {
                "AlarmName": "cpu-high",
                "StateValue": "ALARM",
                "MetricName": "CPUUtilization",
                "StateReason": "threshold crossed",
            }
        ]
    },
    ("rds", "describe-db-instances"): {
        "DBInstances": [
            {
                "DBInstanceIdentifier": "crm-prod",
                "DBInstanceStatus": "available",
                "PubliclyAccessible": True,
                "StorageEncrypted": True,
                "Engine": "postgres",
            }
        ]
    },
    ("ec2", "describe-security-groups"): {
        "SecurityGroups": [
            {
                "GroupId": "sg-1",
                "GroupName": "open-ssh",
                "IpPermissions": [
                    {"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}
                ],
            }
        ]
    },
}
print(json.dumps(canned.get((service, operation), {})))
