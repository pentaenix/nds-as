# Device Toolkit force menubar repair

This patch force-installs **Device Toolkit → Mobile → Extract Installed App ROM…** inside `ShellMixin._build_ui()` immediately after `menubar = self.menuBar()`. It also adds an **Extract App ROM** toolbar fallback so the workflow is visible even when macOS native menu placement is confusing.
