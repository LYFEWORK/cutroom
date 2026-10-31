"""The renderer's TypeScript compiles (npm run typecheck in remotion/), and the project is the one the installer expects."""
import json
import shutil
import subprocess

import pytest

import render_helpers as H

needs_node_modules = pytest.mark.skipif(
    not ((H.REMOTION / "node_modules" / ".bin" / "tsc").exists() and shutil.which("npm")), reason="run npm ci in remotion/ first"
)


@needs_node_modules
def test_the_renderer_typechecks():
    r = subprocess.run(["npm", "run", "typecheck", "--silent"], cwd=H.REMOTION, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_package_is_the_one_the_installer_installs():
    pkg = json.loads((H.REMOTION / "package.json").read_text())
    lock = json.loads((H.REMOTION / "package-lock.json").read_text())
    assert pkg["name"] == "cutroom-renderer" and {"typecheck", "studio"} <= set(pkg["scripts"])
    deps = {**pkg["dependencies"], **pkg["devDependencies"]}
    assert {"remotion", "@remotion/cli", "react", "react-dom", "typescript"} <= set(deps)
    assert pkg["dependencies"]["react"].startswith("^19")
    # the lock names the same project and holds a Remotion that satisfies the range, with the CLI at the same version (Remotion needs them in step)
    assert lock["name"] == "cutroom-renderer" and lock["packages"][""]["dependencies"] == pkg["dependencies"]
    assert lock["packages"]["node_modules/remotion"]["version"] == lock["packages"]["node_modules/@remotion/cli"]["version"]


def test_the_compositions_are_registered_and_the_graphics_registry_lists_the_examples():
    root = (H.REMOTION / "Root.tsx").read_text()
    assert 'id="Edit"' in root and 'id="Montage"' in root  # the caption engine registers its own Captions composition from captions/register.ts
    index = (H.REMOTION / "graphics" / "index.ts").read_text()
    assert "LowerThird" in index and "TitleCard" in index
    assert (H.REMOTION / "index.ts").read_text().count("registerRoot") == 2  # the import and the call


def test_nothing_in_the_renderer_springs_or_overshoots():
    """motion is one long ease-out: no spring anywhere, and every easing curve stays between its start and its end (a control point above 1 or
    under 0 would overshoot)"""
    import re

    curves = 0
    for f in sorted(H.REMOTION.glob("*.ts*")) + sorted((H.REMOTION / "graphics").glob("*.ts*")):
        text = f.read_text()
        assert not re.search(r"\bspring\s*\(|\bspring\b[^\n]*from [\"']remotion", text), f.name
        for m in re.finditer(r"Easing\.bezier\(([^)]*)\)", text):
            x1, y1, x2, y2 = (float(v) for v in m.group(1).split(","))
            assert 0 <= x1 <= 1 and 0 <= x2 <= 1 and 0 <= y1 <= 1 and 0 <= y2 <= 1, (f.name, m.group(0))
            curves += 1
    assert curves >= 6
