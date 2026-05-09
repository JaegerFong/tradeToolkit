#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rule:
    pattern: re.Pattern[str]
    repl: str


DEFAULT_EXCLUDES = {
    "LICENSE",
    "COPYRIGHT.md",
    "LICENSING.md",
    "COMMERCIAL_LICENSE_TEMPLATE.md",
    str(Path("app") / "LICENSE"),
    str(Path("frontend") / "LICENSE"),
}


DEFAULT_TEXT_GLOBS = {
    ".md",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
    ".json",
    ".py",
    ".ps1",
    ".sh",
    ".bat",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".vue",
    ".html",
    ".css",
    ".scss",
    ".env",
    ".dockerignore",
    "Dockerfile",
}


def should_scan(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name == ".gitignore":
        return True
    if path.name.startswith(".") and path.suffix == "":
        # e.g. ".env"
        return True
    if path.name == "Dockerfile":
        return True
    if path.suffix in DEFAULT_TEXT_GLOBS:
        return True
    return False


def iter_files(root: Path) -> list[Path]:
    results: list[Path] = []
    for p in root.rglob("*"):
        if ".git" in p.parts:
            continue
        if "node_modules" in p.parts:
            continue
        if "__pycache__" in p.parts:
            continue
        if should_scan(p):
            results.append(p)
    return results


def normalize_rel(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def apply_rules(text: str, rules: list[Rule]) -> str:
    for r in rules:
        text = r.pattern.sub(r.repl, text)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Debrand repository text references (safe mode excludes license/copyright files)."
    )
    ap.add_argument("--root", default=".", help="Repo root (default: .)")
    ap.add_argument("--project-name", default="tradeToolkit", help="New project display name")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report files that would change, do not write",
    )
    args = ap.parse_args()

    root = Path(args.root).resolve()
    project_name = str(args.project_name).strip() or "tradeToolkit"

    excludes = {p.replace("\\", "/") for p in DEFAULT_EXCLUDES}
    legacy_project_name = "TradingAgents" + "-CN"
    rules = [
        Rule(re.compile(re.escape(legacy_project_name), re.IGNORECASE), project_name),
        Rule(re.compile(r"tradingagents-ai\.com", re.IGNORECASE), ""),
        Rule(re.compile(r"", re.IGNORECASE), ""),
        Rule(re.compile(r"hsliup@163\.com", re.IGNORECASE), ""),
        Rule(re.compile(r""), ""),
    ]

    changed: list[str] = []
    for p in iter_files(root):
        rel = normalize_rel(root, p)
        if rel in excludes or p.name in excludes:
            continue

        try:
            raw = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # best-effort: skip binary-ish or non-utf8 text
            continue

        updated = apply_rules(raw, rules)
        if updated != raw:
            changed.append(rel)
            if not args.dry_run:
                p.write_text(updated, encoding="utf-8")

    print(f"Root: {root}")
    print(f"Project name: {project_name}")
    print(f"Changed files: {len(changed)}")
    for rel in changed[:200]:
        print(f"- {rel}")
    if len(changed) > 200:
        print(f"... ({len(changed) - 200} more)")

    if args.dry_run:
        print("Dry-run only. No files written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

