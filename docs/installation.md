# Installation and daily workflow

## Prerequisites

Install the official [`claude-mem`](https://github.com/thedotmack/claude-mem#quick-start) runtime at a version accepted by `compatibility.toml`, then confirm its worker can run in your environment. Portable Project Memory only creates the local Markdown protocol; it neither installs nor substitutes for the official runtime.

Use macOS, Linux, or WSL. Native Windows is intentionally deferred. A workspace must have relative paths, a root identity, one root state path, and authority files that already exist.

## Start from a release

Clone a known tag, rather than an unpinned branch:

```sh
git clone --branch v1.0.0 --depth 1 https://github.com/nitrogen5052/memory_system_1.git portable-project-memory
cd portable-project-memory
python3 -m venv .venv
.venv/bin/python -m pip install .
```

Copy `examples/multi-project-workspace` and edit only the relative identities, paths, state locations, and authority paths required by your workspace. Child project paths must not overlap, and child authority must stay under its child root.

## Review-first operation

Run `doctor` to identify platform or runtime problems. Then plan and read the full output before applying it:

```sh
.venv/bin/memory-system doctor --workspace .
.venv/bin/memory-system plan --workspace .
.venv/bin/memory-system apply --yes --workspace .
.venv/bin/memory-system verify --workspace .
```

`doctor` may report a runtime warning while still allowing the local layout to be planned. Use `verify --require-runtime` when runtime availability is a release gate. `verify` checks generated content, managed blocks, and declared project isolation.

Never use Git to transfer memory history. Commit the reviewed manifest, authority guidance, and deliberately curated Markdown only; keep runtime data and observation history local to the runtime.
