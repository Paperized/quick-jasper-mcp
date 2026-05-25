from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class Settings:
    workspace_root: Path
    storage_root: Path
    java_build_dir: Path
    jasper_lib_dir: Path | None
    jasper_extra_classpath: list[str]
    retain_runs: bool

    @classmethod
    def from_env(cls) -> "Settings":
        workspace_root = Path(os.getenv("JRXML_MCP_WORKSPACE", Path.cwd())).resolve()
        storage_root = Path(os.getenv("JRXML_MCP_STORAGE", workspace_root / ".jrxml_mcp")).resolve()
        java_build_dir = storage_root / "java_classes"
        lib_env = os.getenv("JASPER_LIB_DIR")
        jasper_lib_dir = Path(lib_env).resolve() if lib_env else None
        if jasper_lib_dir is None:
            auto_vendor = workspace_root / "vendor" / "jasper-lib"
            if auto_vendor.exists():
                jasper_lib_dir = auto_vendor.resolve()
        extra_cp = [part for part in os.getenv("JASPER_EXTRA_CLASSPATH", "").split(os.pathsep) if part]
        retain_runs = _bool_env("JRXML_MCP_RETAIN_RUNS", True)
        return cls(
            workspace_root=workspace_root,
            storage_root=storage_root,
            java_build_dir=java_build_dir,
            jasper_lib_dir=jasper_lib_dir,
            jasper_extra_classpath=extra_cp,
            retain_runs=retain_runs,
        )

    @property
    def java_source_path(self) -> Path:
        return Path(__file__).parent / "java" / "JrxmlToolRunner.java"

    def ensure_dirs(self) -> None:
        self.storage_root.mkdir(parents=True, exist_ok=True)
        self.java_build_dir.mkdir(parents=True, exist_ok=True)

    def create_run_dir(self) -> Path:
        stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.storage_root / "runs" / f"{stamp}_{uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def collect_jasper_jars(self) -> list[Path]:
        jars: list[Path] = []
        if self.jasper_lib_dir and self.jasper_lib_dir.exists():
            jars.extend(sorted(self.jasper_lib_dir.rglob("*.jar")))
        if not jars:
            fallback = self.workspace_root / "vendor" / "jasper-lib"
            if fallback.exists():
                jars.extend(sorted(fallback.rglob("*.jar")))
        if not jars:
            vendor_dir = self.workspace_root / "vendor"
            if vendor_dir.exists():
                for candidate in sorted(vendor_dir.iterdir()):
                    if not candidate.is_dir():
                        continue
                    name = candidate.name.lower()
                    if "jasper" not in name:
                        continue
                    jars.extend(sorted(candidate.rglob("*.jar")))
        return jars
