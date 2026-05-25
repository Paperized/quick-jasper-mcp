from __future__ import annotations

import json
import sys
import shutil
import traceback
import re
import subprocess
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .java_bridge import JavaBridge, JavaBridgeError


app = FastMCP("jrxml-validation-mcp")
settings = Settings.from_env()
bridge = JavaBridge(settings)


def _safe_name(file_name: str, fallback: str) -> str:
    candidate = Path(file_name).name.strip()
    return candidate or fallback


def _safe_relative_path(path_value: str, fallback: str) -> Path:
    raw = Path(path_value.strip()) if path_value else Path(fallback)
    if raw.is_absolute():
        raw = Path(raw.name)
    normalized = Path(*[part for part in raw.parts if part not in {"", ".", ".."}])
    if not normalized.parts:
        return Path(fallback)
    return normalized


def _copy_resource_entry(resource_path: str, run_dir: Path) -> list[str]:
    source = Path(resource_path).expanduser()
    if not source.exists():
        return [f"MISSING:{source}"]
    if source.is_file():
        target = run_dir / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return [str(target)]
    target_dir = run_dir / source.name
    shutil.copytree(source, target_dir, dirs_exist_ok=True)
    return [str(p) for p in target_dir.rglob("*") if p.is_file()]


def _normalize_resource_paths(resource_paths: list[str] | str | None) -> list[str]:
    if resource_paths is None:
        return []
    if isinstance(resource_paths, str):
        candidate = resource_paths.strip()
        return [candidate] if candidate else []
    normalized: list[str] = []
    for item in resource_paths:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            normalized.append(text)
    return normalized


def _normalize_mock_data(mock_data: str | dict | list | None) -> str | None:
    if mock_data is None:
        return None
    if isinstance(mock_data, str):
        return mock_data
    if isinstance(mock_data, (dict, list)):
        return json.dumps(mock_data, ensure_ascii=False)
    return str(mock_data)


def _infer_mock_data_type(mock_data_type: str, mock_data: str | None) -> Literal["none", "json", "xml"]:
    lowered = (mock_data_type or "none").strip().lower()
    if lowered in {"none", "json", "xml"}:
        if lowered != "none":
            return lowered  # explicit
    if mock_data is None:
        return "none"
    stripped = mock_data.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        return "json"
    if stripped.startswith("<"):
        return "xml"
    return "none"


def _infer_template_and_resources_from_jrxml_path(
    jrxml_path: str,
    jrxml_relative_path: str | None,
    resource_paths: list[str] | str | None,
) -> tuple[str | None, list[str] | str | None]:
    path_obj = Path(jrxml_path)
    inferred_relative = jrxml_relative_path
    inferred_resources = resource_paths
    if inferred_relative is None and path_obj.parent.name.lower() == "templates":
        inferred_relative = f"templates/{path_obj.name}"
    if inferred_resources is None and path_obj.parent.name.lower() == "templates":
        candidate_resources = path_obj.parent.parent / "resources"
        if candidate_resources.exists():
            inferred_resources = [str(candidate_resources)]
    return inferred_relative, inferred_resources


def _materialize_resources(
    run_dir: Path,
    resource_paths: list[str] | None,
    resources_inline: dict[str, str] | None,
) -> list[str]:
    copied: list[str] = []
    for resource_path in resource_paths or []:
        copied.extend(_copy_resource_entry(resource_path, run_dir))
    for relative_path, content in (resources_inline or {}).items():
        safe_rel = _safe_relative_path(relative_path, "resource.properties")
        target = run_dir / safe_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        copied.append(str(target))
    return copied


def _normalize_bundle_paths(jrxml_text: str) -> str:
    return jrxml_text.replace("../resources/", "resources/").replace("..\\resources\\", "resources\\")


def _extract_jrxml_fields(jrxml_text: str) -> list[str]:
    pattern = re.compile(r'<field\s+name="([^"]+)"')
    return pattern.findall(jrxml_text)


def _flatten_json_values(value: object, path: tuple[str, ...] = ()) -> dict[tuple[str, ...], object]:
    out: dict[tuple[str, ...], object] = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            out.update(_flatten_json_values(nested, (*path, str(key))))
        return out
    out[path] = value
    return out


