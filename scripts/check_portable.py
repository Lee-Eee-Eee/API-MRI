"""Exercise the shipped Windows executables without importing project code.

Run from any Python with only its standard library available::

    python scripts/check_portable.py --bundle "API-MRI-0.7.0-windows-x64" --out "new check directory"

All model-facing commands use synthetic fixtures or replay saved records. The
GUI executable is started with its built-in smoke flag and must actually exit.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Any, Callable
import zipfile


class CheckFailed(RuntimeError):
    pass


def require(condition: Any, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def load_json(path: Path) -> dict:
    require(path.is_file() and path.stat().st_size > 0, f"Missing JSON: {path.name}")
    result = json.loads(path.read_text(encoding="utf-8"))
    require(isinstance(result, dict), f"Expected JSON object: {path.name}")
    return result


def scrub(text: str | bytes | None, limit: int = 2000) -> str:
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    text = text or ""
    text = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{6,}", "[redacted]", text)
    text = re.sub(r'(?i)((?:api[_-]?key|authorization|session_token|token)[\s\"\']*[:=][\s\"\']*)[^\s,\"\'}]+', r"\1[redacted]", text)
    return text[-limit:]


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def clean_environment(out: Path) -> dict[str, str]:
    # A whitelist also excludes inherited model keys, proxies, editable package
    # paths, virtualenv settings and user-site Python configuration.
    system_root = os.environ.get("SystemRoot", os.environ.get("WINDIR", r"C:\Windows"))
    env = {key: value for key, value in os.environ.items() if key.upper() in {
        "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
        "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "OS", "PROGRAMDATA",
    }}
    env["SystemRoot"] = system_root
    env["WINDIR"] = system_root
    env["PATH"] = os.pathsep.join([str(Path(system_root) / "System32"), system_root])
    profile = out / "isolated profile"
    for name, directory in {
        "USERPROFILE": profile,
        "APPDATA": profile / "AppData" / "Roaming",
        "LOCALAPPDATA": profile / "AppData" / "Local",
        "TEMP": out / "temporary files",
        "TMP": out / "temporary files",
        "HF_HOME": out / "offline cache",
    }.items():
        directory.mkdir(parents=True, exist_ok=True)
        env[name] = str(directory)
    env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    return env


def report_files(directory: Path, *, csv: bool = False) -> dict:
    report = load_json(directory / "report.json")
    for name in ["report.md"] + (["prompts.csv"] if csv else []):
        path = directory / name
        require(path.is_file() and path.stat().st_size > 0, f"Missing report artifact: {name}")
    return report


def comparable(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: comparable(item) for key, item in value.items()
                if key not in {"created_at", "generated_at", "run_id", "demo"}}
    if isinstance(value, list):
        return [comparable(item) for item in value]
    return value


class Checker:
    def __init__(self, bundle: Path, out: Path):
        self.bundle = bundle
        self.out = out
        self.cli = bundle / "api-mri-cli.exe"
        self.gui = bundle / "API-MRI.exe"
        self.env = clean_environment(out)
        self.library = out / "empty reference library"
        self.library.mkdir()
        self.results: dict = {"schema": 1, "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
                              "bundle": str(bundle), "real_model_calls": 0,
                              "environment": {"project_imports": False, "python_path_inherited": False,
                                              "api_keys_inherited": False, "user_profile_isolated": True,
                                              "path": self.env["PATH"]}, "checks": []}

    def save(self) -> None:
        temporary = self.out / "portable-check.json.tmp"
        temporary.write_text(json.dumps(self.results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.out / "portable-check.json")

    def check(self, name: str, function: Callable[[], dict | None]) -> Any:
        print(f"Checking {name}...", flush=True)
        began = time.perf_counter()
        row: dict = {"name": name, "status": "running"}
        self.results["checks"].append(row)
        self.save()
        try:
            details = function() or {}
            row.update(status="passed", details=details)
            return details
        except Exception as exc:
            row.update(status="failed", error=scrub(str(exc)))
            raise
        finally:
            row["seconds"] = round(time.perf_counter() - began, 3)
            self.save()
            print(f"{name}: {row['status']} ({row['seconds']:.2f}s)", flush=True)

    def command(self, executable: Path, *args: str, timeout: float = 300) -> str:
        try:
            result = subprocess.run([str(executable), *map(str, args)], cwd=self.out, env=self.env,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    encoding="utf-8", errors="replace", timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise CheckFailed(f"{executable.name} timed out after {timeout:g}s. {scrub(exc.stderr)}") from None
        if result.returncode != 0:
            raise CheckFailed(f"{executable.name} {args[0] if args else ''} exited {result.returncode}. "
                              f"stderr: {scrub(result.stderr)}")
        return result.stdout

    def cli_command(self, *args: str, timeout: float = 300) -> str:
        return self.command(self.cli, *args, timeout=timeout)

    def default_data_files(self) -> list[str]:
        data = self.bundle / "data"
        if not data.exists():
            return []
        files = sorted(str(path.relative_to(data)) for path in data.rglob("*") if path.is_file())
        require(all(Path(name).name.lower() in {"readme.txt", "readme.md", ".gitkeep"} for name in files),
                "Bundle contains user data or previous sessions in its default data directory")
        return files

    def metadata(self) -> dict:
        for executable in (self.cli, self.gui):
            require(executable.is_file(), f"Missing executable: {executable.name}")
            with executable.open("rb") as stream:
                require(stream.read(2) == b"MZ", f"Not a Windows executable: {executable.name}")
        require((self.bundle / "_internal").is_dir(), "Missing shared _internal runtime")
        require((self.bundle / "licenses").is_dir(), "Missing dependency licenses")
        manifest = load_json(self.bundle / "build-manifest.json")
        require(manifest.get("user_data_included") is False, "Build manifest must exclude user data")
        self.default_data_files()
        self.version = manifest["version"]
        return {"version": self.version, "executables": {
            path.name: {"bytes": path.stat().st_size, "sha256": digest(path)} for path in (self.cli, self.gui)},
            "default_data_has_user_files": False}

    def references(self) -> dict:
        result = json.loads(self.cli_command("references", "--library-dir", str(self.library)))
        require(isinstance(result, list) and len(result) >= 1, "Built-in reference list is empty")
        require(all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in result), "Invalid reference entries")
        self.reference_id = result[0]["id"]
        return {"reference_packs": len(result), "selected_pack": self.reference_id}

    def service_demo(self) -> dict:
        directory = self.out / "service demo"
        self.cli_command("service-demo", "--out", str(directory))
        report = report_files(directory)
        require(report.get("kind") == "service_quality" and report.get("synthetic") is True,
                "Service demo did not produce a synthetic service report")
        require(report.get("status") == "completed", "Synthetic service did not finish")
        require(report["cost"]["request_count"] == 0, "Service demo reported real requests")
        quality = report["service_quality"]
        require(quality["stream"]["successful_samples"] >= 1, "No stream samples in demo")
        require(all(row["status"] == "passed" for row in quality["tools"].values()), "Synthetic tool checks did not pass")
        require((directory / "service_records.jsonl").stat().st_size > 0, "No service evidence")
        return {"stream_samples": quality["stream"]["successful_samples"], "tool_checks": len(quality["tools"])}

    def service_replay(self) -> dict:
        source, directory = self.out / "service demo", self.out / "service replay"
        self.cli_command("service-replay", "--records", str(source / "service_records.jsonl"), "--out", str(directory))
        require(comparable(report_files(source)) == comparable(report_files(directory)), "Service replay changed the report")
        return {"report_equal": True}

    def identify_demo(self) -> dict:
        directory = self.out / "identify demo"
        self.cli_command("identify-demo", "--out", str(directory))
        report = report_files(directory)
        require(report.get("kind") == "identification" and report.get("synthetic") is True,
                "Identification demo did not produce synthetic candidate evidence")
        require(report["coverage"]["compared_candidates"] >= 100 and len(report["ranking"]) >= 1,
                "Identification bank or ranking is incomplete")
        require(report.get("identity_decision") == "abstained" and report.get("probability") is None,
                "Identification report lost its identity abstention")
        require(report["cost"]["real_requests"] == 0, "Identification demo reported real requests")
        return {"compared_candidates": report["coverage"]["compared_candidates"],
                "completed_cells": report["coverage"]["completed_cells"]}

    def identify_replay(self) -> dict:
        source, directory = self.out / "identify demo", self.out / "identify replay"
        self.cli_command("identify-replay", "--records", str(source / "identification_records.jsonl"), "--out", str(directory))
        require(comparable(report_files(source)) == comparable(report_files(directory)), "Identification replay changed the ranking or evidence")
        return {"report_equal_without_demo_label": True}

    def fingerprint(self, profile: str) -> dict:
        directory = self.out / f"fingerprint {profile}"
        self.cli_command("fingerprint-demo", "--reference-pack", self.reference_id, "--variant", "match",
                         "--profile", profile, "--library-dir", str(self.library), "--out", str(directory))
        report = report_files(directory, csv=True)
        require(report.get("synthetic") is True and report.get("profile") == profile, "Wrong fingerprint demo profile")
        summary = report["fingerprint"]["summary"]
        require(summary["answered"] == summary["total"] and summary["mismatches"] == 0, "Fingerprint parser lost matching answers")
        require(report["collection"]["real_requests"] == 0, "Fingerprint demo reported real requests")
        if profile == "full":
            require(isinstance(summary.get("p_value"), (int, float)), "SciPy/KBF statistical calculation did not execute")
        else:
            require(report["fingerprint"]["screening"]["outcome"] == "no_differences_observed", "Screening outcome changed")
        replay = self.out / f"fingerprint {profile} replay"
        self.cli_command("fingerprint-replay", "--reference-pack", self.reference_id,
                         "--records", str(directory / "fingerprint_records.jsonl"),
                         "--library-dir", str(self.library), "--out", str(replay))
        require(comparable(report) == comparable(report_files(replay, csv=True)), "Fingerprint replay changed results")
        return {"profile": profile, "answered": summary["answered"], "replay_equal": True,
                "statistical_calculation": profile == "full"}

    def inspect_met(self) -> dict:
        directory = self.out / "Inspect MET demo"
        self.cli_command("demo", "--out", str(directory), "--repeats", "8", "--permutations", "19", timeout=600)
        summary = load_json(directory / "demo_summary.json")
        require(summary.get("synthetic") is True and summary.get("real_model_calls") == 0, "Inspect demo was not synthetic")
        counts = {}
        versions = {}
        for role in ("honest", "substituted"):
            report = report_files(directory / role, csv=True)
            require(report.get("synthetic") is True, f"MET {role} report was not synthetic")
            require(report["method"]["engine"] == "model-equality-testing" and report["method"]["engine_version"], "MET engine metadata missing")
            require(report["settings"]["permutations"] == 19, "MET permutation setting changed")
            require(report["coverage"]["tested_prompts"] >= 1 and report["coverage"]["untested_prompts"] == 0,
                    f"Torch/MET calculations failed in {role}")
            require(all(row["tested"] and isinstance(row["raw_p"], (int, float)) for row in report["prompt_results"]), "MET did not calculate prompt p-values")
            versions = report["runtime_versions"]
            require(all(versions.get(name) not in {None, "not installed"} for name in
                        ("api-mri", "inspect-ai", "model-equality-testing", "torch", "numpy")), "Bundled dependency metadata is missing")
            counts[role] = report["coverage"]["tested_prompts"]
        logs = list((directory / "inspect").rglob("*.eval"))
        require(len(logs) >= 3 and all(path.stat().st_size > 0 for path in logs), "Inspect evaluation logs missing")
        compression_methods: set[int] = set()
        crc_checked = 0
        for path in logs:
            if zipfile.is_zipfile(path):
                with zipfile.ZipFile(path) as archive:
                    entries = archive.infolist()
                    require(entries and sum(entry.file_size for entry in entries) > 0, "Inspect archive directory is empty")
                    methods = {entry.compress_type for entry in entries}
                    compression_methods.update(methods)
                    # Inspect currently writes Zstandard ZIP entries (method
                    # 93). Python 3.12's standard library can inspect their
                    # directory, but cannot inflate them. The demo itself uses
                    # Inspect's native reader before it exports model records.
                    supported = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}
                    if methods <= supported:
                        require(archive.testzip() is None, "Inspect evaluation archive failed CRC")
                        crc_checked += 1
        return {"tested_prompts": counts, "inspect_eval_logs": len(logs), "runtime_versions": versions,
                "inspect_eval_compression_methods": sorted(compression_methods),
                "stdlib_crc_checked_logs": crc_checked, "native_inspect_reader_exercised_by_demo": True}

    def gui_smoke(self) -> dict:
        data = self.out / "中文 自检" / "data"
        self.command(self.gui, "--smoke-test", "--port", "0", "--data-dir", str(data), timeout=180)
        result = load_json(data / "smoke-test.json")
        require(result.get("status") == "passed" and result.get("version") == self.version, "GUI startup self-test failed")
        require(result.get("real_model_calls") == 0 and result.get("identification_models", 0) >= 100,
                "GUI model bank or offline smoke flags invalid")
        require(result.get("reference_packs", 0) >= 1 and all(size > 100 for size in result.get("assets", {}).values()),
                "GUI resources missing")
        require(set(result.get("assets", {})) == {"/", "/app.js", "/app.css"}, "GUI asset check incomplete")
        require(not (data / ".portable-session.json").exists(), "GUI executable left an active session file")
        port = result.get("port")
        require(type(port) is int and 1024 <= port <= 65535, "Invalid GUI smoke port")
        deadline = time.monotonic() + 5
        closed = False
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                    pass
            except OSError:
                closed = True
                break
            time.sleep(0.1)
        require(closed, "GUI executable exited but its listening port remains open")
        require(not list((data / "runs").glob("*")), "GUI startup unexpectedly created model jobs")
        return {**result, "process_exited": True, "listening_port_closed": True, "unicode_data_path": True}

    def run(self) -> int:
        try:
            self.check("bundle_metadata", self.metadata)
            self.check("references", self.references)
            self.check("service_demo", self.service_demo)
            self.check("service_replay", self.service_replay)
            self.check("identify_demo", self.identify_demo)
            self.check("identify_replay", self.identify_replay)
            self.check("fingerprint_screening_and_replay", lambda: self.fingerprint("screening"))
            self.check("fingerprint_full_and_replay", lambda: self.fingerprint("full"))
            self.check("inspect_met_demo", self.inspect_met)
            self.check("gui_executable_start_and_stop", self.gui_smoke)
            self.check("default_data_remains_clean", lambda: {"files": self.default_data_files(), "user_data_included": False})
            self.results["status"] = "passed"
            return 0
        except Exception as exc:
            self.results["status"] = "failed"
            self.results["error"] = scrub(str(exc))
            print(f"Portable check failed: {scrub(str(exc))}", file=sys.stderr)
            return 1
        finally:
            self.results["finished_at"] = datetime.now(timezone.utc).isoformat()
            self.results["total_seconds"] = round(sum(row.get("seconds", 0) for row in self.results["checks"]), 3)
            self.save()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the shipped Windows bundle using only Python's standard library.")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="A new directory; never overwrite an earlier verification")
    args = parser.parse_args(argv)
    bundle, out = args.bundle.resolve(), args.out.resolve()
    if not bundle.is_dir():
        parser.error("--bundle must be an extracted bundle directory")
    if out.exists():
        parser.error("--out already exists; choose a new directory")
    if out.is_relative_to(bundle):
        parser.error("--out must be outside the bundle")
    if os.name != "nt":
        parser.error("This executable acceptance check runs on Windows")
    out.mkdir(parents=True)
    return Checker(bundle, out).run()


if __name__ == "__main__":
    raise SystemExit(main())
