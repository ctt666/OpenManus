"""Unit tests for SandboxSettings configuration extension (REQ-1.1)."""

from pathlib import Path

import pytest

from app.config import Config, SandboxSettings


class TestSandboxSettings:
    """Test cases for SandboxSettings configuration."""

    def test_default_values(self):
        """Test that default values are correct."""
        settings = SandboxSettings()
        assert settings.mount_workspace is True
        assert settings.workspace_path is None

    def test_custom_mount_workspace(self):
        """Test custom mount_workspace value."""
        settings = SandboxSettings(mount_workspace=False)
        assert settings.mount_workspace is False

    def test_custom_workspace_path(self):
        """Test custom workspace_path value."""
        custom_path = "/custom/workspace/path"
        settings = SandboxSettings(workspace_path=custom_path)
        assert settings.workspace_path == custom_path

    def test_all_fields(self):
        """Test all fields together."""
        settings = SandboxSettings(
            use_sandbox=True,
            image="python:3.11-slim",
            work_dir="/custom_work",
            memory_limit="1g",
            cpu_limit=2.0,
            timeout=600,
            network_enabled=True,
            mount_workspace=False,
            workspace_path="/custom/path",
        )
        assert settings.use_sandbox is True
        assert settings.image == "python:3.11-slim"
        assert settings.work_dir == "/custom_work"
        assert settings.memory_limit == "1g"
        assert settings.cpu_limit == 2.0
        assert settings.timeout == 600
        assert settings.network_enabled is True
        assert settings.mount_workspace is False
        assert settings.workspace_path == "/custom/path"

    def test_config_loading(self):
        """Test that config can be loaded with new fields."""
        # This test verifies that the config system can handle the new fields
        # without errors (assuming default values if not in config file)
        config = Config()
        sandbox_config = config.sandbox

        # Verify new fields exist with default values
        assert hasattr(sandbox_config, "mount_workspace")
        assert hasattr(sandbox_config, "workspace_path")
        assert sandbox_config.mount_workspace is True
        assert sandbox_config.workspace_path is None
