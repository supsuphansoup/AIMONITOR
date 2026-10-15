"""Collect original license material for distributions actually in a PyInstaller bundle.

Uses only the standard library and installed wheel metadata. No network or package installation.
Missing legal material fails the build rather than silently producing an incomplete release.
"""

import importlib.metadata as metadata
import re
import shutil
import sys
from pathlib import Path
from typing import Optional


def _legal_file(path: Path) -> bool:
    name = path.name.lower()
    return name.startswith(("license", "licence", "copying", "notice", "copyright", "authors", "ofl")) or (
        "licenses" in [part.lower() for part in path.parts])


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", value)


def collect_distributions(output: Path, source_paths: list, distributions=None) -> list:
    """Match Analysis source paths against RECORD, excluding installed but unused packages."""
    sources = {Path(path).resolve() for path in source_paths if path}
    rows = []
    distributions = metadata.distributions() if distributions is None else distributions
    for dist in distributions:
        files = list(dist.files or [])
        if not any(Path(dist.locate_file(file)).resolve() in sources for file in files):
            continue
        name, version = dist.metadata["Name"], dist.version
        legal = [file for file in files if _legal_file(Path(file))]
        if not legal:
            raise RuntimeError(f"No original license files in installed distribution: {name} {version}")
        if name.lower() == "pystray":
            for required in ("COPYING", "COPYING.LGPL"):
                if not any(Path(file).name == required for file in legal):
                    raise RuntimeError(f"pystray requires original {required}")
        folder = output / "licenses" / f"{_safe_name(name)}-{_safe_name(version)}"
        copied = []
        for file in legal:
            relative = Path(file)
            # RECORD may contain ../Scripts entries; never copy outside the license directory.
            if relative.is_absolute() or ".." in relative.parts:
                continue
            source = Path(dist.locate_file(file))
            if not source.is_file():
                raise RuntimeError(f"Missing installed license file for {name}: {relative.as_posix()}")
            target = folder / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied.append(target.relative_to(output).as_posix())
        license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License") or ""
        classifiers = dist.metadata.get_all("Classifier", [])
        if not license_name:
            license_name = "; ".join(c for c in classifiers if c.startswith("License ::"))
        urls = dist.metadata.get_all("Project-URL", [])
        homepage = dist.metadata.get("Home-page")
        if homepage:
            urls.append(homepage)
        source_url = f"https://pypi.org/project/{name}/{version}/#files"
        if name.lower() == "pystray":
            source_url = f"https://github.com/moses-palmer/pystray/tree/v{version}"
        if name.lower() == "certifi":
            source_url += " (source distribution; Mozilla Public License 2.0 covered source)"
        rows.append({"name": name, "version": version, "license": license_name,
                     "source": source_url, "urls": urls, "files": copied})
    return sorted(rows, key=lambda row: row["name"].lower())


def collect_notices(project: Path, output: Path, pure: list, data: list,
                    distributions=None, python_prefix: Optional[Path] = None) -> list:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(project) / "LICENSE", output / "LICENSE")
    sources = [entry[1] for entry in list(pure) + list(data)]
    rows = collect_distributions(output, sources, distributions)
    version = ".".join(str(part) for part in sys.version_info[:3])
    prefix = Path(python_prefix or sys.base_prefix)
    python_license = prefix / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("Python runtime LICENSE.txt is required")
    python_target = output / "licenses" / f"Python-{version}" / "LICENSE.txt"
    python_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, python_target)
    runtime_files = [python_target.relative_to(output).as_posix()]
    for runtime, marker in (("Tcl", "_tcl_data"), ("Tk", "_tk_data")):
        candidates = [Path(source) for dest, source, _kind in data
                      if Path(dest).as_posix() == f"{marker}/license.terms"]
        initializer = "init.tcl" if runtime == "Tcl" else "tk.tcl"
        initializers = [Path(source) for dest, source, _kind in data
                        if Path(dest).as_posix() == f"{marker}/{initializer}"]
        runtime_version = "bundled"
        if initializers:
            match = re.search(r"package require -exact " + runtime + r"\s+([0-9.]+)",
                              initializers[0].read_text(encoding="utf-8"))
            if not match:
                raise RuntimeError(f"Cannot identify bundled {runtime} version")
            runtime_version = match.group(1)
        if not candidates:
            # Some Python Windows installs omit Tcl's notice. Use only an exact-version,
            # unmodified upstream copy checked into this source tree; never guess a version.
            vendored = Path(project) / "build-windows/licenses" / f"{runtime}-{runtime_version}"
            if (vendored / "license.terms").is_file():
                candidates = [vendored / "license.terms"]
        if not candidates:
            raise RuntimeError(f"{runtime} runtime license.terms is required")
        source = candidates[0]
        target = output / "licenses" / f"{runtime}-{runtime_version}" / "license.terms"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        provenance = source.parent / "SOURCE.txt"
        if provenance.is_file():
            shutil.copyfile(provenance, target.parent / "SOURCE.txt")
        runtime_files.append(target.relative_to(output).as_posix())
    text = ["AIMONITOR third-party notices", "",
            "AIMONITOR is licensed under MIT; see LICENSE. Third-party components retain their own licenses.",
            "The following distributions were selected from the actual PyInstaller Analysis source files.",
            "Original texts are preserved under licenses/<distribution>-<version>/.", "",
            f"Python runtime {version}: PSF and included component licenses; see {runtime_files[0]}",
            f"Python source: https://www.python.org/ftp/python/{version}/Python-{version}.tgz",
            "Tcl/Tk: original bundled license.terms; source: https://www.tcl.tk/software/tcltk/",
            *runtime_files[1:], ""]
    for row in rows:
        text.extend([f"{row['name']} {row['version']}", f"License metadata (verbatim): {row['license']}",
                     f"Source: {row['source']}", *row["urls"], *row["files"], ""])
    text.extend([
        "Rebuilding with a modified library (including pystray under LGPLv3)",
        "AIMONITOR uses pystray; its original COPYING (GPLv3) and COPYING.LGPL (LGPLv3) are included above.",
        "AIMONITOR source and build instructions: https://github.com/supsuphansoup/AIMONITOR/tree/v0.6.1",
        "Obtain this release's AIMONITOR source, the matching library source above, and Python with Tk.",
        "In your own build environment, install backend/requirements.txt, desktop/requirements.txt and",
        "build-windows/requirements.txt. Install your modified library from its source directory into that",
        "environment, then run: pyinstaller build-windows/aimonitor.spec --noconfirm",
        "Use the resulting dist/AIMONITOR folder in place of the distributed folder. This recombines the",
        "application with your modified library; no signing key is required. Keep its license files.",
        "No restriction is imposed on reverse engineering needed to debug modifications to LGPL components.",
        "certifi's MPL-2.0 covered source is the exact-version source distribution linked above.",
        "",
    ])
    (output / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(text), encoding="utf-8")
    return rows
