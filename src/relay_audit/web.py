"""Local browser workbench. All model calls remain explicit user actions."""

from __future__ import annotations

import copy
import json
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import __version__
from .records import AuditRecord, load_records, save_records
from .study import attach_study, redact_study, validate_study

STATIC = Path(__file__).parent / "static"
MAX_BODY_BYTES = 6 * 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class AnalysisSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repeats: int = Field(default=12, ge=2, le=100, strict=True)
    permutations: int = Field(default=199, ge=19, le=4999, strict=True)
    min_samples: int = Field(default=8, ge=2, le=100, strict=True)
    seed: int = Field(default=11, ge=0, le=2147483647, strict=True)
    alpha: float = Field(default=0.05, gt=0, lt=1, allow_inf_nan=False)


def parse_jsonl(text: Any, *, allow_empty: bool = False) -> list[AuditRecord]:
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_UPLOAD_BYTES:
        raise ValueError("每个证据文件应为不超过2 MB的JSONL文本。")
    records = []
    for number, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        if not line.strip():
            continue
        if len(records) >= 3000:
            raise ValueError("每侧最多导入3000条记录。")
        try:
            records.append(AuditRecord.model_validate_json(line))
        except ValueError:
            raise ValueError(f"第{number}行不是API-MRI证据记录，请导入采集生成的JSONL。") from None
    if not records and not allow_empty:
        raise ValueError("文件中没有证据记录。")
    return records


def _analysis_settings(payload: dict) -> AnalysisSettings:
    try:
        return AnalysisSettings.model_validate(payload)
    except ValueError:
        raise ValueError("请检查重复次数、置换次数和样本门槛；数字须在界面给定范围内。") from None


def validate_job(payload: Any, *, library_dir: Path | None = None) -> dict:
    if not isinstance(payload, dict):
        raise ValueError("请求应为JSON对象。")
    mode = payload.get("mode")
    settings = _analysis_settings(payload)
    clean = {"mode": mode, **settings.model_dump()}
    if mode in {"fingerprint", "fingerprint_demo", "fingerprint_offline"}:
        from .fingerprint import list_reference_packs, plan_fingerprint

        pack_id = payload.get("reference_pack")
        if pack_id not in {pack["id"] for pack in list_reference_packs(library_dir)}:
            raise ValueError("请选择库中已有的参考包；暂不支持的版本需要另建参考。")
        clean["reference_pack"] = pack_id
        if mode == "fingerprint":
            clean.update({key: payload[key] for key in ("candidate", "timeout", "profile", "budget") if key in payload})
            plan_fingerprint({key: clean[key] for key in ("reference_pack", "candidate", "timeout", "profile", "budget") if key in clean}, library_dir=library_dir)
            clean["study"] = validate_study(payload.get("study"))
        elif mode == "fingerprint_demo":
            variant = payload.get("variant", "different")
            if variant not in {"match", "different", "incomplete"}:
                raise ValueError("请选择已有指纹演示情境。")
            clean["variant"] = variant
            profile = payload.get("profile", "screening")
            if profile not in {"screening", "full"}:
                raise ValueError("请选择低成本初筛或完整参考比较。")
            clean["profile"] = profile
        else:
            value = payload.get("evidence_jsonl")
            if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 4 * 1024 * 1024:
                raise ValueError("请选择不超过4 MB的指纹证据JSONL。")
            from .fingerprint import parse_fingerprint_records
            clean["fingerprint_records"] = parse_fingerprint_records(value, pack_id, library_dir=library_dir)
            clean["study"] = validate_study(payload.get("study"))
        return clean
    if mode == "demo":
        return clean
    if mode == "lab":
        from .lab import list_scenarios

        if payload.get("scenario") not in {item["id"] for item in list_scenarios()}:
            raise ValueError("请选择实验台中的一个情境。")
        clean["scenario"] = payload["scenario"]
        return clean
    study = validate_study(payload.get("study"))
    if mode == "offline":
        clean["reference_records"] = parse_jsonl(payload.get("reference_jsonl"), allow_empty=True)
        clean["candidate_records"] = parse_jsonl(payload.get("candidate_jsonl"), allow_empty=True)
        if not clean["reference_records"] and not clean["candidate_records"]:
            raise ValueError("两份文件都没有记录；至少需要一侧的已保存证据。")
        clean["study"] = study
        return clean
    if mode == "live":
        from .workflows import plan_pair

        allowed = {"reference", "candidate", "prompts", "max_tokens", "temperature", "timeout", "attempt_timeout"}
        clean.update({key: payload[key] for key in allowed if key in payload})
        # Validation is performed before the job is accepted; planning makes no calls.
        plan_pair({key: value for key, value in clean.items() if key != "mode"})
        clean["study"] = study
        return clean
    raise ValueError("请选择工作台支持的检测或复算方式。")


