# quick-jasper-mcp

MCP tool to validate JRXML and render Jasper previews (`png`/`pdf`).

JasperReports project: https://github.com/Jaspersoft/jasperreports

## Quick Setup

### Minimum requirements

- Python 3.11+
- Java JDK 8+ (`java` and `javac` in `PATH`)
- Internet access for first dependency download

### Fast setup (recommended)

Linux / WSL:

```bash
./scripts/setup.sh
```

Windows PowerShell:

```powershell
./scripts/setup.ps1
```

What setup does:

1. creates `.venv` if missing
2. installs Python dependencies
3. runs a fresh Jasper bootstrap into `vendor/jasper-lib`

## MCP Server Config (example)

Linux / WSL:

```toml
[mcp_servers.jrxml_validation]
command = "python3"
args = ["-m", "jrxml_mcp_server.server"]
cwd = "/path/to/quick-jasper-mcp"

[mcp_servers.jrxml_validation.env]
JRXML_MCP_WORKSPACE = "/path/to/quick-jasper-mcp"
JRXML_MCP_STORAGE = "/path/to/quick-jasper-mcp/.jrxml_mcp"
JASPER_LIB_DIR = "/path/to/quick-jasper-mcp/vendor/jasper-lib"
JRXML_MCP_RETAIN_RUNS = "true"
```

Windows:

```toml
[mcp_servers.jrxml_validation]
command = "python"
args = ["-m", "jrxml_mcp_server.server"]
cwd = "C:\\path\\to\\quick-jasper-mcp"

[mcp_servers.jrxml_validation.env]
JRXML_MCP_WORKSPACE = "C:\\path\\to\\quick-jasper-mcp"
JRXML_MCP_STORAGE = "C:\\path\\to\\quick-jasper-mcp\\.jrxml_mcp"
JASPER_LIB_DIR = "C:\\path\\to\\quick-jasper-mcp\\vendor\\jasper-lib"
JRXML_MCP_RETAIN_RUNS = "true"
```

## Standard Usage Flow

1. `validate_jrxml(...)`
2. fix JRXML/resources if needed
3. `render_preview(...)`
4. save final outputs to `.quick-jasper-mcp/` (unless user explicitly asks another path)

Use `jrxml_relative_path` + `resource_paths` when template/resources use relative paths.

## Use The Included SKILL (optional, recommended)

This repo ships a reusable skill for LLM agents:

- `skills/jasper-mcp-usage/SKILL.md`

If your agent platform supports custom skills, import/copy that file so agents can use a consistent flow and troubleshooting defaults.

## Essential Notes

- `bootstrap_jasper_deps()` from MCP is always **clean** (target folder is reset each run).
- If helper classes are incompatible with current Java runtime, they are automatically recompiled.
- JVM runs in headless mode (works in WSL/server environments).
