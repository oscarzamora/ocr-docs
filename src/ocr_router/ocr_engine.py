"""OCR engine — uses ocrmypdf (Tesseract-based, fully silent) with fallback to PDF24."""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bundled Tesseract and GhostScript shipped with PDF24
_PDF24_TESSERACT  = Path(r'C:\Program Files\PDF24\tesseract\tesseract.exe')
_PDF24_TESSDATA   = Path(r'C:\Program Files\PDF24\tesseract\tessdata')
_PDF24_GS_BIN     = Path(r'C:\Program Files\PDF24\gs\bin')

# jbig2enc binary (enables optimize=2 in ocrmypdf for B&W scans)
# Bundled in tools/bin/ so the repo is self-sufficient; fallback to user-local install.
_REPO_ROOT        = Path(__file__).parents[2]
_JBIG2ENC_BIN     = _REPO_ROOT / 'tools' / 'bin'
_JBIG2_CANDIDATES = [
    _JBIG2ENC_BIN / 'jbig2.exe',
    _REPO_ROOT / 'venv' / 'Scripts' / 'jbig2.exe',
]
_PNGQUANT_CANDIDATES = [
    _JBIG2ENC_BIN / 'pngquant.exe',
    _REPO_ROOT / 'venv' / 'Scripts' / 'pngquant.exe',
    Path(r'C:\Program Files\pngquant\pngquant.exe'),
    Path(r'C:\Program Files (x86)\pngquant\pngquant.exe'),
]


