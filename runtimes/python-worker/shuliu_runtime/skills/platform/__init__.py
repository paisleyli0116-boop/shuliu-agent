"""Platform-specific Skill sandbox providers (Task 36).

Each module in this package implements `SandboxProvider` for one OS and
must only claim `enforced_controls` that runner attack tests have actually
proven on that OS (see `tests/skills/platform/`).  Unproven controls keep
the platform provider unsupported so executable Skills fail closed.
"""
