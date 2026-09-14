import json
import os
import tempfile
from pathlib import Path

from lead_enricher.models import RunOutput
from lead_enricher.validation import audit_output


class PersistenceError(Exception):
    pass


def ensure_destination(path: Path, mode: str) -> None:
    if mode == "demo" and path.name.casefold() == "output.json":
        raise PersistenceError("Demo must not write output.json; use artifacts/demo_output.json")
    if mode == "demo" and path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("mode") == "live":
                raise PersistenceError("Demo cannot overwrite a live output file")
        except (ValueError, UnicodeError):
            pass


def save_output(run: RunOutput, path: Path) -> None:
    ensure_destination(path, run.mode)
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        checked = RunOutput.model_validate(run.model_dump(mode="json"))
        audit_output(checked)
        payload = json.dumps(
            checked.model_dump(mode="json"), ensure_ascii=False, indent=2, allow_nan=False
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="." + path.name + ".",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary = Path(file.name)
            file.write(payload + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except (OSError, ValueError) as exc:
        raise PersistenceError(
            "Cannot validate/write output atomically; check destination permissions and schema"
        ) from exc
    finally:
        if temporary and temporary.exists():
            temporary.unlink(missing_ok=True)


def load_output(path: Path) -> RunOutput:
    run = RunOutput.model_validate_json(path.read_text(encoding="utf-8"))
    audit_output(run)
    return run
