"""Local demo, Inspect collection and reproducible offline comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .records import load_records, save_records
from .report import STATUS_LABELS, write_report


def _analyze(reference: Path, candidate: Path, out: Path, args: argparse.Namespace) -> dict:
    from .stats import compare_records

    def read_partial(path: Path):
        return load_records(path) if path.read_text(encoding="utf-8").strip() else []
    left, right = read_partial(reference), read_partial(candidate)
    if not left and not right:
        raise ValueError("两份文件都没有记录；至少需要一侧的已保存证据。")
    result = compare_records(
        left, right,
        permutations=args.permutations, seed=args.seed,
        alpha=args.alpha, min_samples=args.min_samples,
    )
    return write_report(result, out, reference=reference, candidate=candidate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="API服务一致性研究工具；输出有限行为证据。")
    commands = parser.add_subparsers(dest="command", required=True)
    refs = commands.add_parser("references", help="列出内置与本机参考包，不调用模型API")
    refs.add_argument("--library-dir", type=Path, default=Path("runs/reference-library"))
    refs_update = commands.add_parser("references-update", help="从KBF公开目录抓取并检查参考包，不调用模型API")
    refs_update.add_argument("--library-dir", type=Path, default=Path("runs/reference-library"))
    fp_plan = commands.add_parser("fingerprint-plan", help="检查单API指纹计划与调用上限，不调用API")
    fp_plan.add_argument("--config", type=Path, required=True)
    fp_run = commands.add_parser("fingerprint", help="用内置参考包检测一个API")
    fp_run.add_argument("--config", type=Path, required=True)
    fp_run.add_argument("--out", type=Path, required=True)
    fp_run.add_argument("--study", type=Path, help="可选服务研究档案JSON")
    fp_replay = commands.add_parser("fingerprint-replay", help="从指纹原始记录离线复算")
    fp_replay.add_argument("--reference-pack", required=True)
    fp_replay.add_argument("--records", type=Path, required=True)
    fp_replay.add_argument("--out", type=Path, required=True)
    fp_replay.add_argument("--study", type=Path)
    fp_demo = commands.add_parser("fingerprint-demo", help="用人工回答演示指纹报告，不调用API")
    fp_demo.add_argument("--reference-pack", required=True)
    fp_demo.add_argument("--variant", choices=["match", "different", "incomplete"], default="different")
    fp_demo.add_argument("--out", type=Path, required=True)
    for command in (fp_plan, fp_run, fp_replay, fp_demo):
        command.add_argument("--library-dir", type=Path, default=Path("runs/reference-library"))
    demo = commands.add_parser("demo", help="使用Inspect合成fixture，完全不调用真实模型")
    demo.add_argument("--out", type=Path, required=True, help="新输出目录；不覆盖已有证据")
    demo.add_argument("--repeats", type=int, default=12)
    compare = commands.add_parser("compare", help="从JSONL离线复算MET，不调用API")
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    compare.add_argument("--study", type=Path, help="可选服务研究档案JSON")
    lab = commands.add_parser("lab", help="模拟替换、路由、提示改写、限额或故障，无API调用")
    lab.add_argument("--scenario", choices=["honest", "substitution", "mixed_route", "prompt_rewrite", "output_cap", "unstable"], default="substitution")
    lab.add_argument("--out", type=Path, required=True)
    lab.add_argument("--repeats", type=int, default=12)
    for command in [demo, compare, lab]:
        command.add_argument("--permutations", type=int, default=499)
        command.add_argument("--seed", type=int, default=11)
        command.add_argument("--alpha", type=float, default=0.05)
        command.add_argument("--min-samples", type=int, default=8, help="工程下限，不保证统计功效")
    collect = commands.add_parser("collect", help="按显式配置调用OpenAI兼容端点，可能产生费用")
    collect.add_argument("--config", type=Path, required=True)
    collect.add_argument("--out", type=Path, required=True)
    plan = commands.add_parser("plan", help="检查双端点配置与请求上限，不调用API")
    plan.add_argument("--config", type=Path, required=True)
    run = commands.add_parser("run", help="交错采集两侧服务并生成报告，可能产生费用")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--study", type=Path, help="可选服务研究档案JSON，不发送至API")
    ui = commands.add_parser("ui", help="启动本机浏览器工作台")
    ui.add_argument("--port", type=int, default=8765)
    ui.add_argument("--runs-dir", type=Path, default=Path("runs/ui"))
    ui.add_argument("--library-dir", type=Path, default=Path("runs/reference-library"))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "out", None) is not None and args.out.exists():
        parser.error("输出目录已存在，请选一个新目录以保留已有证据。")
    if args.command in ["demo", "compare", "lab"]:
        if args.permutations < 19 or not 0 < args.alpha < 1 or args.min_samples < 2:
            parser.error("permutations至少19；alpha在0与1之间；min-samples至少2。")
    if args.command == "demo" and args.repeats < 2:
        parser.error("repeats至少2。")
    try:
        from .study import attach_study, redact_study, validate_study

        study = validate_study(json.loads(args.study.read_text(encoding="utf-8"))) if getattr(args, "study", None) else {}
        if args.command == "references":
            from .fingerprint import list_reference_packs
            print(json.dumps(list_reference_packs(args.library_dir), ensure_ascii=False, indent=2))
        elif args.command == "references-update":
            from .reference_library import update_reference_library
            result = update_reference_library(args.library_dir)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if result.get("status") == "cancelled":
                return 130
            if result.get("status") in {"failed", "error", "busy", "partial"}:
                return 1
        elif args.command in {"fingerprint-plan", "fingerprint", "fingerprint-demo", "fingerprint-replay"}:
            from .fingerprint import plan_fingerprint, run_fingerprint, demo_fingerprint, replay_fingerprint

            if args.command in {"fingerprint-plan", "fingerprint"}:
                payload = json.loads(args.config.read_text(encoding="utf-8"))
                if not isinstance(payload, dict) or not isinstance(payload.get("candidate"), dict):
                    raise ValueError("配置需要reference_pack和candidate对象。")
                if "api_key" in payload["candidate"]:
                    raise ValueError("CLI配置请使用api_key_env环境变量名称，移除明文api_key。")
                if args.command == "fingerprint-plan":
                    print(json.dumps(plan_fingerprint(payload, library_dir=args.library_dir), ensure_ascii=False, indent=2))
                    return 0
                study = redact_study(study, payload)
                result = run_fingerprint(payload, args.out, library_dir=args.library_dir)
            elif args.command == "fingerprint-demo":
                result = demo_fingerprint(args.reference_pack, args.out, variant=args.variant, library_dir=args.library_dir)
            else:
                result = replay_fingerprint(args.reference_pack, args.records, args.out, library_dir=args.library_dir)
            result = attach_study(result, study, args.out)
            labels = {"difference_detected": "发现显著差异", "no_difference_detected": "未检出明显差异", "insufficient_evidence": "证据不足"}
            print(labels.get(result["status"], result["status"]))
            print(f"报告：{args.out / 'report.md'}")
            if result.get("collection", {}).get("failure"):
                return 1
        elif args.command == "ui":
            try:
                from .web import serve
            except ImportError:
                parser.error('请先安装界面依赖：python -m pip install -e ".[api,ui]"')
            serve(port=args.port, runs_dir=args.runs_dir, library_dir=args.library_dir)
        elif args.command in {"plan", "run"}:
            from .workflows import plan_pair, run_pair

            payload = json.loads(args.config.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("配置应为JSON对象。")
            for role in ("reference", "candidate"):
                if "api_key" in payload.get(role, {}):
                    raise ValueError("CLI配置只填写api_key_env环境变量名称；请从文件中移除明文api_key。")
            if args.command == "plan":
                print(json.dumps(plan_pair(payload), ensure_ascii=False, indent=2))
            else:
                study = redact_study(study, payload)
                result = run_pair(payload, args.out)
                result = attach_study(result, study, args.out)
                print(STATUS_LABELS.get(result["status"], result["status"]))
                print(f"报告：{args.out / 'report.md'}")
                if result.get("collection", {}).get("failure"):
                    return 1
        elif args.command == "lab":
            from .lab import run_scenario

            result = run_scenario(args.scenario, args.out, repeats=args.repeats, seed=args.seed,
                                  permutations=args.permutations, alpha=args.alpha, min_samples=args.min_samples)
            print(STATUS_LABELS.get(result["status"], result["status"]))
            print(f"模拟情境：{result['lab']['scenario']['name']}；报告：{args.out / 'report.md'}")
        elif args.command == "demo":
            from .collect import collect_fixture

            args.out.mkdir(parents=True)
            paths = {}
            for index, (role, variant) in enumerate([
                ("reference", "honest"), ("honest", "honest"), ("substituted", "substituted")
            ]):
                records = collect_fixture(
                    role, variant, repeats=args.repeats,
                    seed=args.seed + index, log_dir=args.out / "inspect" / role,
                )
                paths[role] = args.out / f"{role}.jsonl"
                save_records(paths[role], records)
            results = {}
            for role in ["honest", "substituted"]:
                result = _analyze(paths["reference"], paths[role], args.out / role, args)
                results[role] = {"status": result["status"], "synthetic": True}
                print(f"{role}: {STATUS_LABELS.get(result['status'], result['status'])}")
            with (args.out / "demo_summary.json").open("x", encoding="utf-8") as stream:
                json.dump({"synthetic": True, "real_model_calls": 0, "results": results}, stream, ensure_ascii=False, indent=2)
            print(f"合成流程完成；报告：{args.out}")
        elif args.command == "compare":
            result = _analyze(args.reference, args.candidate, args.out, args)
            result = attach_study(result, study, args.out)
            print(STATUS_LABELS.get(result["status"], result["status"]))
            print(f"报告：{args.out / 'report.md'}")
        elif args.command == "collect":
            from .collect import collect_endpoint

            records = collect_endpoint(args.config, log_dir=args.out / "inspect")
            save_records(args.out / "records.jsonl", records)
            print(f"采集完成：{args.out / 'records.jsonl'}；服务端自报字段不是独立身份认证。")
        return 0
    except KeyboardInterrupt:
        print("已取消；此前安装的参考包和已保存记录保留。")
        return 130
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        parser.error(str(exc))
    except Exception as exc:
        parser.error(f"执行失败（{type(exc).__name__}）；查看本地记录，避免在共享报告中复制凭据或原始异常。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