class OcrEngine:
    """OCR PDFs silently using ocrmypdf + Tesseract.

    Strategy (in order):
    1. ocrmypdf with bundled PDF24 Tesseract  — fully silent, no GUI
    2. ocrmypdf with system Tesseract          — if PDF24 not installed
    3. Skip (return False)                     — Tesseract not found anywhere
    """

    def __init__(self, config: dict):
        self.config = config.get('ocr_settings', {})
        # Legacy PDF24 path kept for is_available() compatibility
        self.pdf24_path = self.config.get('pdf24_path',
            r'C:\Program Files\PDF24\pdf24-Ocr.exe')
        self._tesseract_path = self._find_tesseract()
        self._pngquant_path = self._find_pngquant()
        self._jbig2_path = self._find_working_jbig2()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ocr_pdf(self, input_pdf: Path, output_pdf: Optional[Path] = None,
                optimize: int = 3, image_dpi: Optional[int] = None) -> bool:
        """Add a text layer to input_pdf (or convert an image) and write to output_pdf.

        Returns True on success, False on failure.

        Args:
            optimize: ocrmypdf optimize level.
                3 = lossy jbig2+pngquant (~40% smaller, good for B&W financial statements).
                1 = lossless only (use for color photos / JPEG→PDF to avoid quality loss).
            image_dpi: DPI hint when input is a raster image (JPEG/PNG). None for PDFs.
        """
        if output_pdf is None:
            output_pdf = input_pdf.parent / f"{input_pdf.stem}_ocr.pdf"

        if not self._tesseract_path:
            logger.warning("Tesseract not found — cannot OCR %s", input_pdf.name)
            return False

        try:
            import ocrmypdf
            env = self._tesseract_env()
            orig_path = os.environ.get('PATH', '')
            orig_tessdata = os.environ.get('TESSDATA_PREFIX', '')
            os.environ['PATH'] = env['PATH']
            if 'TESSDATA_PREFIX' in env:
                os.environ['TESSDATA_PREFIX'] = env['TESSDATA_PREFIX']
            try:
                # pngquant and jbig2 are required for optimize >=2 in ocrmypdf.
                # If unavailable/unhealthy, downgrade to optimize=1 so OCR succeeds.
                effective_optimize = optimize
                if optimize >= 2 and not self._pngquant_path:
                    logger.warning(
                        "pngquant not found; falling back optimize=%s -> 1 for %s",
                        optimize,
                        input_pdf.name,
                    )
                    effective_optimize = 1
                if effective_optimize >= 2 and not self._jbig2_path:
                    logger.warning(
                        "jbig2 not usable; falling back optimize=%s -> 1 for %s",
                        effective_optimize,
                        input_pdf.name,
                    )
                    effective_optimize = 1

                kwargs: dict = dict(
                    language='eng+spa',
                    deskew=True,
                    skip_text=True,
                    progress_bar=False,
                    tesseract_timeout=120,
                    optimize=effective_optimize,
                )
                if image_dpi is not None:
                    kwargs['image_dpi'] = image_dpi
                ocrmypdf.ocr(input_pdf, output_pdf, **kwargs)
            finally:
                os.environ['PATH'] = orig_path
                if orig_tessdata:
                    os.environ['TESSDATA_PREFIX'] = orig_tessdata
                elif 'TESSDATA_PREFIX' in os.environ:
                    del os.environ['TESSDATA_PREFIX']
            return True
        except Exception as e:
            logger.warning("ocrmypdf failed for %s: %s", input_pdf.name, e)
            return False

    def is_available(self) -> bool:
        """Return True if OCR is possible (Tesseract found)."""
        return self._tesseract_path is not None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_tesseract(self) -> Optional[Path]:
        """Return the best available Tesseract executable path."""
        if _PDF24_TESSERACT.exists():
            return _PDF24_TESSERACT
        # Check system PATH
        sys_tess = shutil.which('tesseract')
        if sys_tess:
            return Path(sys_tess)
        return None

    def _find_pngquant(self) -> Optional[Path]:
        """Resolve pngquant from PATH, configured path, or common install locations."""
        cfg_path = self.config.get('pngquant_path')
        if cfg_path:
            p = Path(cfg_path)
            if p.exists():
                return p

        # Prefer venv-local binary for reproducible behavior in this workspace.
        py_dir = Path(sys.executable).parent
        venv_local = py_dir / 'pngquant.exe'
        if venv_local.exists():
            return venv_local

        sys_pngquant = shutil.which('pngquant')
        if sys_pngquant:
            return Path(sys_pngquant)

        for candidate in _PNGQUANT_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    def _find_working_jbig2(self) -> Optional[Path]:
        """Find a jbig2 binary that can execute `--version` successfully."""
        candidates: list[Path] = []
        candidates.extend(_JBIG2_CANDIDATES)

        sys_jbig2 = shutil.which('jbig2')
        if sys_jbig2:
            candidates.append(Path(sys_jbig2))

        seen: set[str] = set()
        for candidate in candidates:
            c = str(candidate)
            if c in seen or not candidate.exists():
                continue
            seen.add(c)
            try:
                proc = subprocess.run(
                    [c, '--version'],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=5,
                )
                if proc.returncode == 0:
                    return candidate
            except Exception:
                continue
        return None

    def _tesseract_env(self) -> dict:
        """Build env vars so ocrmypdf finds the bundled Tesseract + GhostScript + tessdata."""
        env = os.environ.copy()
        env['PATH'] = self._sanitize_path_for_jbig2(env.get('PATH', ''))
        if self._tesseract_path == _PDF24_TESSERACT:
            # Prepend PDF24 Tesseract and GhostScript dirs to PATH
            tess_dir = str(_PDF24_TESSERACT.parent)
            gs_dir   = str(_PDF24_GS_BIN) if _PDF24_GS_BIN.exists() else ''
            extra    = os.pathsep.join(d for d in [tess_dir, gs_dir] if d)
            env['PATH'] = extra + os.pathsep + env.get('PATH', '')
            env['TESSDATA_PREFIX'] = str(_PDF24_TESSDATA)
        # Always add jbig2enc if available (enables optimize=3 lossy compression)
        if self._jbig2_path:
            env['PATH'] = str(self._jbig2_path.parent) + os.pathsep + env.get('PATH', '')
        # Persist pngquant usage rule by prepending discovered binary location
        # only when it differs from tools/bin (already prepended above).
        if self._pngquant_path and self._pngquant_path.parent != _JBIG2ENC_BIN:
            env['PATH'] = str(self._pngquant_path.parent) + os.pathsep + env.get('PATH', '')
        return env

    def _sanitize_path_for_jbig2(self, path_value: str) -> str:
        """Remove path segments that expose broken jbig2 binaries.

        ocrmypdf probes jbig2 eagerly and can emit traceback noise when a broken
        binary exists in PATH. If we don't have a validated jbig2 path, strip
        any segment that contains a jbig2 executable.
        """
        if self._jbig2_path:
            return path_value

        cleaned: list[str] = []
        for segment in path_value.split(os.pathsep):
            seg = segment.strip()
            if not seg:
                continue
            candidate = Path(seg) / 'jbig2.exe'
            if candidate.exists():
                continue
            cleaned.append(seg)
        return os.pathsep.join(cleaned)