def _project_mock_record(source: dict[str, object], field_names: list[str]) -> dict[str, object]:
    flat = _flatten_json_values(source)
    joined_map: dict[str, object] = {}
    leaf_map: dict[str, list[object]] = {}
    for path, value in flat.items():
        if not path:
            continue
        joined_key = "_".join(path)
        joined_map[joined_key] = value
        leaf_key = path[-1]
        leaf_map.setdefault(leaf_key, []).append(value)

    projected: dict[str, object] = {}
    for field in field_names:
        if field in joined_map:
            projected[field] = joined_map[field]
            continue
        values = leaf_map.get(field, [])
        if len(values) == 1:
            projected[field] = values[0]
    return projected


def _prepare_json_mock_payload(jrxml_text: str, mock_data: str) -> str:
    try:
        payload = json.loads(mock_data)
    except Exception:
        return mock_data

    fields = _extract_jrxml_fields(jrxml_text)
    if not fields:
        return mock_data

    if isinstance(payload, dict):
        if isinstance(payload.get("rows"), list):
            return json.dumps(payload, ensure_ascii=False)
        source = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(source, dict):
            return mock_data
        projected = _project_mock_record(source, fields)
        if not projected:
            projected = source
        return json.dumps({"rows": [projected]}, ensure_ascii=False)

    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        if rows:
            return json.dumps({"rows": rows}, ensure_ascii=False)

    return mock_data


def _new_run(keep_files: bool) -> Path:
    run_dir = settings.create_run_dir()
    if not keep_files and not settings.retain_runs:
        run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _finalize_run(run_dir: Path, keep_files: bool) -> None:
    if keep_files or settings.retain_runs:
        return
    shutil.rmtree(run_dir, ignore_errors=True)


