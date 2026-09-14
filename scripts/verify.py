"""Run documented checks and record actual command exits. Never invokes paid APIs."""

import argparse
import json
import os
import platform
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--clean", action="store_true", help="Use a separate .venv-clean environment"
    )
    args = parser.parse_args()
    workspace = Path(__file__).resolve().parent.parent
    environment = os.environ.copy()
    if args.clean:
        environment["UV_PROJECT_ENVIRONMENT"] = str(workspace / ".venv-clean")
    environment["PYTHONIOENCODING"] = "utf-8"
    prefix = "clean_" if args.clean else ""
    artifacts = workspace / "artifacts"
    artifacts.mkdir(exist_ok=True)
    checks = [
        ("sync", ["uv", "sync", "--locked", "--extra", "dev"]),
        (
            "chromium",
            ["uv", "run", "--extra", "dev", "python", "-m", "playwright", "install", "chromium"],
        ),
        ("lint", ["uv", "run", "--extra", "dev", "ruff", "check", "."]),
        ("format", ["uv", "run", "--extra", "dev", "ruff", "format", "--check", "."]),
        ("types", ["uv", "run", "--extra", "dev", "mypy", "src/lead_enricher"]),
        ("tests", ["uv", "run", "--extra", "dev", "pytest", "-q"]),
        ("doctor", ["uv", "run", "python", "-m", "lead_enricher", "doctor"]),
        (
            "demo",
            [
                "uv",
                "run",
                "python",
                "-m",
                "lead_enricher",
                "demo",
                "--output",
                f"artifacts/{prefix}demo_output.json",
            ],
        ),
        (
            "validate",
            [
                "uv",
                "run",
                "python",
                "-m",
                "lead_enricher",
                "validate-output",
                f"artifacts/{prefix}demo_output.json",
            ],
        ),
    ]
    records = []
    if args.clean:
        checks[0][1].append("--no-editable")
        for _, command in checks[1:]:
            command.insert(2, "--no-sync")
    for name, command in checks:
        started = datetime.now(UTC).isoformat()
        result = subprocess.run(
            command,
            cwd=workspace,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        log = artifacts / f"{prefix}{name}.log"
        log.write_text(result.stdout + result.stderr, encoding="utf-8")
        records.append(
            {
                "name": name,
                "command": command,
                "exit_code": result.returncode,
                "started_at": started,
                "finished_at": datetime.now(UTC).isoformat(),
                "log": str(log.relative_to(workspace)),
            }
        )
        print(f"{name}: exit {result.returncode}", flush=True)
    report = {
        "environment": ".venv-clean" if args.clean else ".venv",
        "platform": platform.platform(),
        "checks": records,
    }
    (artifacts / f"{prefix}verification.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return int(any(record["exit_code"] != 0 for record in records))


if __name__ == "__main__":
    raise SystemExit(main())
