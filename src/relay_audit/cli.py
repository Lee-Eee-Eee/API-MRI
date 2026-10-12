"""Local demo, Inspect collection and reproducible offline comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .records import load_records, save_records
from .report import STATUS_LABELS, write_report


def _analyze(reference: Path, candidate: Path, out: Path, args: argparse.Namespace) -> dict:
    from .stats import compare_records

    result = compare_records(
        load_records(reference), load_records(candidate),
        permutations=args.permutations, seed=args.seed,
        alpha=args.alpha, min_samples=args.min_samples,
    )
    return write_report(result, out, reference=reference, candidate=candidate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="API服务一致性研究工具；输出有限行为证据。")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="使用Inspect合成fixture，完全不调用真实模型")
    demo.add_argument("--out", type=Path, required=True, help="新输出目录；不覆盖已有证据")
    demo.add_argument("--repeats", type=int, default=12)
    compare = commands.add_parser("compare", help="从JSONL离线复算MET，不调用API")
    compare.add_argument("--reference", type=Path, required=True)
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--out", type=Path, required=True)
    for command in [demo, compare]:
        command.add_argument("--permutations", type=int, default=499)
        command.add_argument("--seed", type=int, default=11)
        command.add_argument("--alpha", type=float, default=0.05)
        command.add_argument("--min-samples", type=int, default=8, help="工程下限，不保证统计功效")
    collect = commands.add_parser("collect", help="按显式配置调用OpenAI兼容端点，可能产生费用")
    collect.add_argument("--config", type=Path, required=True)
    collect.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error("输出目录已存在，请选一个新目录以保留已有证据。")
    if args.command in ["demo", "compare"]:
        if args.permutations < 19 or not 0 < args.alpha < 1 or args.min_samples < 2:
            parser.error("permutations至少19；alpha在0与1之间；min-samples至少2。")
    if args.command == "demo" and args.repeats < 2:
        parser.error("repeats至少2。")
    try:
        if args.command == "demo":
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
            print(STATUS_LABELS.get(result["status"], result["status"]))
            print(f"报告：{args.out / 'report.md'}")
        else:
            from .collect import collect_endpoint

            records = collect_endpoint(args.config, log_dir=args.out / "inspect")
            save_records(args.out / "records.jsonl", records)
            print(f"采集完成：{args.out / 'records.jsonl'}；服务端自报字段不是独立身份认证。")
        return 0
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        parser.error(str(exc))
    except Exception as exc:
        parser.error(f"执行失败（{type(exc).__name__}）；查看本地记录，避免在共享报告中复制凭据或原始异常。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
