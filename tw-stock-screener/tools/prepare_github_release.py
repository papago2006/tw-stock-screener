"""Create a new allowlisted review copy; never git add/commit/push or deploy."""
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import shutil


def prepare(project, destination):
    project, destination = Path(project).resolve(strict=True), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copy2(project.parent / "AGENTS.md", destination / "AGENTS.md")
    workflow = destination / ".github/workflows/pages.yml"
    workflow.parent.mkdir(parents=True)
    shutil.copy2(project / "deployment/pages.yml", workflow)
    (destination / "README.md").write_text(
        "# tw-stock-screener\n\nPython 專案位於 `tw-stock-screener/`。\n\n"
        "開發前讀取 [AGENTS.md](AGENTS.md)、[SPEC](tw-stock-screener/docs/SPEC.md) 及 "
        "[CHANGELOG](tw-stock-screener/docs/CHANGELOG.md)。\n\n"
        "[安裝與執行](tw-stock-screener/README.md) · "
        "[發布範圍](tw-stock-screener/docs/DEPLOYMENT.md)\n\n"
        "[手機報表：台股選股 KD 觀察站](https://papago2006.github.io/tw-stock-screener/)\n\n"
        "手機報表目前為單日、待確認試算快照；尚未接入雲端選股、每日排程或手機確認入口。\n",
        encoding="utf-8")
    (destination / ".gitignore").write_text(
        ".tmp/\n.venv/\n**/__pycache__/\n*.py[cod]\n*.egg-info/\n"
        "**/data/\n**/output/\n**/release/\n**/local.yaml\n.env\n.env.*\n"
        "*.sqlite3*\n*.db\n*.log\n", encoding="utf-8")
    target = destination / "tw-stock-screener"
    target.mkdir()
    for name in ("AGENTS.md", "README.md", "pyproject.toml", "config.yaml"):
        shutil.copy2(project / name, target / name)
    for folder, suffixes in {"src": {".py"}, "tests": {".py", ".json", ".csv", ".html"},
                             "tools": {".py"}, "web": {".html", ".css", ".js", ".svg", ".webmanifest"},
                             "deployment": {".yml"}, "docs": {".md", ".gz"}}.items():
        for source in sorted((project / folder).rglob("*")):
            if not source.is_file() or source.is_symlink() or "__pycache__" in source.parts or source.suffix not in suffixes:
                continue
            relative = source.relative_to(project)
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, path)
    for name in ("index.html", "styles.css", "app.js", "icon.svg", "manifest.webmanifest",
                 "data.js", "data.json", "selected.csv", ".nojekyll"):
        (target / "site").mkdir(exist_ok=True)
        shutil.copy2(project / "site" / name, target / "site" / name)
    files = [{"path": p.relative_to(destination).as_posix(), "bytes": p.stat().st_size,
              "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
             for p in sorted(destination.rglob("*")) if p.is_file()]
    manifest = {"target_repository": "papago2006/tw-stock-screener", "status": "local_review_only",
                "files": files, "total_bytes": sum(f["bytes"] for f in files),
                "excluded": ["行情 SQLite 與 raw", "local.yaml", ".venv", "output 與執行日誌", ".tmp 備份",
                             "八法專案", "舊 mobile-preview", "私人截圖與壓縮檔"],
                "deployment": "manual Pages only; no automatic quote calculation/schedule"}
    destination.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    stamp = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    destination = args.destination or project / "release" / ("github-pages-" + stamp)
    manifest = prepare(project, destination)
    print(json.dumps({"destination": str(destination), "files": len(manifest["files"]),
                      "total_bytes": manifest["total_bytes"]}))
