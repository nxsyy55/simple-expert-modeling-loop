"""Run an inspection script while rejecting common model-fitting entry points."""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import runpy
import sys


FIT_NAMES = {"fit", "fit_predict", "partial_fit", "fit_transform", "train", "cross_validate"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=Path)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    parsed = parser.parse_args(argv)
    script = parsed.script.resolve()
    workspace = Path.cwd().resolve() / "workspace"
    if not script.is_relative_to(workspace / "scripts") or script.suffix.lower() != ".py":
        raise ValueError("Inspection script must be under workspace/scripts/*.py")
    source = script.read_text(encoding="utf-8-sig")
    ast.parse(source, filename=str(script))

    def guard(frame, event, arg):
        if event == "call" and frame.f_code.co_name in FIT_NAMES:
            raise RuntimeError("Model fitting is available only through the training tool")
        return guard

    sys.argv = [str(script), *parsed.args]
    sys.setprofile(guard)
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
