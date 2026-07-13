"""N10 ``run_sandbox`` — ephemeral, tiered dbt verification (B-S2, ADR-0006).

Lifecycle for one candidate fix:

1. **Materialize** — copy the read-only repo checkout into a fresh, host-visible
   temp project dir and overlay the agent's in-memory edits (:class:`WorkingCopy`).
   Write a ``profiles.yml`` with a read-only ``compile`` target (tier-1) and, if
   a dev/sample connection is configured, a writable ``sample`` target (tier-2).
2. **Tier-1** — ``dbt compile --select <model>`` against the read-only target.
   Pass iff exit 0 and the node compiled; capture ``compiled_code``.
3. **Tier-2** — only if a sample connection is set: ``dbt build --select <model>
   --target sample`` (bounded sample). Pass iff the node's ``run_results`` status
   is ``success``.
4. **Teardown** — every container is ``--rm``; the temp dir is removed.

Docker-out-of-Docker: the worker talks to the host daemon over the mounted
socket, so ``docker run -v <path>:/project`` binds a *host* path. The temp
project therefore lives under ``work_dir``, which is bind-mounted into the worker
at the **same absolute path** as on the host — so the path resolves identically
on the daemon. Containers are ``--rm``, network-scoped, memory-capped, and hold
**no** prod-write creds (tier-2 targets a dev/sample, never prod).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..agent.diffing import WorkingCopy

# Files/dirs never copied into the sandbox project (stale/irrelevant, or the
# project-local profiles.yml — the sandbox mounts its own generated one).
_SKIP = {".git", "target", "dbt_packages", "logs", "__pycache__", "profiles.yml"}

# Every ephemeral sandbox container carries this label so a crashed worker's
# leftovers can be swept on the next startup (V5 task 2, orphan cleanup).
SANDBOX_LABEL = "sbflow.sandbox=1"


def cleanup_orphans() -> int:
    """Remove orphaned ephemeral sandbox containers left by a crashed worker.

    Containers are launched ``--rm`` so a clean run leaves nothing; a worker that
    died mid-verification can strand one. On startup we sweep by label. Returns
    the number of containers removed (0 when docker is unavailable — never
    raises, so it can't block worker startup).
    """
    try:
        listed = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label={SANDBOX_LABEL}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if listed.returncode != 0:
        return 0
    ids = [c for c in listed.stdout.split() if c]
    if not ids:
        return 0
    try:
        subprocess.run(
            ["docker", "rm", "-f", *ids],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    return len(ids)


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot run at all (image/daemon problem)."""


@dataclass
class TierResult:
    ran: bool
    passed: bool | None  # None = did not run
    log: str = ""
    compiled_code: str | None = None
    node_status: str | None = None


@dataclass
class SandboxRun:
    """Everything a verification produced, for the evidence builder."""

    tier1: TierResult
    tier2: TierResult
    select: str


@dataclass
class SandboxRunner:
    """Runs tiered verification for a candidate fix via the host Docker daemon."""

    repo_root: str
    image: str = "sbflow-sandbox:latest"
    #: RO warehouse connection tier-1 compiles against (dbt must connect).
    warehouse_url: str | None = None
    #: Writable dev/sample connection for tier-2; None disables tier-2.
    sample_url: str | None = None
    network: str | None = None
    work_dir: str = "/tmp/sbflow-sandbox"
    timeout: int = 120
    sample_limit: int = 10000
    #: sandbox/ context inside the worker image, for lazy image build.
    build_context: str | None = "/opt/sandbox"
    _image_ready: bool = field(default=False, repr=False)

    # -- image lifecycle ----------------------------------------------------
    def ensure_image(self) -> None:
        """Build the pre-baked image if the host daemon doesn't have it yet."""
        if self._image_ready:
            return
        have = subprocess.run(
            ["docker", "image", "inspect", self.image],
            capture_output=True,
            text=True,
        )
        if have.returncode == 0:
            self._image_ready = True
            return
        if not self.build_context or not Path(self.build_context).is_dir():
            raise SandboxError(
                f"sandbox image {self.image!r} missing and no build context at "
                f"{self.build_context!r}"
            )
        built = subprocess.run(
            ["docker", "build", "-t", self.image, self.build_context],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if built.returncode != 0:
            raise SandboxError(
                f"could not build sandbox image:\n{built.stderr[-2000:]}"
            )
        self._image_ready = True

    # -- verification -------------------------------------------------------
    def verify(self, working: WorkingCopy, select: str) -> SandboxRun:
        """Materialize ``working`` and run tier-1 (+tier-2 if configured)."""
        self.ensure_image()
        run_id = uuid.uuid4().hex[:12]
        proj = Path(self.work_dir) / run_id / "project"
        prof = Path(self.work_dir) / run_id / "profiles"
        try:
            self._materialize(proj, prof, working)
            tier1 = self._tier1(proj, prof, select)
            if tier1.passed and self.sample_url:
                tier2 = self._tier2(proj, prof, select)
            elif self.sample_url:
                # tier-1 failed: no point building, but disclose tier-2 as not run.
                tier2 = TierResult(
                    ran=False, passed=None, log="skipped: tier-1 did not pass"
                )
            else:
                tier2 = TierResult(
                    ran=False, passed=None, log="sample/dev connection not configured"
                )
            return SandboxRun(tier1=tier1, tier2=tier2, select=select)
        finally:
            self._cleanup(Path(self.work_dir) / run_id)

    # -- steps --------------------------------------------------------------
    def _materialize(self, proj: Path, prof: Path, working: WorkingCopy) -> None:
        proj.mkdir(parents=True, exist_ok=True)
        prof.mkdir(parents=True, exist_ok=True)
        root = Path(self.repo_root)
        for item in root.iterdir():
            if item.name in _SKIP:
                continue
            dest = proj / item.name
            if item.is_dir():
                shutil.copytree(item, dest, ignore=shutil.ignore_patterns(*_SKIP))
            else:
                shutil.copy2(item, dest)
        # Overlay the agent's in-memory edits (the candidate fix).
        for path in working.changed_paths():
            target = proj / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(working.current(path))
        prof.joinpath("profiles.yml").write_text(self._profiles_yml())

    def _profiles_yml(self) -> str:
        """A profile with a read-only ``compile`` target and (if configured) a
        writable dev ``sample`` target. dbt requires a reachable connection even
        to compile, so tier-1 uses the read-only warehouse connection."""
        outputs: dict[str, Any] = {}
        if self.warehouse_url:
            outputs["compile"] = _pg_output(self.warehouse_url, "sbflow_verify")
        if self.sample_url:
            outputs["sample"] = _pg_output(self.sample_url, "sbflow_sample")
        # Default target: prefer the RO compile target; fall back to sample.
        default = "compile" if "compile" in outputs else next(iter(outputs), "compile")
        lines = ["analytics:", f"  target: {default}", "  outputs:"]
        for name, o in outputs.items():
            lines.append(f"    {name}:")
            for k, v in o.items():
                lines.append(f"      {k}: {v}")
        return "\n".join(lines) + "\n"

    def _tier1(self, proj: Path, prof: Path, select: str) -> TierResult:
        cp = self._docker_run(proj, prof, ["dbt", "compile", "--select", select])
        compiled = _read_compiled(proj, select)
        passed = cp.returncode == 0 and compiled is not None
        return TierResult(
            ran=True,
            passed=passed,
            log=_clip(cp.stdout + cp.stderr),
            compiled_code=compiled,
        )

    def _tier2(self, proj: Path, prof: Path, select: str) -> TierResult:
        cp = self._docker_run(
            proj,
            prof,
            [
                "dbt",
                "build",
                "--select",
                select,
                "--target",
                "sample",
                "--vars",
                json.dumps({"sample_limit": self.sample_limit}),
            ],
        )
        status = _node_status(proj, select)
        return TierResult(
            ran=True,
            passed=(status == "success"),
            log=_clip(cp.stdout + cp.stderr),
            node_status=status,
        )

    def _docker_run(
        self, proj: Path, prof: Path, cmd: list[str]
    ) -> subprocess.CompletedProcess[str]:
        args = [
            "docker",
            "run",
            "--rm",
            "--label",
            SANDBOX_LABEL,
            "--memory",
            "1g",
            "--cpus",
            "2",
            "-v",
            f"{proj}:/project",
            "-v",
            f"{prof}:/profiles",
        ]
        if self.network:
            args += ["--network", self.network]
        args += [self.image, *cmd]
        try:
            return subprocess.run(
                args, capture_output=True, text=True, timeout=self.timeout
            )
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(
                args,
                returncode=124,
                stdout=e.stdout or "",
                stderr=f"sandbox timed out after {self.timeout}s",
            )
        except FileNotFoundError as e:  # docker CLI not present
            raise SandboxError(f"docker CLI unavailable in worker: {e}") from e

    def _cleanup(self, run_dir: Path) -> None:
        # dbt writes target/ as root inside the container; the worker is root in
        # its own container so rmtree succeeds. Ignore stray-permission errors.
        shutil.rmtree(run_dir, ignore_errors=True)


# --- helpers ---------------------------------------------------------------
def _pg_output(url: str, schema: str) -> dict[str, Any]:
    """Parse a ``postgres://user:pass@host:port/db`` URL into dbt profile keys."""
    from urllib.parse import urlparse

    u = urlparse(url)
    return {
        "type": "postgres",
        "host": u.hostname or "localhost",
        "port": u.port or 5432,
        "user": u.username or "postgres",
        "password": u.password or "",
        "dbname": (u.path or "/postgres").lstrip("/") or "postgres",
        "schema": schema,
        "threads": 1,
    }


def _model_file(select: str) -> str:
    """dbt writes compiled/run SQL under a name derived from the model file."""
    return f"{select}.sql"


def _read_compiled(proj: Path, select: str) -> str | None:
    hits = list((proj / "target" / "compiled").rglob(_model_file(select)))
    if hits:
        try:
            return hits[0].read_text()
        except OSError:
            return None
    return None


def _node_status(proj: Path, select: str) -> str | None:
    rr = proj / "target" / "run_results.json"
    if not rr.is_file():
        return None
    try:
        data = json.loads(rr.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    for r in data.get("results", []):
        uid = r.get("unique_id", "")
        if uid.endswith(f".{select}") or uid.split(".")[-1] == select:
            return r.get("status")
    return None


def _clip(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + "\n…[clipped]"
