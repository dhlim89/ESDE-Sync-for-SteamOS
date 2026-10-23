#!/usr/bin/env python3
"""Strict event and result verifier for the isolated Mini V2 recovery harness."""
from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import re
import tempfile


class VerificationError(Exception):
    pass


def parse_events(path: Path) -> list[tuple[str, str, str, str]]:
    try:
        with path.open("r", encoding="utf-8", newline="") as stream:
            rows = list(csv.reader(stream, delimiter="\t", strict=True))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise VerificationError(f"event log missing or invalid: {exc}") from exc
    if not rows or any(len(row) != 4 or row[0] != "ESDE_EVENT_V1" for row in rows):
        raise VerificationError("event log empty, truncated, or malformed")
    return [tuple(row) for row in rows]  # type: ignore[return-value]


def expected_events(case: str, final_path: str, part_path: str) -> list[tuple[str, str, str, str]]:
    if case == "restore_fail":
        return [
            ("ESDE_EVENT_V1", "ACTION", "CLAIM", final_path),
            ("ESDE_EVENT_V1", "ACTION", "BACKUP", final_path),
            ("ESDE_EVENT_V1", "PUSH", "ATTEMPT", part_path),
            ("ESDE_EVENT_V1", "ACTION", "REPLACE", final_path),
            ("ESDE_EVENT_V1", "SETUP", "CORRUPT_AFTER_REPLACE", final_path),
            ("ESDE_EVENT_V1", "ACTION", "RESTORE_COPY", final_path),
            ("ESDE_EVENT_V1", "ACTION", "RESTORE", final_path),
            ("ESDE_EVENT_V1", "INJECT", "RESTORE_FAIL", final_path),
        ]
    if case == "push_fail":
        return [
            ("ESDE_EVENT_V1", "ACTION", "CLAIM", final_path),
            ("ESDE_EVENT_V1", "ACTION", "BACKUP", final_path),
            ("ESDE_EVENT_V1", "PUSH", "ATTEMPT", part_path),
            ("ESDE_EVENT_V1", "INJECT", "PUSH_FAIL", part_path),
        ]
    raise VerificationError(f"unsupported case: {case}")


def verify(
    events: list[tuple[str, str, str, str]],
    *,
    case: str,
    final_path: str,
    part_path: str,
    status: str,
    return_code: int,
    recovery_required: int,
    old_hash: str,
    new_hash: str,
    final_hash: str,
    backup_hash: str,
    part_hash: str,
    restore_hash: str,
    report: str,
) -> None:
    expected = expected_events(case, final_path, part_path)
    if events != expected:
        raise VerificationError(f"event sequence mismatch: expected {expected!r}, got {events!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", old_hash) or not re.fullmatch(r"[0-9a-f]{64}", new_hash) or old_hash == new_hash:
        raise VerificationError("fixture hashes are invalid or unexpectedly equal")
    if return_code != 1 or recovery_required != 1:
        raise VerificationError("expected one failed helper operation with recovery artifacts retained")
    if case == "restore_fail":
        corrupt_hash = hashlib.sha256(b"CORRUPT").hexdigest()
        if status != "RECOVERY_REQUIRED":
            raise VerificationError(f"wrong product status: {status}")
        if final_hash != corrupt_hash or final_hash in (old_hash, new_hash):
            raise VerificationError("final fixture does not match the deliberate corruption")
        if backup_hash != old_hash or restore_hash != old_hash:
            raise VerificationError("verified A backup/restore copy was not preserved")
        if part_hash != "ABSENT":
            raise VerificationError("unexpected .part artifact remains")
        for marker in ("RECOVERY_REQUIRED", "backup=", "restore="):
            if marker not in report:
                raise VerificationError(f"recovery report missing {marker}")
    else:
        if status != "FAILED_PRESERVED":
            raise VerificationError(f"wrong product status: {status}")
        if final_hash != old_hash or backup_hash != old_hash:
            raise VerificationError("old final/backup hash mismatch")
        if part_hash != "ABSENT" or restore_hash != "ABSENT":
            raise VerificationError("unexpected temporary artifact remains")
        if "FAILED_PRESERVED" not in report or "backup=" not in report:
            raise VerificationError("preservation report missing")
    if len([row for row in events if row[1:3] == ("INJECT", "RESTORE_FAIL" if case == "restore_fail" else "PUSH_FAIL")]) != 1:
        raise VerificationError("failure injection count is not exactly one")
    if case == "restore_fail" and len([row for row in events if row[1:3] == ("SETUP", "CORRUPT_AFTER_REPLACE")]) != 1:
        raise VerificationError("fixture corruption setup count is not exactly one")


