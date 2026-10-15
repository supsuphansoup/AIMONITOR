import importlib.metadata as metadata
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def collector():
    spec = importlib.util.spec_from_file_location("collect_notices", ROOT / "build-windows/collect_notices.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def distribution(root, name, license_name="MIT", license_files=("LICENSE",)):
    info = root / f"{name}-1.2.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Name: {name}\nVersion: 1.2\nLicense: {license_name}\n", encoding="utf-8")
    (root / name).mkdir()
    (root / name / "__init__.py").write_text("", encoding="utf-8")
    records = [f"{name}/__init__.py,,"]
    for filename in license_files:
        (info / filename).write_text(f"Original {name} {filename}\n", encoding="utf-8")
        records.append(f"{info.name}/{filename},,")
    (info / "RECORD").write_text("\n".join(records), encoding="utf-8")
    return metadata.PathDistribution(info)


def test_collector_selects_actual_bundle_files_and_preserves_original_notices(tmp_path):
    collect = collector()
    included = distribution(tmp_path, "customtkinter", "Creative Commons Zero v1.0 Universal")
    excluded = distribution(tmp_path, "not_bundled")
    output = tmp_path / "out"
    output.mkdir()
    names = collect.collect_distributions(output, [tmp_path / "customtkinter/__init__.py"],
                                           distributions=[included, excluded])
    assert [row["name"] for row in names] == ["customtkinter"]
    assert "Creative Commons Zero" in names[0]["license"]
    original = output / "licenses/customtkinter-1.2/customtkinter-1.2.dist-info/LICENSE"
    assert original.read_text(encoding="utf-8") == "Original customtkinter LICENSE\n"
    assert not (output / "licenses/not_bundled-1.2").exists()


def test_collector_requires_both_pystray_license_texts(tmp_path):
    collect = collector()
    dist = distribution(tmp_path, "pystray", "LGPLv3", ("COPYING",))
    with pytest.raises(RuntimeError, match="COPYING.LGPL"):
        collect.collect_distributions(tmp_path / "out", [tmp_path / "pystray/__init__.py"], [dist])


def test_runtime_and_project_license_materials_are_at_bundle_top_level(tmp_path):
    collect = collector()
    project, runtime, output = tmp_path / "repo", tmp_path / "python", tmp_path / "out"
    project.mkdir()
    runtime.mkdir()
    (project / "LICENSE").write_text("Project MIT\n", encoding="utf-8")
    (runtime / "LICENSE.txt").write_text("Python PSF\n", encoding="utf-8")
    tcl, tk = tmp_path / "tcl8.6/license.terms", tmp_path / "tk8.6/license.terms"
    for path in (tcl, tk):
        path.parent.mkdir()
        path.write_text("Tcl/Tk original terms\n", encoding="utf-8")
    collect.collect_notices(project, output, [], [("_tcl_data/license.terms", str(tcl), "DATA"),
                                                ("_tk_data/license.terms", str(tk), "DATA")],
                            distributions=[], python_prefix=runtime)
    assert (output / "LICENSE").read_text(encoding="utf-8") == "Project MIT\n"
    assert list((output / "licenses").glob("Python-*/LICENSE.txt"))
    assert "Rebuilding with a modified library" in (output / "THIRD_PARTY_NOTICES.txt").read_text()


def test_missing_runtime_notice_uses_only_exact_version_upstream_copy(tmp_path):
    collect = collector()
    project, runtime, output = tmp_path / "repo", tmp_path / "python", tmp_path / "out"
    project.mkdir()
    runtime.mkdir()
    (project / "LICENSE").write_text("MIT")
    (runtime / "LICENSE.txt").write_text("PSF")
    tcl_init, tk_license = tmp_path / "init.tcl", tmp_path / "license.terms"
    tcl_init.write_text("package require -exact Tcl 8.6.12\n")
    tk_license.write_text("Tk original")
    vendor = project / "build-windows/licenses/Tcl-8.6.12"
    vendor.mkdir(parents=True)
    (vendor / "license.terms").write_text("Tcl exact original")
    data = [("_tcl_data/init.tcl", str(tcl_init), "DATA"),
            ("_tk_data/license.terms", str(tk_license), "DATA")]
    collect.collect_notices(project, output, [], data, [], runtime)
    assert (output / "licenses/Tcl-8.6.12/license.terms").read_text() == "Tcl exact original"
    tcl_init.write_text("package require -exact Tcl 8.6.13\n")
    with pytest.raises(RuntimeError, match="Tcl runtime license"):
        collect.collect_notices(project, output, [], data, [], runtime)


def test_notices_point_to_the_source_of_this_release():
    import re

    version = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(encoding="utf-8"), re.M)
    source = (ROOT / "build-windows/collect_notices.py").read_text(encoding="utf-8")
    assert f"/AIMONITOR/tree/v{version.group(1)}\"" in source
