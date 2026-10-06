import argparse
import getpass
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from .auth import set_password
from .config import load_config
from .evaluation import benchmark, score_review, summarize_log
from .inference import InferenceRunner
from .telemetry import event, provenance


def demo(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    image = np.full((960, 1280, 3), 45, np.uint8)
    cv2.rectangle(image, (230, 65), (1030, 895), (239, 242, 244), -1)
    lines = [
        "LINEAR ALGEBRA",
        "Eigenvectors and eigenvalues",
        "A v = lambda v",
        "A transformation can preserve",
        "the direction of a vector.",
        "Find lambda from det(A-lambda I)=0",
        "Example: diagonal matrices",
        "SYNTHETIC FIXTURE - NOT HANDWRITING",
    ]
    for index, line in enumerate(lines):
        cv2.putText(
            image,
            line,
            (270, 150 + index * 86),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.66,
            (40, 45, 40),
            2,
            cv2.LINE_AA,
        )
    path = directory / "demo.png"
    if path.exists():
        event("demo_exists", path=str(path))
        return
    cv2.imwrite(str(path), image)
    event("demo_created", path=str(path))


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(prog="notetaker")
    parser.add_argument("--config", default="config.toml")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "setup", help="Set password interactively; never printed or passed on command line"
    )
    commands.add_parser("serve")
    demo_parser = commands.add_parser("demo")
    demo_parser.add_argument("--directory", default="evaluation/replay")
    bench = commands.add_parser("benchmark")
    bench.add_argument("--manifest", required=True)
    bench.add_argument("--queries")
    bench.add_argument("--output", default="data/benchmark")
    bench.add_argument("--split", choices=["development", "heldout"], default="heldout")
    bench.add_argument("--strategy", choices=["full", "tiles"])
    smoke = commands.add_parser("smoke")
    smoke.add_argument("--image", required=True)
    smoke.add_argument("--output", default="data/hardware-smoke.json")
    summary = commands.add_parser("summarize-log")
    summary.add_argument("--log", required=True)
    summary.add_argument("--output", default="data/live-metrics.json")
    score = commands.add_parser("score-review")
    score.add_argument("--manifest", required=True)
    score.add_argument("--benchmark-dir", required=True)
    score.add_argument("--reviews", required=True)
    score.add_argument("--queries", required=True)
    score.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            demo(args.directory)
            return
        if args.command == "summarize-log":
            summarize_log(args.log, args.output)
            return
        if args.command == "score-review":
            score_review(args.manifest, args.benchmark_dir, args.reviews, args.queries, args.output)
            return
        config = load_config(args.config)
        if args.command == "setup":
            config.root.mkdir(parents=True, exist_ok=True)
            password = getpass.getpass("Choose a NoteTaker password (12+ characters): ")
            if password != getpass.getpass("Confirm password: "):
                raise ValueError("Passwords do not match")
            set_password(config.root / "auth.json", password)
            event("setup_complete", username="notetaker")
        elif args.command == "serve":
            import uvicorn

            from .app import create_app

            uvicorn.run(create_app(config), host=config.host, port=config.port, workers=1)
        elif args.command == "benchmark":
            if args.strategy:
                config = replace(config, strategy=args.strategy)
            benchmark(config, args.manifest, args.output, args.queries, args.split)
        elif args.command == "smoke":
            if config.provider != "hailo":
                raise ValueError('Hardware smoke test requires provider="hailo"')
            runner = InferenceRunner(config)
            try:
                result = runner.run(Path(args.image).resolve(), config.course)
            finally:
                runner.close()
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps({"provenance": provenance(config), "result": result}, indent=2)
            )
            if "error" in result:
                raise ValueError(result["error"])
            event("hardware_smoke_complete", output=str(output))
    except (ValueError, OSError) as exc:
        event("command_failed", error=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()
