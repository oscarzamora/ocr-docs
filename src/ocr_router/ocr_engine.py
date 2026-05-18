"""OCR engine — uses ocrmypdf (Tesseract-based, fully silent) with fallback to PDF24."""

import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Bundled Tesseract shipped with PDF24
_PDF24_TESSERACT = Path(r'C:\Program Files\PDF24\tesseract\tesseract.exe')
_PDF24_TESSDATA  = Path(r'C:\Program Files\PDF24\tesseract\tessdata')


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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ocr_pdf(self, input_pdf: Path, output_pdf: Optional[Path] = None) -> bool:
        """Add a text layer to input_pdf and write to output_pdf.

        Returns True on success, False on failure.
        Only processes files that have no extractable text (confidence == 0).
        """
        if output_pdf is None:
            output_pdf = input_pdf.parent / f"{input_pdf.stem}_ocr.pdf"

        if not self._tesseract_path:
            logger.warning("Tesseract not found — cannot OCR %s", input_pdf.name)
            return False

        try:
            import ocrmypdf
            # Point ocrmypdf at the bundled Tesseract by setting env before call
            env = self._tesseract_env()
            orig_path = os.environ.get('PATH', '')
            orig_tessdata = os.environ.get('TESSDATA_PREFIX', '')
            os.environ['PATH'] = env['PATH']
            if 'TESSDATA_PREFIX' in env:
                os.environ['TESSDATA_PREFIX'] = env['TESSDATA_PREFIX']
            try:
                ocrmypdf.ocr(
                    input_pdf,
                    output_pdf,
                    language='eng+spa',      # English + Spanish for Peruvian docs
                    deskew=True,
                    skip_text=True,          # skip pages that already have text
                    progress_bar=False,
                    tesseract_timeout=120,
                    optimize=0,              # skip GS optimization (not required)
                )
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
        import shutil
        sys_tess = shutil.which('tesseract')
        if sys_tess:
            return Path(sys_tess)
        return None

    def _tesseract_env(self) -> dict:
        """Build env vars so ocrmypdf finds the bundled Tesseract + tessdata."""
        env = os.environ.copy()
        if self._tesseract_path == _PDF24_TESSERACT:
            # Prepend PDF24 Tesseract dir to PATH
            tess_dir = str(_PDF24_TESSERACT.parent)
            env['PATH'] = tess_dir + os.pathsep + env.get('PATH', '')
            env['TESSDATA_PREFIX'] = str(_PDF24_TESSDATA)
        return env