class JobManager:
    """One worker at a time avoids interleaved experiments and Inspect globals."""

    def __init__(self, root: Path, *, library_dir: Path | None = None):
        self.root = root.resolve()
        self.library_dir = library_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.jobs: dict[str, dict] = {}
        self.events: dict[str, threading.Event] = {}
        self.active: str | None = None
        self.threads: list[threading.Thread] = []
        for file in sorted(self.root.glob("*/job.json"))[-100:]:
            try:
                if not file.resolve().is_relative_to(self.root) or file.stat().st_size > 2 * 1024 * 1024:
                    continue
                job = json.loads(file.read_text(encoding="utf-8"))
                if job.get("id") != file.parent.name or not self.valid_id(job["id"]):
                    continue
                if job.get("status") in {"queued", "running"}:
                    job["status"] = "cancelled"
                    job["error"] = "上次运行因服务退出而中断，已保存的文件仍保留在本地。"
                self.jobs[job["id"]] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue

    @staticmethod
    def valid_id(value: str) -> bool:
        return isinstance(value, str) and 1 <= len(value) <= 80 and all(c in "0123456789-abcdefT" for c in value)

    def _save(self, job: dict) -> None:
        directory = self.root / job["id"]
        directory.mkdir(exist_ok=True)
        temp = directory / "job.json.tmp"
        temp.write_text(json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")
        temp.replace(directory / "job.json")

    def get(self, identifier: str) -> dict:
        with self.lock:
            if not self.valid_id(identifier) or identifier not in self.jobs:
                raise KeyError(identifier)
            return copy.deepcopy(self.jobs[identifier])

    def list(self) -> list[dict]:
        with self.lock:
            rows = []
            for job in sorted(self.jobs.values(), key=lambda j: j["created_at"], reverse=True)[:50]:
                row = {key: copy.deepcopy(value) for key, value in job.items() if key != "reports"}
                row["reports"] = []
                for entry in job.get("reports", []):
                    report = entry["report"]
                    summary = {key: copy.deepcopy(report[key]) for key in ("kind", "status", "synthetic", "study", "profile", "cost") if key in report}
                    if report.get("lab"):
                        summary["lab"] = {"scenario": copy.deepcopy(report["lab"].get("scenario", {}))}
                    if report.get("fingerprint"):
                        summary["fingerprint"] = {key: copy.deepcopy(report["fingerprint"][key]) for key in ("reference", "summary", "demo_variant") if key in report["fingerprint"]}
                    row["reports"].append({"name": entry["name"], "report": summary})
                rows.append(row)
            return rows

    def start(self, payload: dict) -> dict:
        with self.lock:
            if self.active is not None:
                raise RuntimeError("已有任务正在运行，请等待完成或先取消。")
            identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")+"-"+secrets.token_hex(5)
            job = {"id": identifier, "mode": payload["mode"], "status": "queued",
                   "created_at": datetime.now(timezone.utc).isoformat(), "reports": [],
                   "progress": {"completed": 0, "total": 0, "phase": "queued", "message": "准备运行"}}
            self.jobs[identifier] = job
            self.events[identifier] = threading.Event()
            self.active = identifier
            self._save(job)
            thread = threading.Thread(target=self._run, args=(identifier, payload), daemon=True, name="relay-audit-worker")
            self.threads.append(thread)
            thread.start()
            return copy.deepcopy(job)

    def _update(self, identifier: str, **changes) -> None:
        with self.lock:
            self.jobs[identifier].update(changes)
            self._save(self.jobs[identifier])

    def cancel(self, identifier: str) -> dict:
        with self.lock:
            self.get(identifier)
            if identifier in self.events and self.jobs[identifier]["status"] in {"queued", "running"}:
                self.events[identifier].set()
                self.jobs[identifier]["progress"]["message"] = "正在停止：当前请求或计算结束后保存结果。"
                self._save(self.jobs[identifier])
            return self.get(identifier)

    def close(self) -> None:
        for event in self.events.values():
            event.set()

    def _run(self, identifier: str, payload: dict) -> None:
        out = self.root / identifier / "evidence"
        event = self.events[identifier]
        settings = {key: payload[key] for key in ("permutations", "seed", "alpha", "min_samples")}
        progress = lambda value: self._update(identifier, progress=value)
        try:
            self._update(identifier, status="running")
            reports = []
            if event.is_set():
                self._update(identifier, status="cancelled")
                return
            if payload["mode"] in {"fingerprint", "fingerprint_demo", "fingerprint_offline"}:
                from .fingerprint import run_fingerprint, demo_fingerprint, replay_fingerprint

                if payload["mode"] == "fingerprint":
                    data = {key: payload[key] for key in ("reference_pack", "candidate", "timeout", "profile", "budget") if key in payload}
                    study = redact_study(payload.get("study", {}), data)
                    result = run_fingerprint(data, out, progress=progress, cancelled=event.is_set, library_dir=self.library_dir)
                elif payload["mode"] == "fingerprint_demo":
                    progress({"completed": 0, "total": 1, "phase": "analyzing", "message": "生成本地指纹示例并计算"})
                    study = {}
                    result = demo_fingerprint(payload["reference_pack"], out, variant=payload["variant"], profile=payload["profile"], library_dir=self.library_dir)
                else:
                    progress({"completed": 0, "total": 1, "phase": "analyzing", "message": "从原始记录重新解析指纹"})
                    study = payload.get("study", {})
                    result = replay_fingerprint(payload["reference_pack"], payload["fingerprint_records"], out, library_dir=self.library_dir)
                reports.append({"name": "fingerprint", "report": attach_study(result, study, out)})
            elif payload["mode"] == "live":
                from .workflows import run_pair

                study = redact_study(payload.get("study", {}), payload)
                result = run_pair({key: value for key, value in payload.items() if key not in {"mode", "study"}}, out,
                                  progress=progress, cancelled=event.is_set)
                result = attach_study(result, study, out)
                reports.append({"name": "comparison", "report": result})
            elif payload["mode"] == "lab":
                from .lab import run_scenario

                progress({"completed": 0, "total": 1, "phase": "analyzing", "message": "生成所选情境并计算差异"})
                result = run_scenario(payload["scenario"], out, repeats=payload["repeats"], **settings)
                reports.append({"name": "comparison", "report": result})
            else:
                from .report import write_report
                from .stats import compare_records

                out.mkdir(parents=True)
                if payload["mode"] == "demo":
                    from .collect import collect_fixture

                    paths = {}
                    for index, (role, variant) in enumerate((("reference", "honest"), ("honest", "honest"), ("substituted", "substituted"))):
                        if event.is_set():
                            break
                        progress({"completed": index, "total": 5, "phase": "collecting", "message": "生成本地模拟回答"})
                        rows = collect_fixture(role, variant, repeats=payload["repeats"], seed=payload["seed"]+index,
                                               log_dir=out / "inspect" / role)
                        paths[role] = out / f"{role}.jsonl"
                        save_records(paths[role], rows)
                    for index, role in enumerate(("honest", "substituted"), 3):
                        if event.is_set() or role not in paths or "reference" not in paths:
                            break
                        progress({"completed": index, "total": 5, "phase": "analyzing", "message": "比较模拟回答"})
                        result = compare_records(load_records(paths["reference"]), load_records(paths[role]), **settings)
                        reports.append({"name": role, "report": write_report(result, out / role,
                                       reference=paths["reference"], candidate=paths[role])})
                else:
                    progress({"completed": 0, "total": 1, "phase": "analyzing", "message": "读取本地证据并计算"})
                    left, right = payload["reference_records"], payload["candidate_records"]
                    reference, candidate = out / "reference.jsonl", out / "candidate.jsonl"
                    save_records(reference, left)
                    save_records(candidate, right)
                    result = compare_records(left, right, **settings)
                    result = write_report(result, out, reference=reference, candidate=candidate)
                    reports.append({"name": "comparison", "report": attach_study(result, payload.get("study", {}), out)})
            cancelled = event.is_set() or any(item["report"].get("collection", {}).get("cancelled") for item in reports)
            budget_stops = {"budget_max_requests", "budget_max_output_tokens"}
            budget_stopped = any(item["report"].get("collection", {}).get("stop_code") in budget_stops for item in reports)
            failed = any(item["report"].get("collection", {}).get("failure")
                         and item["report"].get("collection", {}).get("stop_code") not in budget_stops for item in reports)
            job_progress = self.get(identifier)["progress"]
            if failed:
                job_progress.update(phase="failed", message="采集中断，已保存部分证据与报告")
            elif not cancelled:
                if budget_stopped:
                    job_progress.update(phase="completed", message="已按预算停止，部分回答与用量记录已保存")
                else:
                    job_progress.update(completed=job_progress["total"], phase="completed", message="结果已保存")
            else:
                job_progress.update(phase="cancelled", message="已停止，已完成的记录保留在本地")
            self._update(identifier, status="failed" if failed else "cancelled" if cancelled else "completed", reports=reports, progress=job_progress)
        except Exception as exc:
            # Upstream errors may include secrets or user content. Never persist their text.
            self._update(identifier, status="failed", error=f"运行未完成（{type(exc).__name__}）。请检查配置、网络或导入记录，修正后新建任务。")
        finally:
            payload.clear()
            with self.lock:
                self.active = None

    def file(self, identifier: str, report_name: str, format_name: str) -> Path:
        job = self.get(identifier)
        if report_name not in {item["name"] for item in job.get("reports", [])}:
            raise KeyError(report_name)
        names = {"json": "report.json", "md": "report.md", "csv": "prompts.csv"}
        if job["mode"].startswith("fingerprint"):
            names["jsonl"] = "fingerprint_records.jsonl"
        filename = names.get(format_name)
        if filename is None:
            raise KeyError(format_name)
        directory = self.root / identifier / "evidence"
        if job["mode"] == "demo":
            directory = directory / report_name
        target = (directory / filename).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise KeyError(filename)
        return target

    def records(self, identifier: str, report_name: str) -> dict:
        job = self.get(identifier)
        if job["status"] not in {"completed", "cancelled", "failed"}:
            return {"reference": [], "candidate": []}
        if report_name not in {item["name"] for item in job.get("reports", [])}:
            raise KeyError(report_name)
        directory = self.root / identifier / "evidence"
        if job["mode"].startswith("fingerprint"):
            path = (directory / "fingerprint_records.jsonl").resolve()
            if not path.is_relative_to(self.root):
                raise KeyError("fingerprint_records.jsonl")
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []
            return {"fingerprint": rows}
        names = {"reference": "reference.jsonl", "candidate": f"{report_name}.jsonl" if job["mode"] == "demo" else "candidate.jsonl"}
        output = {}
        for role, filename in names.items():
            path = (directory / filename).resolve()
            if not path.is_relative_to(self.root):
                raise KeyError(filename)
            output[role] = [row.model_dump(mode="json") for row in load_records(path)] if path.exists() and path.stat().st_size else []
        return output


class ReferenceUpdateManager:
    """One explicit data-only update, persisted separately from model evidence."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "update-job.json"
        self.lock = threading.RLock()
        self.event = threading.Event()
        self.thread: threading.Thread | None = None
        self.job: dict = {"id": None, "status": "idle", "progress": {}, "result": None}
        try:
            if self.path.is_file() and self.path.stat().st_size <= 2 * 1024 * 1024:
                previous = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(previous, dict) and previous.get("status") in {"queued", "running", "completed", "cancelled", "failed"}:
                    if previous["status"] in {"queued", "running"}:
                        previous.update(status="cancelled", error="上次更新因服务退出中断，已安装的包保留。")
                    self.job = previous
        except (OSError, ValueError, TypeError):
            pass

    def snapshot(self) -> dict:
        with self.lock:
            return copy.deepcopy(self.job)

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.job, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def _update(self, **changes) -> None:
        with self.lock:
            self.job.update(changes)
            self._save()

    def start(self) -> dict:
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError("Reference update already running")
            self.event.clear()
            self.job = {"id": secrets.token_hex(12), "status": "queued", "created_at": datetime.now(timezone.utc).isoformat(),
                        "progress": {"phase": "queued", "completed": 0, "total": 0, "message": "准备读取 KBF 公开参考目录"}, "result": None}
            self._save()
            self.thread = threading.Thread(target=self._run, daemon=True, name="relay-reference-update")
            self.thread.start()
            return self.snapshot()

    def _run(self) -> None:
        try:
            from .reference_library import update_reference_library

            self._update(status="running")
            result = update_reference_library(self.root, progress=lambda value: self._update(progress=value), cancelled=self.event.is_set)
            state = "cancelled" if self.event.is_set() or result.get("status") == "cancelled" else "failed" if result.get("status") in {"failed", "error", "busy"} else "completed"
            self._update(status=state, result=result, finished_at=datetime.now(timezone.utc).isoformat())
        except Exception as exc:
            self._update(status="cancelled" if self.event.is_set() else "failed", error=f"参考更新未完成（{type(exc).__name__}）。请检查网络或稍后重试；已安装的包保留。",
                         finished_at=datetime.now(timezone.utc).isoformat())

    def cancel(self) -> dict:
        with self.lock:
            if self.job["status"] in {"queued", "running"}:
                self.event.set()
                self._update(progress={**self.job.get("progress", {}), "message": "正在停止更新，已安装参考保留。"})
            return self.snapshot()

    def close(self) -> None:
        self.event.set()


def create_app(runs_dir: Path | str = "runs/ui", *, library_dir: Path | str | None = None) -> FastAPI:
    library_path = Path(library_dir) if library_dir is not None else Path(runs_dir).parent / "reference-library"
    manager = JobManager(Path(runs_dir), library_dir=library_path)
    updater = ReferenceUpdateManager(library_path)
    session_token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close()
        updater.close()

    app = FastAPI(title="API-MRI", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager = manager
    app.state.reference_updater = updater

    @app.middleware("http")
    async def local_access(request: Request, call_next):
        try:
            host = urlsplit("http://"+request.headers.get("host", "")).hostname
        except ValueError:
            return JSONResponse({"detail": "无效的本机地址。"}, status_code=403)
        if host not in LOOPBACK_HOSTS:
            return JSONResponse({"detail": "此工作台只接受本机访问。"}, status_code=403)
        origin = request.headers.get("origin")
        expected = f"{request.url.scheme}://{request.headers.get('host')}"
        if origin and origin != expected:
            return JSONResponse({"detail": "请求来源不匹配，请从本机工作台操作。"}, status_code=403)
        if request.method not in {"GET", "HEAD"}:
            if not secrets.compare_digest(request.headers.get("x-relay-token", ""), session_token):
                return JSONResponse({"detail": "会话已更新，请刷新页面后重试。"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "需要JSON格式请求。"}, status_code=415)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    return JSONResponse({"detail": "上传内容过大，每个JSONL最多2 MB。"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({"detail": "请求格式不正确，请检查配置或导入文件。"}, status_code=422)

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/app.js")
    def javascript():
        return FileResponse(STATIC / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    def stylesheet():
        return FileResponse(STATIC / "app.css", media_type="text/css")

    @app.get("/api/bootstrap")
    def bootstrap():
        from .lab import list_scenarios
        from .probes import list_probes
        from .fingerprint import list_reference_packs

        return {"version": __version__, "session_token": session_token, "probes": list_probes(),
                "scenarios": list_scenarios(), "reference_packs": list_reference_packs(library_path),
                "capabilities": {"modes": ["fingerprint", "fingerprint_demo", "fingerprint_offline", "demo", "lab", "live", "offline"], "protocol": "Chat Completions", "local_only": True}}

    @app.get("/api/runs")
    def runs():
        return {"runs": manager.list()}

    @app.get("/api/reference-library")
    def reference_library():
        from .fingerprint import list_reference_packs
        from .reference_library import reference_library_warnings

        state = updater.snapshot()
        return {"reference_packs": list_reference_packs(library_path),
                "source": {"repository": "Ooo0ption/KBF", "directory": "probes/reference_YYYYMM",
                           "url": "https://github.com/Ooo0ption/KBF", "automatic_code_updates": False},
                "last_update": state if state.get("id") else None, "warnings": reference_library_warnings(library_path)}

    @app.get("/api/reference-library/update")
    def reference_update():
        return updater.snapshot()

    @app.post("/api/reference-library/update", status_code=202)
    def start_reference_update(payload: dict):
        if payload:
            raise HTTPException(422, "参考更新不需要模型配置、密钥或自定义网址。")
        if manager.active is not None:
            raise HTTPException(409, "请先等待当前检测结束，再更新参考库。")
        try:
            return updater.start()
        except RuntimeError:
            raise HTTPException(409, "参考库正在更新，请等待或先取消。") from None

    @app.post("/api/reference-library/update/cancel")
    def cancel_reference_update(payload: dict):
        if payload:
            raise HTTPException(422, "取消更新需要空 JSON 对象。")
        return updater.cancel()

    @app.post("/api/plan")
    def plan(payload: dict):
        from .workflows import plan_pair

        try:
            if payload.get("mode") == "fingerprint":
                from .fingerprint import plan_fingerprint
                cleaned = validate_job(payload, library_dir=library_path)
                return plan_fingerprint({key: cleaned[key] for key in ("reference_pack", "candidate", "timeout", "profile", "budget") if key in cleaned}, library_dir=library_path)
            cleaned = validate_job({**payload, "mode": "live"})
            return plan_pair({key: value for key, value in cleaned.items() if key not in {"mode", "study"}})
        except (ValueError, TypeError):
            raise HTTPException(422, "请检查参考包、Base URL、模型名和预算；请求数与输出配额须填写正整数。") from None

    @app.post("/api/runs", status_code=202)
    def start(payload: dict):
        try:
            cleaned = validate_job(payload, library_dir=library_path)
        except (ValueError, TypeError) as exc:
            detail = str(exc) if payload.get("mode") == "offline" and not isinstance(exc, ValidationError) else "请检查参考包、服务地址、模型名和文件格式；指纹复算需使用同一参考包的原始JSONL。"
            raise HTTPException(422, detail) from None
        try:
            return manager.start(cleaned)
        except RuntimeError:
            raise HTTPException(409, "已有任务正在运行，请等待完成或先取消。") from None

    @app.get("/api/runs/{identifier}")
    def run(identifier: str):
        try:
            return manager.get(identifier)
        except KeyError:
            raise HTTPException(404, "没有找到这次运行。") from None

    @app.post("/api/runs/{identifier}/cancel")
    def cancel(identifier: str):
        try:
            return manager.cancel(identifier)
        except KeyError:
            raise HTTPException(404, "没有找到这次运行。") from None

    @app.get("/api/runs/{identifier}/download/{report_name}/{format_name}")
    def download(identifier: str, report_name: str, format_name: str):
        try:
            path = manager.file(identifier, report_name, format_name)
        except KeyError:
            raise HTTPException(404, "报告尚未生成或格式不存在。") from None
        return FileResponse(path, filename=f"api-mri-{identifier}-{report_name}.{format_name}")

    @app.get("/api/runs/{identifier}/records")
    def records(identifier: str, report: str = "comparison"):
        try:
            return manager.records(identifier, report)
        except KeyError:
            raise HTTPException(404, "没有找到这份报告的记录。") from None

    return app


def serve(*, port: int = 8765, runs_dir: Path | str = "runs/ui", library_dir: Path | str | None = None) -> None:
    import uvicorn

    if not 1024 <= port <= 65535:
        raise ValueError("端口应在1024到65535之间。")
    print(f"API-MRI 工作台：http://127.0.0.1:{port}（Ctrl+C停止）", flush=True)
    uvicorn.run(create_app(runs_dir, library_dir=library_dir), host="127.0.0.1", port=port, access_log=False, log_level="warning")
