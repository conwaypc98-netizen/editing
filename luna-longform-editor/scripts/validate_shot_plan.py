#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

from production_evidence import media_identity, read_json, validate_shot_plan, write_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate an immutable autonomous Luna shot specification."
    )
    parser.add_argument("--shot-plan", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()

    plan_path = Path(args.shot_plan).expanduser().resolve()
    project_path = Path(args.project).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    report = {
        "schema_version": 1,
        "shot_plan": str(plan_path),
        "project": str(project_path),
        "project_identity": media_identity(project_path),
        **validate_shot_plan(read_json(plan_path), read_json(project_path)),
    }
    write_json(report_path, report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
