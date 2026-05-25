from __future__ import annotations

import os
import shutil
import subprocess
import zipfile
from pathlib import Path

from .config import Settings


class JavaBridgeError(RuntimeError):
    pass


class JavaBridge:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.settings.ensure_dirs()

    def _find_bin(self, name: str) -> str:
        java_home = os.getenv("JAVA_HOME")
        if java_home:
            candidate = Path(java_home) / "bin" / (f"{name}.exe" if os.name == "nt" else name)
            if candidate.exists():
                return str(candidate)
        found = shutil.which(name)
        if found:
            return found
        raise JavaBridgeError(f"Executable '{name}' not found. Configure JAVA_HOME or PATH.")

    def _classpath_entries(self, extra_entries: list[str] | None = None) -> list[str]:
        entries: list[str] = [str(self.settings.java_build_dir)]
        jars = self.settings.collect_jasper_jars()
        entries.extend(str(jar) for jar in jars)
        entries.extend(self.settings.jasper_extra_classpath)
        entries.extend(extra_entries or [])
        if len(entries) <= 1:
            raise JavaBridgeError(
                "No JasperReports jars found. Set JASPER_LIB_DIR (folder with JasperReports 7.x jars)."
            )
        return entries

    def _classpath_value(self, extra_entries: list[str] | None = None) -> str:
        return os.pathsep.join(self._classpath_entries(extra_entries))

    @staticmethod
    def _classfile_major_to_java(major: int) -> int:
        if major < 45:
            return major
        return major - 44

    @staticmethod
    def _read_class_major_from_bytes(payload: bytes) -> int | None:
        if len(payload) < 8:
            return None
        if payload[0:4] != b"\xCA\xFE\xBA\xBE":
            return None
        return int.from_bytes(payload[6:8], byteorder="big", signed=False)

    def _detect_java_runtime_major(self) -> int:
        java = self._find_bin("java")
        proc = subprocess.run([java, "-version"], capture_output=True, text=True)
        output = f"{proc.stdout}\n{proc.stderr}"
        for token in output.replace('"', " ").split():
            if token and token[0].isdigit():
                head = token.split(".", 1)[0]
                if head.isdigit():
                    return int(head)
        raise JavaBridgeError(f"Unable to detect java runtime version from `java -version` output:\n{output}")

    def _max_required_java_from_jars(self, jar_paths: list[str]) -> tuple[int | None, str | None]:
        max_major: int | None = None
        offender: str | None = None
        for jar_path in jar_paths:
            jar = Path(jar_path)
            if not jar.exists() or jar.suffix.lower() != ".jar":
                continue
            try:
                with zipfile.ZipFile(jar) as zf:
                    for info in zf.infolist():
                        if not info.filename.endswith(".class"):
                            continue
                        # Ignore multi-release classes: entries under META-INF/versions/<N>/
                        # are only visible on runtimes >= N and do not define the minimum runtime.
                        if info.filename.startswith("META-INF/versions/"):
                            continue
                        # Ignore JPMS descriptor: module-info.class does not affect Java 8 runtime for class loading.
                        if info.filename.endswith("module-info.class"):
                            continue
                        with zf.open(info, "r") as class_file:
                            major = self._read_class_major_from_bytes(class_file.read(8))
                        if major is None:
                            continue
                        if max_major is None or major > max_major:
                            max_major = major
                            offender = f"{jar}:{info.filename}"
            except Exception:
                continue
        if max_major is None:
            return None, None
        return self._classfile_major_to_java(max_major), offender

    def _ensure_runtime_compatibility(self) -> None:
        classpath_entries = self._classpath_entries()
        required_java, offender = self._max_required_java_from_jars(classpath_entries)
        if required_java is None:
            return
        runtime_java = self._detect_java_runtime_major()
        if runtime_java < required_java:
            details = f" Offending class: {offender}." if offender else ""
            raise JavaBridgeError(
                "Java runtime too old for configured Jasper classpath. "
                f"Required Java {required_java}+ (class file {required_java + 44}.0), found Java {runtime_java}.{details}"
            )

    def ensure_compiled(self) -> None:
        self._ensure_runtime_compatibility()
        source = self.settings.java_source_path
        if not source.exists():
            raise JavaBridgeError(f"Java helper not found at: {source}")
        class_file = self.settings.java_build_dir / "itas" / "jrxml" / "JrxmlToolRunner.class"
        runtime_java = self._detect_java_runtime_major()
        if class_file.exists():
            try:
                major = self._read_class_major_from_bytes(class_file.read_bytes()[:8])
                compiled_for = self._classfile_major_to_java(major) if major else None
            except Exception:
                compiled_for = None
            if compiled_for is not None and compiled_for > runtime_java:
                class_file.unlink(missing_ok=True)
            elif class_file.stat().st_mtime >= source.stat().st_mtime:
                return
        javac = self._find_bin("javac")
        cmd = [
            javac,
            "-encoding",
            "UTF-8",
            "-source",
            "8",
            "-target",
            "8",
            "-cp",
            self._classpath_value(),
            "-d",
            str(self.settings.java_build_dir),
            str(source),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise JavaBridgeError(f"Java helper compilation failed.\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}")

    def run(self, args: list[str], extra_classpath: list[str] | None = None) -> subprocess.CompletedProcess[str]:
        self.ensure_compiled()
        java = self._find_bin("java")
        cmd = [
            java,
            "-Djava.awt.headless=true",
            "-cp",
            self._classpath_value(extra_classpath),
            "itas.jrxml.JrxmlToolRunner",
            *args,
        ]
        return subprocess.run(cmd, capture_output=True, text=True)
