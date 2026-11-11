import os
import sys
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pydatavault import directory_links


class DirectoryLinkTests(unittest.TestCase):
    def setUp(self):
        self.root = REPO_ROOT / ".test_tmp" / f"links_{uuid4().hex}"
        self.target = self.root / "measurement data"
        self.target.mkdir(parents=True)
        (self.target / "data.txt").write_text("keep measurement")
        self.link = self.root / "project" / "device"

    def test_symlink_success_does_not_need_fallback(self):
        with mock.patch.object(directory_links.os, "symlink") as symlink:
            directory_links.create_directory_link(self.target, self.link)
        symlink.assert_called_once_with(
            self.target.resolve(), self.link, target_is_directory=True
        )
        self.assertTrue(self.link.parent.is_dir())

    def test_unrelated_error_is_not_suppressed(self):
        error = PermissionError("Directory is not writable")
        with mock.patch.object(directory_links.os, "symlink", side_effect=error):
            with self.assertRaises(PermissionError) as raised:
                directory_links.create_directory_link(self.target, self.link)
        self.assertIs(raised.exception, error)
        self.assertFalse(self.link.exists())

    @unittest.skipUnless(os.name == "nt", "Windows junctions")
    def test_privilege_failure_creates_readable_junction_and_preserves_target(self):
        error = OSError("Missing symlink privilege")
        error.winerror = 1314
        with mock.patch.object(directory_links.os, "symlink", side_effect=error):
            directory_links.create_directory_link(self.target, self.link)
        self.assertTrue(directory_links.is_junction(self.link))
        self.assertEqual((self.link / "data.txt").read_text(), "keep measurement")
        with mock.patch.object(directory_links.os, "symlink") as symlink:
            directory_links.create_directory_link(self.target, self.link)
        symlink.assert_not_called()
        directory_links.remove_directory_link(self.link)
        self.assertFalse(directory_links.is_junction(self.link))
        self.assertEqual((self.target / "data.txt").read_text(), "keep measurement")

    @unittest.skipUnless(os.name == "nt", "Windows junctions")
    def test_broken_junction_can_be_removed(self):
        import _winapi

        self.link.parent.mkdir()
        _winapi.CreateJunction(str(self.target), str(self.link))
        renamed_target = self.root / "renamed target"
        self.target.rename(renamed_target)
        self.assertFalse(self.link.exists())
        self.assertTrue(directory_links.is_junction(self.link))
        directory_links.remove_directory_link(self.link)
        self.assertFalse(directory_links.is_junction(self.link))
        self.assertEqual((renamed_target / "data.txt").read_text(), "keep measurement")

    @unittest.skipUnless(os.name == "nt", "Windows junctions")
    def test_existing_directory_is_never_replaced_by_fallback(self):
        self.link.mkdir(parents=True)
        sentinel = self.link / "existing.txt"
        sentinel.write_text("existing data")
        error = OSError("Missing symlink privilege")
        error.winerror = 1314
        with mock.patch.object(directory_links.os, "symlink", side_effect=error):
            with self.assertRaises(OSError):
                directory_links.create_directory_link(self.target, self.link)
        self.assertFalse(directory_links.is_junction(self.link))
        self.assertEqual(sentinel.read_text(), "existing data")


if __name__ == "__main__":
    unittest.main()
