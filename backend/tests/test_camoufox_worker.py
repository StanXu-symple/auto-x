import sys
from types import ModuleType
from unittest.mock import Mock

import pytest

from app.camoufox_worker import CamoufoxWorker


@pytest.fixture
def sdk(monkeypatch, tmp_path):
    package = ModuleType("camoufox")
    exceptions = ModuleType("camoufox.exceptions")
    exceptions.CamoufoxNotInstalled = type("CamoufoxNotInstalled", (FileNotFoundError,), {})
    exceptions.UnsupportedVersion = type("UnsupportedVersion", (Exception,), {})
    versions = ModuleType("camoufox.multiversion")
    versions.COMPAT_FLAG = tmp_path / ".0.5_FLAG"
    versions.COMPAT_FLAG.touch()
    pkgman = ModuleType("camoufox.pkgman")
    pkgman.INSTALL_DIR = tmp_path
    active = tmp_path / "browsers" / "official" / "version-selected-by-sdk"
    active.mkdir(parents=True)
    executable = active / "camoufox-bin"
    executable.write_text("binary")
    executable.chmod(0o755)
    pkgman.camoufox_path = Mock(return_value=active)
    pkgman.launch_path = Mock(return_value=str(executable))
    for module in (package, exceptions, versions, pkgman):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return pkgman, exceptions, versions, executable


def test_detects_sdk_selected_multiversion_browser_without_download(sdk):
    pkgman, _, _, executable = sdk
    assert CamoufoxWorker.browser_installed() is True
    pkgman.camoufox_path.assert_called_once_with(download_if_missing=False)
    pkgman.launch_path.assert_called_once_with(browser_path=executable.parent)


@pytest.mark.parametrize("failure", ["missing", "unsupported", "permission"])
def test_unavailable_browser_returns_false(sdk, failure):
    pkgman, exceptions, _, _ = sdk
    error = {
        "missing": exceptions.CamoufoxNotInstalled,
        "unsupported": exceptions.UnsupportedVersion,
        "permission": PermissionError,
    }[failure]
    pkgman.camoufox_path.side_effect = error("unavailable")
    assert CamoufoxWorker.browser_installed() is False
    pkgman.camoufox_path.assert_called_once_with(download_if_missing=False)
    pkgman.launch_path.assert_not_called()


def test_missing_or_nonexecutable_file_is_not_installed(sdk):
    _, _, _, executable = sdk
    executable.chmod(0o644)
    assert CamoufoxWorker.browser_installed() is False
    executable.unlink()
    assert CamoufoxWorker.browser_installed() is False


def test_status_leaves_incompatible_cache_untouched(sdk):
    pkgman, _, versions, executable = sdk
    versions.COMPAT_FLAG.unlink()
    assert CamoufoxWorker.browser_installed() is False
    assert executable.read_text() == "binary"
    pkgman.camoufox_path.assert_not_called()


async def test_status_propagates_detection_and_requires_cli(sdk, monkeypatch):
    from types import SimpleNamespace
    import app.camoufox_worker as module

    worker = object.__new__(CamoufoxWorker)
    worker.executor = SimpleNamespace(browser_pool=SimpleNamespace(size=0, busy_count=0))
    worker.settings = SimpleNamespace(camoufox_browser_pool_size=1)
    monkeypatch.setattr(module.shutil, "which", lambda _: "/usr/local/bin/xhs")
    assert (await worker.browser_status())["installed"] is True
    monkeypatch.setattr(module.shutil, "which", lambda _: None)
    assert (await worker.browser_status())["installed"] is False
