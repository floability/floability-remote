"""Application validation that combines domain rules with local I/O checks."""

from ..domain.config import (
    ConfigError,
    RunConfig,
    ValidationIssue,
    run_config_issues as domain_run_config_issues,
    validate_run_config as validate_domain_run_config,
)
from ..services.backpacks import inspect as inspect_backpack


def run_config_issues(config: RunConfig) -> list[ValidationIssue]:
    """Return structural issues plus problems found by inspecting local backpacks."""
    issues = list(domain_run_config_issues(config))
    source = config.backpack
    invalid_path = any(issue.field == "backpack.local_path" for issue in issues)
    if source.source_type in ("folder", "zip") and not invalid_path:
        issues.extend(
            ValidationIssue("backpack.local_path", message, "--backpack")
            for message in inspect_backpack(source.local_path).problems
        )
    return issues


def validate_run_config(config: RunConfig) -> RunConfig:
    """Validate a run at the application boundary and normalize local paths."""
    issues = run_config_issues(config)
    if issues:
        raise ConfigError(issues)
    return validate_domain_run_config(config)
