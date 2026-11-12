"""Re-take the hashes in tests/phase1_3a_checksums.txt after an INTENDED change to the earlier phases.
Files that no longer exist are dropped, the new shared files are added.   Run from the repo root: python refresh_checksums.py"""
import hashlib
import pathlib

path = pathlib.Path("tests/phase1_3a_checksums.txt")
names = [line.split("  ", 1)[1] for line in path.read_text().splitlines() if line.strip()]
for extra in ("config/config.yaml", "src/project_config.py"):
    if extra not in names:
        names.append(extra)
lines = [f"{hashlib.sha256(pathlib.Path(n).read_bytes()).hexdigest()}  {n}" for n in names if pathlib.Path(n).is_file()]
path.write_text("\n".join(lines) + "\n")
print(f"{len(lines)} files hashed, {len(names) - len(lines)} dropped (no longer exist)")
