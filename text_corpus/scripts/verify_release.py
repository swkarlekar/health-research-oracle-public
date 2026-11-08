#!/usr/bin/env python3
"""Run integrity checks and audit table-cell offsets in this corpus snapshot."""
from datetime import datetime, timezone
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main():
    output = ROOT / "evaluation"
    output.mkdir(exist_ok=True)
    log = io.StringIO()
    suite = unittest.TestLoader().discover(str(ROOT / "tests"))
    with redirect_stdout(log), redirect_stderr(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    (output / "integrity_checks.log").write_text(log.getvalue(), encoding="utf-8")
    cell_count = table_count = 0
    errors = []
    with (ROOT / "processed/sections.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            section = json.loads(line)
            for table in section.get("tables", []):
                table_count += 1
                for row in table.get("row_spans", []):
                    for cell in row.get("cells", []):
                        cell_count += 1
                        start, end = cell.get("start_char"), cell.get("end_char")
                        valid = (isinstance(start, int) and isinstance(end, int)
                                 and 0 <= start <= end <= len(section["text"])
                                 and section["text"][start:end] == cell.get("text", ""))
                        if not valid:
                            errors.append({"section_id": section["section_id"], "start": start, "end": end})
    passed = result.wasSuccessful() and not errors
    report = {
        "status": "passed" if passed else "failed",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "corpus_file_sha256": sha256(ROOT / "processed/documents.jsonl"),
        "automated_tests_run": result.testsRun,
        "test_failures": len(result.failures), "test_errors": len(result.errors), "test_skipped": len(result.skipped),
        "actual_corpus_table_representations_audited": table_count,
        "actual_corpus_table_cells_audited": cell_count,
        "table_cell_offset_errors": len(errors), "table_cell_error_examples": errors[:20],
        "scope": "Mechanical extraction, quotation, citation, provenance, and harness integrity; not legal-answer quality or client benchmark performance.",
        "model_inference_performed": False, "client_benchmark_status": "awaiting_client_benchmark",
    }
    (output / "integrity_checks.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if not passed:
        print(log.getvalue(), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
