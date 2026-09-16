"""Stage the integration and verified amd64 wheel for Home Assistant OS."""

import hashlib
import json
from pathlib import Path
import shutil


def main():
    root = Path(__file__).resolve().parent.parent
    artifacts = root / "pyAds-build"
    relative = Path((artifacts / "amd64/3.6.0/CURRENT").read_text().strip())
    if relative.parts[:3] != ("artifacts", "amd64", "3.6.0") or ".." in relative.parts:
        raise SystemExit(f"Unexpected CURRENT path: {relative}")
    wheel = artifacts.joinpath(*relative.parts[1:])
    if wheel.name != "pyads-3.6.0-py3-none-musllinux_1_2_x86_64.whl":
        raise SystemExit(f"Unexpected wheel: {wheel.name}")
    checksums = dict(
        (name.lstrip("*"), digest)
        for digest, name in (
            line.split() for line in wheel.with_name("SHA256SUMS").read_text().splitlines()
            if line.strip()
        )
    )
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    if checksums.get(wheel.name) != digest:
        raise SystemExit(f"SHA256 mismatch: {wheel}")

    stage = root / ".pyads-deploy/ads_multi"
    if stage.exists():
        shutil.rmtree(stage)
    shutil.copytree(
        root / "custom_components/ads_multi", stage,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    wheels = stage / "wheels"
    wheels.mkdir()
    shutil.copy2(wheel, wheels / wheel.name)
    manifest_path = stage / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    requirement = (
        f"pyads @ file:///config/custom_components/ads_multi/wheels/{wheel.name}"
        f"#sha256={digest}"
    )
    if "pyads==3.6.0" not in manifest["requirements"]:
        raise SystemExit("Expected pyads==3.6.0 in source manifest")
    manifest["requirements"] = [
        requirement if entry == "pyads==3.6.0" else entry
        for entry in manifest["requirements"]
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Prepared {stage}\n{requirement}")


if __name__ == "__main__":
    main()
