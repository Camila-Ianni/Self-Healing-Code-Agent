# Self-Healing Code Agent

> 🎥 **Video demo (< 3 min):** `PASTE PUBLIC YOUTUBE LINK HERE BEFORE SUBMITTING`
>
> 🆔 **Codex Session ID (/feedback):** `019f824e-0a6a-7530-a6a1-4134a4093fd1`

Python CLI that transforms a failing test into a verifiable repair loop: executes tests, captures the stack trace, localizes the target source files, requests a correction from OpenAI, and **retains the changes only if the tests pass**. If the validation attempt fails, it automatically rolls back the changes.

**How Codex accelerated this project:** Codex generated the CLI skeleton, separated the core repair engine from the interface, created the demo, and helped implement the validation loop with rollback. This allowed me to focus my time on the agentic flow and creating a demo that judges can run immediately.

## 30-Second Demo

The example intentionally contains an error in `example/calculator.py`: `multiply(6, 7)` performs addition instead of multiplication.

```bash
git clone https://github.com/Camila-Ianni/Self-Healing-Code-Agent.git
cd Self-Healing-Code-Agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
export OPENAI_API_KEY="your_key"

# Recommended command for judges (no options need to be known):
python -m self_healing_agent.cli test ./example/test_calculator.py
```

Expected output:

```text
❌ Tests failed. Analyzing example/calculator.py with gpt-5.6…
✅ Repair validated. Patch applied in example/calculator.py (...)
```

Then `pytest -q example` turns green. To repeat the demo, change `return left * right` back to `return left + right`.

## Business Value & ROI

The Self-Healing Code Agent is not just a technical convenience tool; it is an enterprise-grade solution designed to optimize development and systems operation workflows.

### 📈 Business Impact & ROI
* **Drastic MTTR (Mean Time to Repair) Reduction**: Automatically analyzes and corrects regressions in CI pipelines within seconds, significantly reducing downtime for production services.
* **Direct Engineering Cost Savings (Man-Hours)**: Relieves developers from the tedium of debugging trivial syntax errors or common concurrency bugs (deadlocks, race conditions). This translates directly to saving thousands of dollars in engineering hours previously spent on mechanical debugging.
* **Operational Risk Mitigation (Sandbox Security)**: By enforcing test validation inside isolated environments (Docker or subprocesses with POSIX resource limits), it prevents runaway tests or destructive scripts from impacting shared integration infrastructure.
* **Automated Dual-Agent Audit**: The combination of a Fixer agent with an independent Reviewer agent prevents the introduction of performance bottlenecks or security vulnerabilities before any patch is committed.

## How it Works

```text
test command → stack trace → Fixer Agent → Reviewer Agent → diff + confirmation → tests
                                                         ├─ rejection: discard changes
                                                         └─ approval: temporary patch → pass: retain / fail: rollback
```

The architecture strictly follows a Model–View–Controller (MVC) separation of concerns:

- `model.py`: Immutable test evidence and deep parsing of Python and Go stack traces.
- `controller.py`: Orchestrates the Fixer, Reviewer, human approval, backups, and persistence.
- `view.py`: Rich terminal spinner, colorized diff, alerts, and CLI confirmation.
- `sandbox.py`: Temporary workspace copying and constrained Docker/subprocess environments for validation.

The **Fixer Agent** proposes the complete corrected file contents. Before writing any changes, the **Reviewer Agent** audits the proposal searching for security risks, infinite loops, performance regressions, data races, and deadlocks. Only an `APPROVE` verdict unlocks the colorized diff and user confirmation. Local backups are stored as hidden `.{filename}.bak` files in the same directory and are not version-controlled.

Use `--yes` to apply approved patches automatically without terminal confirmation (useful for automation pipelines).

## Concurrent Backend Demo (Go)

In addition to the calculator, `example/go_backend/` reproduces a typical race condition in a web server request counter. The test uses Go's race detector:

```bash
self-heal \
  --test-command "cd example/go_backend && go test -race ./..." \
  --source example/go_backend/counter.go
```

The Fixer must replace the unsafe concurrent access with correct synchronization; the Reviewer explicitly checks for race conditions and deadlocks before presenting the diff. This demo requires Go 1.22+ and a valid `OPENAI_API_KEY`.

### Asynchronous Market Integration

`example/go_market_aggregator/` simulates JSON responses from predictive market platforms. The bug deadlocks goroutines against an unbuffered channel while the `sync.WaitGroup` waits for them to finish; the test exposes a timeout with the exact diagnosis.

```bash
self-heal \
  --test-command "cd example/go_market_aggregator && go test ./..." \
  --source example/go_market_aggregator/aggregator.go
```

The expected repair correctly coordinates the channels and the `sync.WaitGroup` while preserving the concurrent JSON deserialization of the feeds.

## Validation Sandbox

Before overwriting any local file, the approved proposal is copied to a temporary directory and validated inside an ephemeral Docker container. This container runs with networking disabled, dropped Linux capabilities, `no-new-privileges`, a 768 MB memory limit, 2 CPUs, and a maximum of 256 processes. The only mounted volume is the temporary workspace copy—never your live working tree.

Build the sandbox image once:

```bash
docker build -t self-healing-sandbox:latest -f Dockerfile.sandbox .
```

If this image is not found, the CLI falls back automatically to a local subprocess sandbox that enforces strict POSIX limits (`resource.setrlimit`) to isolate execution. You can also supply a custom image via `--sandbox-image name:tag`.

## Configuration

The CLI uses the [OpenAI Responses API](https://developers.openai.com/api/docs/guides/text). By default, it uses `gpt-5.6`; this can be overridden without changing the code:

```bash
export OPENAI_MODEL="gpt-5.6"
self-heal --test-command "pytest -q"
```

Primary options:

```text
--test-command "pytest -q"       Test command to run and capture
--source path/to/module.py       File(s) the agent is allowed to modify
--model gpt-5.6                  OpenAI model name
--root .                         Project root directory
--sandbox docker                 Sandbox type: 'docker' or 'subprocess'
--commit                         Automatically commit the changes to Git with AI messages
--rollback                       Restore backups recursively from hidden .bak files
```

## Project Verification

```bash
pip install -e '.[dev]'
pytest -q
```

The internal unit tests validate exception capturing and safe target localization. End-to-end repairs require a valid `OPENAI_API_KEY`, so they are mocked during local test suite runs.

## Security & Guardrails

- The model never receives shell execution permissions; it only returns the complete content of the target files.
- Two independent model calls separate the proposal (Fixer) from the safety audit (Reviewer).
- The diff is displayed and requires explicit user confirmation before writing; `--yes` is the only override.
- The proposed code runs strictly inside the Docker/subprocess sandbox before being persisted locally.
- The working tree is only permanently updated after a successful test run on the modified code.

## Use of Codex and GPT-5.6

Codex accelerated the creation of the CLI skeleton, test suite, rollback workflow, and this documentation. GPT-5.6 is the reasoning engine behind the product: it receives the source code and failing test evidence, proposes a minimal correct patch, and the agent validates it automatically.

## Video

Paste the public YouTube link at the top of this README before submitting.
