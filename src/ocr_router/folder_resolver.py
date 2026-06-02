"""Folder resolution with density-aware routing.

Rules (applied in order):
1. Target path exists exactly → use it.
2. Last segment is a year AND parent exists:
   a. Parent has year subfolders (dense) → create the year folder.
   b. Parent has no year subfolders (flat) → use parent directly.
3. Parent doesn't exist → status='suggest' (caller decides).
4. Non-year path doesn't exist → create it.
"""

from pathlib import Path


class FolderResolver:
    """Resolve the actual destination folder for a routed document."""

    def __init__(self, output_root: Path):
        self.output_root = output_root

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, route: str) -> tuple[Path, str]:
        """Return (destination_dir, status).

        status values:
          'exact'   — route existed, used as-is
          'created' — year folder missing but created (parent was year-organized)
          'flat'    — parent is flat (no year dirs), file lives in parent directly
          'suggest' — required folder doesn't exist; caller should log a suggestion
        """
        target = self.output_root / route

        if target.exists():
            return target, 'exact'

        parts = list(Path(route).parts)

        # Case: last part is a 4-digit year
        if parts and _is_year(parts[-1]) and len(parts) > 1:
            parent_path = self.output_root / Path(*parts[:-1])

            if parent_path.exists():
                if self._has_year_subfolders(parent_path):
                    target.mkdir(parents=True, exist_ok=True)
                    return target, 'created'
                else:
                    return parent_path, 'flat'
            else:
                return target, 'suggest'

        # Case: no year at end — create intermediate folders if safe to do so
        parent = target.parent
        if parent.exists():
            target.mkdir(parents=True, exist_ok=True)
            return target, 'created'

        return target, 'suggest'

    def suggest_message(self, route: str) -> str:
        """Human-readable suggestion when the folder doesn't exist."""
        return f"⚠  SUGGEST creating: {self.output_root / route}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _has_year_subfolders(folder: Path) -> bool:
        """Return True if the folder contains any 4-digit year subdirectories."""
        try:
            return any(d.is_dir() and _is_year(d.name) for d in folder.iterdir())
        except PermissionError:
            return False


def _is_year(name: str) -> bool:
    return name.isdigit() and len(name) == 4