def _find_maven_command(explicit_command: str | None = None) -> str | None:
    if explicit_command:
        return explicit_command
    candidates = [
        settings.workspace_root / "mvnw.cmd",
        settings.workspace_root / "mvnw",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return shutil.which("mvn")


def _count_jars(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*.jar"))


@app.tool()
def bootstrap_jasper_deps(
    target_dir: str = "vendor/jasper-lib",
    clean_target: bool = True,
    maven_command: str | None = None,
    pom_path: str = "pom.xml",
) -> dict:
    """
    Bootstrap JasperReports runtime jars into a local folder using Maven dependency copy.
    """
    mvn_cmd = _find_maven_command(maven_command)
    pom = (settings.workspace_root / pom_path).resolve() if not Path(pom_path).is_absolute() else Path(pom_path)
    target = (settings.workspace_root / target_dir).resolve() if not Path(target_dir).is_absolute() else Path(target_dir)

    if mvn_cmd is None:
        return {
            "success": False,
            "error": "Maven command not found. Install Maven or provide maven_command.",
            "hint": "Expected mvn in PATH or mvnw(.cmd) in workspace root.",
            "target_dir": str(target),
            "pom_path": str(pom),
        }
    if not pom.exists():
        return {
            "success": False,
            "error": f"pom.xml not found at {pom}",
            "target_dir": str(target),
            "pom_path": str(pom),
        }

    try:
        # Always enforce a fresh bootstrap to avoid mixed jar versions in classpath.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        target.mkdir(parents=True, exist_ok=True)

        cmd = [
            mvn_cmd,
            "-q",
            "-f",
            str(pom),
            "dependency:copy-dependencies",
            "-DincludeScope=runtime",
            f"-DoutputDirectory={target}",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        jar_count = _count_jars(target)
        return {
            "success": proc.returncode == 0,
            "command": cmd,
            "pom_path": str(pom),
            "target_dir": str(target),
            "jar_count": jar_count,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "exit_code": proc.returncode,
        }
    except Exception:
        return {
            "success": False,
            "command": [],
            "pom_path": str(pom),
            "target_dir": str(target),
            "jar_count": _count_jars(target),
            "stdout": "",
            "stderr": "Unexpected error while bootstrapping Jasper dependencies.",
            "stack_trace": traceback.format_exc(),
            "exit_code": -1,
        }


@app.tool()
def usage_guide() -> dict:
    """
    Returns exhaustive usage instructions intended for LLM agents and human operators.
    """
    return {
        "name": "JRXML Validation/Preview MCP - Usage Guide",
        "out_of_the_box_defaults": {
            "workspace": str(settings.workspace_root),
            "storage": str(settings.storage_root),
            "jasper_lib_dir": str(settings.jasper_lib_dir) if settings.jasper_lib_dir else None,
            "auto_discovery": "If JASPER_LIB_DIR is not set, server auto-uses <workspace>/vendor/jasper-lib when present.",
            "java_requirement": "Java 17+ required. javac is required the first time to compile helper class.",
        },
        "setup_from_clone": [
            "1) Install Java 17+ and Python 3.11+.",
            "2) Install python deps: python -m pip install -e .",
            "3) Populate Jasper jars: call MCP tool bootstrap_jasper_deps() or run Maven copy-dependencies.",
            "4) Start server: python -m jrxml_mcp_server.server",
            "5) Optional: set JASPER_LIB_DIR if jars are not in vendor/jasper-lib.",
        ],
        "vendor_policy": {
            "recommended": "Do not commit vendor/jasper-lib jars to git (binary/transitive artifacts).",
            "commit": ["pom.xml", "python source files"],
            "ignore": ["vendor/jasper-lib/", ".jrxml_mcp/"],
        },
        "tools": {
            "bootstrap_jasper_deps": {
                "purpose": "Download/copy Jasper runtime jars from pom.xml to local target folder.",
                "required_inputs": [],
                "optional_inputs": [
                    "target_dir",
                    "clean_target",
                    "maven_command",
                    "pom_path",
                ],
                "returns": [
                    "success",
                    "target_dir",
                    "jar_count",
                    "stdout",
                    "stderr",
                    "exit_code",
                ],
            },
                "validate_jrxml": {
                "purpose": "Compile-only gatekeeper for JRXML syntax/schema compatibility.",
                "required_inputs": ["jrxml"],
                    "optional_inputs": [
                        "jrxml_path",
                        "template_name",
                        "jrxml_relative_path",
                        "resource_paths",
                        "resources_inline",
                        "keep_files",
                    ],
                    "input_tolerance": "resource_paths accepts string or list of strings.",
                "returns": [
                    "success",
                    "stdout",
                    "stderr",
                    "stack_trace",
                    "exit_code",
                    "run_dir",
                    "jrxml_path",
                    "resources",
                ],
            },
            "render_preview": {
                "purpose": "Compile + fill + render visual preview PNG/PDF.",
                "required_inputs": ["jrxml OR jrxml_path"],
                "optional_inputs": [
                    "jrxml_path",
                    "template_name",
                    "jrxml_relative_path",
                    "output_format",
                    "mock_data",
                    "mock_data_type",
                    "resource_paths",
                    "resources_inline",
                        "output_name",
                        "page_index",
                        "report_parameters",
                        "locale",
                        "normalize_resource_bundle_paths",
                        "keep_files",
                    ],
                "input_tolerance": "mock_data accepts string/object/list; resource_paths accepts string or list.",
                "llm_note": "If JRXML defines <parameter>, pass values in report_parameters.",
                "returns": [
                    "success",
                    "stdout",
                    "stderr",
                    "stack_trace",
                    "exit_code",
                    "run_dir",
                    "jrxml_path",
                    "output_path",
                    "resources",
                ],
            },
        },
        "resource_handling": {
            "when_needed": "Use resource_paths/resources_inline when JRXML references bundles/fonts/images/sub-assets.",
            "recommended_layout": "Set jrxml_relative_path to preserve template layout, e.g. templates/report.jrxml.",
            "bundle_support": "Renderer can normalize ../resources/... bundle paths in temporary JRXML copy.",
        },
        "mock_data_support": {
            "none": "Uses JREmptyDataSource(1). Useful for static templates.",
            "json": "Supports flat and nested JSON. Accepts JSON string or object/list input.",
            "xml": "Raw XML accepted and loaded with JRXmlDataSource.",
        },
        "typical_agent_loop": [
            "1) validate_jrxml -> if fail, inspect stack_trace.",
            "2) patch JRXML/resources/data bindings.",
            "3) validate_jrxml again until success.",
            "4) render_preview to PNG/PDF (include report_parameters when JRXML has <parameter>).",
            "5) inspect output_path visually and iterate.",
        ],
        "input_examples": {
            "bootstrap": {
                "tool_call": "bootstrap_jasper_deps()",
                "expected_result": "target_dir contains Jasper jars and transitive dependencies.",
            },
            "validate": {
                "jrxml_relative_path": "templates/stampa.jrxml",
                "resource_paths": [r"C:\path\to\resources"],
            },
            "render_png_with_json": {
                "output_format": "png",
                "mock_data_type": "json",
                "report_parameters": {"EXAMPLE_PARAM": "value"},
                "locale": "it_IT",
                "jrxml_relative_path": "templates/stampa.jrxml",
                "resource_paths": [r"C:\path\to\resources"],
                "mock_data_note": "Can be flat or nested JSON.",
            },
            "render_pdf_with_xml": {
                "output_format": "pdf",
                "mock_data_type": "xml",
                "jrxml_relative_path": "templates/stampa.jrxml",
            },
        },
        "troubleshooting": [
            "MissingResourceException: pass resource_paths and jrxml_relative_path preserving templates/resources structure.",
            "ClassNotFound / No Jasper jars: populate vendor/jasper-lib or set JASPER_LIB_DIR.",
            "Render success but empty layout: verify JRXML field bindings and mock payload field names.",
        ],
    }


@app.tool()
def validate_jrxml(
    jrxml: str | None = None,
    jrxml_path: str | None = None,
    template_name: str = "template.jrxml",
    jrxml_relative_path: str | None = None,
    resource_paths: list[str] | str | None = None,
    resources_inline: dict[str, str] | None = None,
    keep_files: bool = True,
) -> dict:
    """
    Validate JRXML syntax/compatibility with JasperReports 7.x.

    Required input:
    - jrxml OR jrxml_path:
      - jrxml: full JRXML text content.
      - jrxml_path: absolute/local path to existing JRXML file.

    Optional input:
    - template_name: fallback file name used in run folder.
    - jrxml_relative_path: relative path to preserve project layout in run folder.
      Example: "templates/report.jrxml".
    - resource_paths: one path string or list of path strings (files/folders) to copy in run folder.
      Use this when JRXML references external assets (resource bundles, fonts, images).
    - resources_inline: inline resources map {relative_path: file_content}.
      Useful when caller already has resource text and wants no filesystem lookup.
    - keep_files: keep generated run folder for inspection.

    Returns:
    - success, stdout, stderr, stack_trace, exit_code
    - run_dir and jrxml_path used for the validation run
    - resources copied/written in run folder
    """
    if (jrxml is None or not jrxml.strip()) and not jrxml_path:
        return {
            "success": False,
            "mode": "validate",
            "stdout": "",
            "stderr": "Missing input: provide jrxml text or jrxml_path.",
            "stack_trace": "",
            "exit_code": -1,
        }

    effective_jrxml = jrxml
    effective_relative = jrxml_relative_path
    effective_resource_paths = resource_paths

    if (effective_jrxml is None or not effective_jrxml.strip()) and jrxml_path:
        source_path = Path(jrxml_path)
        if not source_path.exists():
            return {
                "success": False,
                "mode": "validate",
                "stdout": "",
                "stderr": f"JRXML path not found: {source_path}",
                "stack_trace": "",
                "exit_code": -1,
            }
        effective_jrxml = source_path.read_text(encoding="utf-8")
        effective_relative, effective_resource_paths = _infer_template_and_resources_from_jrxml_path(
            jrxml_path=str(source_path),
            jrxml_relative_path=jrxml_relative_path,
            resource_paths=resource_paths,
        )

    run_dir = _new_run(keep_files)
    jrxml_rel = _safe_relative_path(effective_relative or template_name, "template.jrxml")
    jrxml_path = run_dir / jrxml_rel
    jrxml_path.parent.mkdir(parents=True, exist_ok=True)
    jrxml_path.write_text(effective_jrxml or "", encoding="utf-8")
    normalized_resource_paths = _normalize_resource_paths(effective_resource_paths)
    copied_resources = _materialize_resources(run_dir, normalized_resource_paths, resources_inline)
    try:
        proc = bridge.run(["validate", str(jrxml_path)])
        success = proc.returncode == 0
        result = {
            "success": success,
            "mode": "validate",
            "run_dir": str(run_dir),
            "jrxml_path": str(jrxml_path),
            "resources": copied_resources,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "stack_trace": "" if success else proc.stderr,
            "exit_code": proc.returncode,
        }
        if not success and "stack_trace" not in result:
            result["stack_trace"] = proc.stderr
        return result
    except JavaBridgeError as exc:
        return {
            "success": False,
            "mode": "validate",
            "run_dir": str(run_dir),
            "jrxml_path": str(jrxml_path),
            "resources": copied_resources,
            "stdout": "",
            "stderr": str(exc),
            "stack_trace": traceback.format_exc(),
            "exit_code": -1,
        }
    except Exception:
        return {
            "success": False,
            "mode": "validate",
            "run_dir": str(run_dir),
            "jrxml_path": str(jrxml_path),
            "resources": copied_resources,
            "stdout": "",
            "stderr": "Unexpected error in validate_jrxml",
            "stack_trace": traceback.format_exc(),
            "exit_code": -1,
        }
    finally:
        _finalize_run(run_dir, keep_files)


@app.tool()
def render_preview(
    jrxml: str | None = None,
    jrxml_path: str | None = None,
    template_name: str = "template.jrxml",
    jrxml_relative_path: str | None = None,
    output_format: Literal["png", "pdf"] = "png",
    mock_data: str | dict | list | None = None,
    mock_data_type: Literal["none", "json", "xml"] = "none",
    resource_paths: list[str] | str | None = None,
    resources_inline: dict[str, str] | None = None,
    output_name: str | None = None,
    page_index: int = 0,
    report_parameters: dict[str, object] | None = None,
    pdf_metadata: dict[str, object] | None = None,
    locale: str = "it_IT",
    normalize_resource_bundle_paths: bool = True,
    keep_files: bool = True,
) -> dict:
    """
    Compile + fill + render JRXML to PNG/PDF with optional mock data.

    Required input:
    - jrxml OR jrxml_path:
      - jrxml: full JRXML text content.
      - jrxml_path: absolute/local path to existing JRXML file.

    Optional input:
    - template_name: fallback file name used in run folder.
    - jrxml_relative_path: relative path to preserve template/resources relative references.
      Example: "templates/report.jrxml" plus resource folder copied as "resources/...".
    - output_format: "png" or "pdf".
    - mock_data / mock_data_type:
      - none: render with JREmptyDataSource(1)
      - json: accepts flat or nested JSON; nested structures are projected to JRXML field names.
        mock_data can be JSON string OR object/list input.
      - xml: raw XML content for JRXmlDataSource.
    - resource_paths / resources_inline:
      resource_paths accepts one string path or list of string paths.
      resources_inline accepts {relative_path: file_content}.
    - output_name: output file name.
    - page_index: page index for PNG rendering.
    - report_parameters: map for JRXML <parameter> values (passed to Jasper fill parameters).
    - pdf_metadata: map for PDF metadata/export settings.
      Supported keys: title, author, subject, keywords, creator, tagged, tag_language,
      display_metadata_title, compressed, force_linebreak_policy.
    - locale: locale passed as REPORT_LOCALE (default: it_IT).
    - normalize_resource_bundle_paths:
      if true, normalizes "../resources/..." -> "resources/..." in temporary JRXML copy.
    - keep_files: keep generated run folder for inspection.

    Returns:
    - success, stdout, stderr, stack_trace, exit_code
    - run_dir, jrxml_path, output_path
    - resources copied/written in run folder
    """
    if (jrxml is None or not jrxml.strip()) and not jrxml_path:
        return {
            "success": False,
            "mode": "render",
            "stdout": "",
            "stderr": "Missing input: provide jrxml text or jrxml_path.",
            "stack_trace": "",
            "exit_code": -1,
        }

    effective_jrxml = jrxml
    effective_relative = jrxml_relative_path
    effective_resource_paths = resource_paths

    if (effective_jrxml is None or not effective_jrxml.strip()) and jrxml_path:
        source_path = Path(jrxml_path)
        if not source_path.exists():
            return {
                "success": False,
                "mode": "render",
                "stdout": "",
                "stderr": f"JRXML path not found: {source_path}",
                "stack_trace": "",
                "exit_code": -1,
            }
        effective_jrxml = source_path.read_text(encoding="utf-8")
        effective_relative, effective_resource_paths = _infer_template_and_resources_from_jrxml_path(
            jrxml_path=str(source_path),
            jrxml_relative_path=jrxml_relative_path,
            resource_paths=resource_paths,
        )

    run_dir = _new_run(keep_files)
    jrxml_rel = _safe_relative_path(effective_relative or template_name, "template.jrxml")
    jrxml_target_path = run_dir / jrxml_rel
    jrxml_target_path.parent.mkdir(parents=True, exist_ok=True)
    jrxml_to_write = _normalize_bundle_paths(effective_jrxml or "") if normalize_resource_bundle_paths else (effective_jrxml or "")
    jrxml_target_path.write_text(jrxml_to_write, encoding="utf-8")
    normalized_resource_paths = _normalize_resource_paths(effective_resource_paths)
    copied_resources = _materialize_resources(run_dir, normalized_resource_paths, resources_inline)

    normalized_mock_data = _normalize_mock_data(mock_data)
    data_path = "-"
    data_type = _infer_mock_data_type(mock_data_type, normalized_mock_data)
    if normalized_mock_data is not None and data_type in {"json", "xml"}:
        data_content = normalized_mock_data
        if data_type == "json":
            data_content = _prepare_json_mock_payload(effective_jrxml or "", normalized_mock_data)
        extension = ".json" if data_type == "json" else ".xml"
        data_file = run_dir / f"mock_data{extension}"
        data_file.write_text(data_content, encoding="utf-8")
        data_path = str(data_file)

    default_output_name = f"preview.{output_format}"
    output_path = run_dir / _safe_name(output_name or default_output_name, default_output_name)
    params_path = "-"
    if report_parameters:
        params_file = run_dir / "report_parameters.json"
        params_file.write_text(json.dumps(report_parameters, ensure_ascii=False), encoding="utf-8")
        params_path = str(params_file)
    pdf_metadata_path = "-"
    if pdf_metadata:
        pdf_metadata_file = run_dir / "pdf_metadata.json"
        pdf_metadata_file.write_text(json.dumps(pdf_metadata, ensure_ascii=False), encoding="utf-8")
        pdf_metadata_path = str(pdf_metadata_file)
    try:
        proc = bridge.run(
            [
                "render",
                str(jrxml_target_path),
                str(output_path),
                output_format,
                data_path,
                data_type,
                str(page_index),
                locale,
                params_path,
                pdf_metadata_path,
            ],
            extra_classpath=[str(run_dir)],
        )
        success = proc.returncode == 0
        return {
            "success": success,
            "mode": "render",
            "run_dir": str(run_dir),
            "jrxml_path": str(jrxml_target_path),
            "output_path": str(output_path),
            "output_format": output_format,
            "mock_data_type": data_type,
            "report_parameters": report_parameters or {},
            "pdf_metadata": pdf_metadata or {},
            "resources": copied_resources,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "stack_trace": "" if success else proc.stderr,
            "exit_code": proc.returncode,
        }
    except JavaBridgeError as exc:
        return {
            "success": False,
            "mode": "render",
            "run_dir": str(run_dir),
            "jrxml_path": str(jrxml_target_path),
            "output_path": str(output_path),
            "output_format": output_format,
            "mock_data_type": data_type,
            "report_parameters": report_parameters or {},
            "pdf_metadata": pdf_metadata or {},
            "resources": copied_resources,
            "stdout": "",
            "stderr": str(exc),
            "stack_trace": traceback.format_exc(),
            "exit_code": -1,
        }
    except Exception:
        return {
            "success": False,
            "mode": "render",
            "run_dir": str(run_dir),
            "jrxml_path": str(jrxml_target_path),
            "output_path": str(output_path),
            "output_format": output_format,
            "mock_data_type": data_type,
            "report_parameters": report_parameters or {},
            "pdf_metadata": pdf_metadata or {},
            "resources": copied_resources,
            "stdout": "",
            "stderr": "Unexpected error in render_preview",
            "stack_trace": traceback.format_exc(),
            "exit_code": -1,
        }
    finally:
        _finalize_run(run_dir, keep_files)


def main() -> None:
    if any(arg in {"-h", "--help", "-help"} for arg in sys.argv[1:]):
        print(
            "\n".join(
                [
                    "JRXML MCP Server",
                    "",
                    "Usage:",
                    "  jrxml-mcp-server            Start MCP server on stdio transport",
                    "  jrxml-mcp-server -help      Show this help",
                    "",
                    "Goal:",
                    "  Validate and render externally generated JRXML files (JasperReports 7.x),",
                    "  returning compiler/render errors to drive iterative AI fixes.",
                    "",
                    "Environment:",
                    "  JAVA_HOME                   Java 17+ home (optional if java/javac are in PATH)",
                    "  JASPER_LIB_DIR              Folder containing JasperReports 7.x jars + runtime deps",
                    "  JASPER_EXTRA_CLASSPATH      Extra classpath entries separated by os path separator",
                    "  JRXML_MCP_WORKSPACE         Workspace root (default: current directory)",
                    "  JRXML_MCP_STORAGE           Run/output root (default: <workspace>/.jrxml_mcp)",
                    "  JRXML_MCP_RETAIN_RUNS       true/false to keep run artifacts",
                    "",
                    "First setup on a new machine:",
                    "  1) python -m pip install -e .",
                    "  2) Call MCP tool bootstrap_jasper_deps()",
                    "     (or run Maven: dependency:copy-dependencies to vendor/jasper-lib)",
                    "  3) Start server.",
                    "",
                    "What the caller should pass to MCP tools:",
                    "  1) Pass JRXML as string in `jrxml` OR file path in `jrxml_path`.",
                    "  2) If JRXML uses relative paths (templates/resources), pass:",
                    "     - jrxml_relative_path (e.g. templates/my-report.jrxml)",
                    "     - resource_paths as string or list with resource folders/files.",
                    "  3) For preview use render_preview with output_format png/pdf.",
                    "     If JRXML has <parameter>, pass report_parameters.",
                    "  4) For data mocking:",
                    "     - json: flat or nested object/list/string accepted, auto-mapped to JRXML fields.",
                    "     - xml: raw xml text.",
                    "     - none: empty datasource.",
                    "",
                    "Run folder behavior:",
                    "  - Each request creates: <storage>/runs/<timestamp>_<id>/...",
                    "  - Response returns exact run_dir/jrxml_path/output_path.",
                    "  - Set keep_files=false and JRXML_MCP_RETAIN_RUNS=false to auto-clean.",
                    "",
                    "Error feedback loop:",
                    "  - On failure tools return stderr + stack_trace + exit_code.",
                    "  - Feed this output back to the LLM to patch JRXML and retry.",
                    "",
                    "Recommended iterative workflow for LLM agents:",
                    "  1) Call validate_jrxml.",
                    "  2) If failed: inspect stack_trace, patch JRXML, retry validate.",
                    "  3) Once validate succeeds: call render_preview.",
                    "  4) If render fails: fix resources/mock/expressions and retry.",
                    "  5) When render succeeds: inspect output_path preview visually.",
                    "",
                    "MCP tools:",
                    "  bootstrap_jasper_deps(...)",
                    "    Purpose: fetch Jasper runtime deps to local vendor folder.",
                    "    Key outputs: target_dir, jar_count, stdout/stderr.",
                    "",
                    "  validate_jrxml(...)",
                    "    Purpose: compile-only validation gatekeeper.",
                    "    Key inputs: jrxml, jrxml_relative_path, resource_paths/resources_inline.",
                    "",
                    "  render_preview(...)",
                    "    Purpose: visual verification (png/pdf) with optional mock data.",
                    "    Key inputs: jrxml, output_format, mock_data/mock_data_type,",
                    "                jrxml_relative_path, resource_paths/resources_inline, locale.",
                    "",
                    "Example invocation pattern (conceptual):",
                    "  - validate_jrxml(jrxml=<text>, jrxml_relative_path='templates/r.jrxml',",
                    "                   resource_paths=['C:\\\\...\\\\resources'])",
                    "  - validate_jrxml(jrxml_path='C:\\\\...\\\\templates\\\\r.jrxml')",
                    "  - render_preview(jrxml=<text>, output_format='png', mock_data_type='json',",
                    "                   mock_data='{...}', report_parameters={'P1':'V1'}, locale='it_IT',",
                    "                   jrxml_relative_path='templates/r.jrxml',",
                    "                   resource_paths=['C:\\\\...\\\\resources'])",
                    "  - render_preview(jrxml_path='C:\\\\...\\\\templates\\\\r.jrxml', output_format='png')",
                ]
            )
        )
        return
    app.run()


if __name__ == "__main__":
    main()


