"""Prepare a reproducible Home Assistant build using the local musllinux wheel."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", choices=("amd64", "aarch64"), default="amd64")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    artifacts = root / "pyAds-build"
    # CURRENT retains the original build repository's artifacts/ prefix.
    relative = Path((artifacts / args.arch / "3.6.0/CURRENT").read_text().strip())
    if relative.parts[:3] != ("artifacts", args.arch, "3.6.0") or ".." in relative.parts:
        raise SystemExit(f"Unexpected CURRENT path: {relative}")
    wheel = artifacts.joinpath(*relative.parts[1:])
    platform = "x86_64" if args.arch == "amd64" else "aarch64"
    if wheel.name != f"pyads-3.6.0-py3-none-musllinux_1_2_{platform}.whl":
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

    build = root / ".pyads-lab" / args.arch / "build"
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True)
    shutil.copy2(wheel, build / wheel.name)
    shutil.copytree(
        root / "custom_components/ads_multi", build / "ads_multi",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store"),
    )
    manifest_path = build / "ads_multi/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["requirements"] = [
        "pyads==3.6.0" if requirement.startswith("pyads==") else requirement
        for requirement in manifest["requirements"]
    ]
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    config = build.parent / "config"
    config.mkdir(exist_ok=True)
    if not (config / "configuration.yaml").exists():
        shutil.copy2(root / "config/configuration.yaml", config / "configuration.yaml")
    print(f"Prepared {wheel.relative_to(root)}\nSHA256: {digest}")


if __name__ == "__main__":
    main()
