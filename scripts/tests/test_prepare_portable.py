import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "prepare-portable.py"
SPEC = importlib.util.spec_from_file_location("prepare_portable", SCRIPT)
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


class PinnedUvTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="macbot-uv-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.target = self.root / "release/MacBot-Portable/MacBot"
        self.pinned = b"project-pinned-uv"
        self.manifest = {"uv_sha256": hashlib.sha256(self.pinned).hexdigest()}
        roots = patch.multiple(packaging, ROOT=self.root, TARGET=self.target)
        roots.start()
        self.addCleanup(roots.stop)

    def binary(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def test_global_upgrade_does_not_block_existing_portable(self):
        bundled = self.binary(self.target / "uv.exe", self.pinned)
        global_uv = self.binary(self.root / "global/uv.exe", b"newer-global-uv")
        with patch.object(packaging.shutil, "which", return_value=str(global_uv)):
            self.assertEqual(packaging.resolve_uv(self.manifest), bundled)
        self.assertEqual(global_uv.read_bytes(), b"newer-global-uv")

    def test_fresh_checkout_uses_project_tool_with_newer_global_uv(self):
        local_uv = self.binary(self.root / "build/tools/uv.exe", self.pinned)
        global_uv = self.binary(self.root / "global/uv.exe", b"newer-global-uv")
        with patch.object(packaging.shutil, "which", return_value=str(global_uv)):
            self.assertEqual(packaging.resolve_uv(self.manifest), local_uv)

    def test_corrupt_portable_binary_is_replaced_by_verified_project_tool(self):
        self.binary(self.target / "uv.exe", b"corrupt")
        local_uv = self.binary(self.root / "build/tools/uv.exe", self.pinned)
        with patch.object(packaging.shutil, "which", return_value=None):
            self.assertEqual(packaging.resolve_uv(self.manifest), local_uv)

    def test_matching_global_binary_is_accepted(self):
        global_uv = self.binary(self.root / "global/uv.exe", self.pinned)
        with patch.object(packaging.shutil, "which", return_value=str(global_uv)):
            self.assertEqual(packaging.resolve_uv(self.manifest), global_uv)

    def test_unverified_binaries_are_rejected_with_local_setup_instructions(self):
        global_uv = self.binary(self.root / "global/uv.exe", b"unverified")
        with (
            patch.object(packaging.shutil, "which", return_value=str(global_uv)),
            self.assertRaisesRegex(RuntimeError, "build/tools/uv.exe"),
        ):
            packaging.resolve_uv(self.manifest)

    def test_prepare_reuses_bundled_uv_and_preserves_workspace_data(self):
        import json

        self.binary(self.target / "uv.exe", self.pinned)
        history = self.binary(self.target / "Data/history.txt", b"private-history")
        requirements = self.binary(self.root / "bootstrap/requirements.txt", b"example==1\n")
        manifest = {
            **self.manifest,
            "requirements_sha256": packaging.digest(requirements),
        }
        self.binary(self.root / "bootstrap/engines.json", json.dumps(manifest).encode())
        self.binary(self.root / "backend/macbot/__init__.py", b"")
        for name in ("desktop_entry.py", "model-manifest.json", "chat-model.json"):
            self.binary(self.root / "backend" / name, b"")
        python = self.root / "python/Scripts/python.exe"
        for package in ("antlr4", "antlr4_python3_runtime-4.9.3.dist-info"):
            self.binary(self.root / "python/Lib/site-packages" / package / "file.txt", b"package")
        program_files = self.root / "Program Files"
        self.binary(
            program_files / "Microsoft Visual Studio/2022/BuildTools/VC/Redist/MSVC"
            / "14.44.0/x64/Microsoft.VC143.CRT/vcruntime140.dll",
            b"runtime",
        )
        with (
            patch.multiple(packaging, PORTABLE=self.target.parent,
                           PAYLOAD=self.root / "build/bootstrap-payload"),
            patch.object(packaging.sys, "executable", str(python)),
            patch.object(packaging.shutil, "which", return_value=None),
            patch.dict(packaging.os.environ, {"ProgramFiles(x86)": str(program_files)}),
        ):
            packaging.prepare()
        self.assertEqual((self.target / "uv.exe").read_bytes(), self.pinned)
        self.assertEqual(history.read_bytes(), b"private-history")
        self.assertTrue((self.target / "bootstrap/manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