def self_test() -> None:
    final = "/isolated/restore_fail/fixture.gb"
    part = "/isolated/restore_fail/.part"
    old_hash = hashlib.sha256(b"fixture-A").hexdigest()
    new_hash = hashlib.sha256(b"fixture-B").hexdigest()
    good = expected_events("restore_fail", final, part)
    baseline = dict(
        case="restore_fail", final_path=final, part_path=part,
        status="RECOVERY_REQUIRED", return_code=1, recovery_required=1,
        old_hash=old_hash, new_hash=new_hash,
        final_hash=hashlib.sha256(b"CORRUPT").hexdigest(),
        backup_hash=old_hash, part_hash="ABSENT", restore_hash=old_hash,
        report="RECOVERY_REQUIRED backup=/isolated/.backup restore=/isolated/.restore",
    )

    def accepted(events=good, **overrides):
        values = baseline | overrides
        try:
            verify(events, **values)
            return True
        except VerificationError:
            return False

    cases = {
        "valid": accepted(),
        "no_restore_injection": accepted(good[:-1]),
        "double_restore_injection": accepted(good + [good[-1]]),
        "no_fixture_damage": accepted(good[:4] + good[5:]),
        "double_fixture_damage": accepted(good[:5] + [good[4]] + good[5:]),
        "reverse_order": accepted(good[:5] + [good[6], good[5]] + good[7:]),
        "unexpected_write": accepted(good + [("ESDE_EVENT_V1", "UNEXPECTED", "ADB_WRITE", final)]),
        "missing_event": accepted([]),
        "success_status": accepted(status="SUCCESS"),
        "backup_hash_mismatch": accepted(backup_hash=new_hash),
        "restore_hash_mismatch": accepted(restore_hash=new_hash),
    }
    if cases != {"valid": True, **{name: False for name in cases if name != "valid"}}:
        raise VerificationError(f"self-test failed: {cases!r}")
    with tempfile.TemporaryDirectory(prefix="esde-event-log-") as tmp:
        log = Path(tmp) / "events.tsv"
        with log.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
            writer.writerows(good)
        verify(parse_events(log), **baseline)
        log.write_text("", encoding="utf-8")
        try:
            parse_events(log)
        except VerificationError:
            pass
        else:
            raise VerificationError("empty event log accepted")
        log.write_text("ESDE_EVENT_V1\tACTION\tRESTORE", encoding="utf-8")
        try:
            parse_events(log)
        except VerificationError:
            pass
        else:
            raise VerificationError("truncated event log accepted")
        try:
            parse_events(Path(tmp) / "missing.tsv")
        except VerificationError:
            pass
        else:
            raise VerificationError("missing event log accepted")
    print("EVENT_VERIFIER SELF_TEST PASS: valid, missing, duplicate, reversed, unexpected, status, hashes")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--events", type=Path)
    parser.add_argument("--case", choices=("push_fail", "restore_fail"))
    parser.add_argument("--final-path")
    parser.add_argument("--part-path")
    parser.add_argument("--status")
    parser.add_argument("--return-code", type=int)
    parser.add_argument("--recovery-required", type=int)
    parser.add_argument("--old-hash")
    parser.add_argument("--new-hash")
    parser.add_argument("--final-hash")
    parser.add_argument("--backup-hash")
    parser.add_argument("--part-hash")
    parser.add_argument("--restore-hash")
    parser.add_argument("--report", default="")
    args = parser.parse_args()
    try:
        if args.self_test:
            self_test()
        else:
            required = (args.events, args.case, args.final_path, args.part_path, args.status,
                        args.return_code, args.recovery_required, args.old_hash, args.new_hash,
                        args.final_hash, args.backup_hash, args.part_hash, args.restore_hash)
            if any(value is None for value in required):
                raise VerificationError("missing verifier argument")
            verify(parse_events(args.events), case=args.case, final_path=args.final_path,
                   part_path=args.part_path, status=args.status, return_code=args.return_code,
                   recovery_required=args.recovery_required, old_hash=args.old_hash,
                   new_hash=args.new_hash, final_hash=args.final_hash, backup_hash=args.backup_hash,
                   part_hash=args.part_hash, restore_hash=args.restore_hash, report=args.report)
            print(f"EVENT_VERIFIER PASS: {args.case} status={args.status} injection=1")
    except VerificationError as exc:
        print(f"EVENT_VERIFIER FAIL: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
