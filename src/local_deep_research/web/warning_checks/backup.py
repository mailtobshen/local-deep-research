"""Backup status warning checks.

Pure functions that take primitive values and return warning dicts (or None).
"""

from typing import Optional

from ..translations import _


def check_backup_disabled(
    backup_enabled: bool, dismissed: bool
) -> Optional[dict]:
    """Warn if automatic backups are disabled."""
    if backup_enabled:
        return None
    if dismissed:
        return None

    return {
        "type": "backup_disabled",
        "icon": "💾",
        "title": _("Database Backups Disabled"),
        "message": _(
            "Automatic database backups are disabled. Without backups, "
            "data cannot be recovered if the database is corrupted. "
            "Enable backups in Settings › Backup."
        ),
        "dismissKey": "app.warnings.dismiss_backup_disabled",
    }


def check_no_backups_exist(
    backup_enabled: bool, backup_count: int, dismissed: bool
) -> Optional[dict]:
    """Warn if backups are enabled but none exist yet."""
    if not backup_enabled:
        return None
    if backup_count > 0:
        return None
    if dismissed:
        return None

    return {
        "type": "no_backups",
        "icon": "📁",
        "title": _("No Backups Found"),
        "message": _(
            "No database backups have been created yet. A backup will "
            "be created automatically on your next login. You can "
            "configure backup settings in Settings › Backup."
        ),
        "dismissKey": "app.warnings.dismiss_no_backups",
    }


def check_backup_healthy(
    backup_enabled: bool,
    backup_count: int,
    total_size_human: str,
    dismissed: bool,
) -> Optional[dict]:
    """Show positive backup status info (dismissable)."""
    if not backup_enabled:
        return None
    if backup_count == 0:
        return None
    if dismissed:
        return None

    # Plural form (English) — Chinese has no plural, so the {plural}
    # placeholder is simply blank in zh.json.
    plural = "s" if backup_count > 1 else ""
    return {
        "type": "backup_info",
        "icon": "✅",
        "title": _("Database Backup Active"),
        "message": _(
            "{count} backup{plural} ({size}) on disk. Encrypted backups cannot be "
            "compressed, so each backup equals your database size "
            "(which can be large if PDFs are stored in the database). "
            "Backups are created once per day on login. Manage backups "
            "in Settings › Backup.",
            count=backup_count,
            plural=plural,
            size=total_size_human,
        ),
        "dismissKey": "app.warnings.dismiss_backup_info",
    }
