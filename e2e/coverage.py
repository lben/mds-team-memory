"""Endpoint coverage, measured rather than asserted.

Reads uvicorn access logs produced by the adversarial harnesses (e2e/adv/access*.log)
and diffs the paths actually requested against the real route table.

The route table must come from `app.openapi()["paths"]` — walking `app.routes`
does not work here, because the routers are wrapped in `_IncludedRouter`.

    .venv/bin/python e2e/coverage.py [extra-access-log ...]
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.main import app  # noqa: E402

# uvicorn access line: '127.0.0.1:1234 - "GET /api/feed?x=1 HTTP/1.1" 200'
ACCESS = re.compile(r'"(?P<method>[A-Z]+) (?P<path>[^ ?]+)[^"]*" (?P<status>\d{3})')
HEXID = re.compile(r"/[0-9a-fA-F-]{8,}")
NUMID = re.compile(r"/\d+")


def normalise(path: str) -> str:
    return NUMID.sub("/{id}", HEXID.sub("/{id}", path))


def spec_paths() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path, ops in app.openapi()["paths"].items():
        methods = {m.upper() for m in ops if m.upper() in
                   {"GET", "POST", "PUT", "PATCH", "DELETE"}}
        if methods:
            out[normalise(re.sub(r"\{[^}]+\}", "{id}", path))] = methods
    return out


def hits(logs: list[Path]) -> set[tuple[str, str]]:
    seen = set()
    for log in logs:
        if not log.exists():
            continue
        for line in log.read_text(errors="replace").splitlines():
            m = ACCESS.search(line)
            if not m or not m["path"].startswith("/api"):
                continue
            # A 404/405 is not coverage: the route was never actually executed.
            if m["status"] in {"404", "405"}:
                continue
            seen.add((m["method"], normalise(m["path"])))
    return seen


def main() -> int:
    logs = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "e2e" / "adv").glob("access*.log"))
    if not logs:
        print("no access logs found — run the adversarial harnesses first")
        return 1
    spec = spec_paths()
    reached = hits(logs)
    total = sum(len(ms) for ms in spec.values())
    covered, missing = 0, []
    for path, methods in sorted(spec.items()):
        for method in sorted(methods):
            if (method, path) in reached:
                covered += 1
            else:
                missing.append(f"{method} {path}")

    print(f"logs: {', '.join(p.name for p in logs)}")
    print(f"endpoint coverage: {covered}/{total} ({round(100 * covered / total)}%)")
    if missing:
        print(f"\nNEVER REACHED ({len(missing)}):")
        for m in missing:
            print("   ", m)
    unknown = sorted(
        f"{me} {pa}" for me, pa in reached
        if pa not in spec and pa not in {"/api/health"}
    )
    if unknown:
        print(f"\nrequested but not in the spec ({len(unknown)}):")
        for u in unknown:
            print("   ", u)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
