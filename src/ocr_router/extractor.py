"""Text and metadata extraction from OCR'd PDFs."""

import re
from pathlib import Path
from typing import Optional

from pypdf import PdfReader

# Month name lookup tables for date normalization
_MONTH_NAMES: dict[str, int] = {
    'january': 1, 'february': 2, 'march': 3, 'april': 4,
    'may': 5, 'june': 6, 'july': 7, 'august': 8,
    'september': 9, 'october': 10, 'november': 11, 'december': 12,
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4,
    'jun': 6, 'jul': 7, 'aug': 8,
    'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}

_DEFAULT_AMOUNT_LABELS = [
    'total amount due', 'amount due', 'total due',
    'inv total', 'invoice total',
    'new balance', 'statement balance', 'balance due',
    'payment due', 'net pay', 'total',
]

# Amounts below this value are likely noise (line-item numbers mistaken for $)
_MIN_AMOUNT = 1.00


class MetadataExtractor:
    """Extract metadata from PDF text."""

    def __init__(self, config: dict):
        self.config = config
        self.extraction_patterns = config.get('extraction_patterns', {})
        self.known_issuers: dict[str, str] = config.get('known_issuers', {})

    def extract_from_text(self, text: str, filename: str) -> dict:
        """Extract all metadata from PDF text, returning a flat dict."""
        account_info = self._extract_account(text)
        # Filename-stem date wins when present (e.g. '20250221_...pdf' ->
        # 2025-02-21). This is the most reliable signal for bank/CC
        # statements that name files by statement date and prevents the
        # year-only fallback from collapsing a whole batch to YYYY-01-01
        # (which then collides into one filename and silently overwrites).
        date = self._extract_date_from_filename(filename) or self._extract_date(text)
        # Track whether date came from year-only fallback (stored as YYYY-01-01)
        date_year_only = bool(date and date.endswith('-01-01') and
                              not re.search(r'january\s+1|jan\.?\s+1\b|1/1/', text.lower()))
        return {
            'date': date,
            'date_year_only': date_year_only,
            'amount': self._extract_amount(text),
            'currency': self._extract_currency(text),
            'account': account_info['value'],
            'account_masked': account_info['masked'],
            'account_digits': account_info['digits'],
            'owner': self._extract_owner(text),
            'issuer': self._extract_issuer(text, filename),
        }

    # ------------------------------------------------------------------
    # Currency detection
    # ------------------------------------------------------------------

    def _extract_currency(self, text: str) -> str:
        """Detect the primary currency used in the document.

        Counts symbol occurrences and picks the most frequent. Defaults to
        ``$`` on ties or when none are present. This avoids the historical
        bug where a single stray ``€`` (from OCR noise / a copyright glyph
        misread / a tiny FX-conversion footnote) flipped a clearly-USD
        statement's amount into a euro-formatted filename.
        """
        counts = {
            '$':  len(re.findall(r'\$\s*[\d,]+', text)),
            'S/': len(re.findall(r'\bS/\.?\s*[\d,]+', text)),
            '€':  len(re.findall(r'€\s*[\d,]+|[\d,]+\s*€', text)),
            '£':  len(re.findall(r'£\s*[\d,]+', text)),
        }
        winner, n = max(counts.items(), key=lambda kv: (kv[1], kv[0] == '$'))
        if n == 0:
            return '$'
        # Bias toward $ on ties: any non-$ winner must beat $ count strictly.
        if counts['$'] >= n:
            return '$'
        return winner

    # ------------------------------------------------------------------
    # Date extraction
    # ------------------------------------------------------------------

    def _normalize_date(self, year: int, month: int, day: int) -> str:
        return f"{year:04d}-{month:02d}-{day:02d}"

    # Labels that introduce a date-of-birth — these dates should be skipped.
    _DOB_LABELS = re.compile(
        r'(?:date\s+of\s+birth|d\.?o\.?b\.?|birth\s+date|born\s+on)[:\s]*',
        re.IGNORECASE,
    )

    def _strip_dob_dates(self, text: str) -> str:
        """Remove dates near a DATE OF BIRTH label regardless of before/after order."""
        dob_pattern = re.compile(
            r'(?:date\s+of\s+birth|d\.?o\.?b\.?|birth\s+date|born\s+on)',
            re.IGNORECASE,
        )
        date_pattern = re.compile(r'\d{1,2}[/\-]\d{1,2}[/\-]\d{4}')

        # Find all DOB label positions
        dob_positions = [m.start() for m in dob_pattern.finditer(text)]
        if not dob_positions:
            return text

        # Collect all date spans that fall within ±200 chars of any DOB label
        window = 200
        spans_to_blank: list[tuple[int, int]] = []
        for date_match in date_pattern.finditer(text):
            ds, de = date_match.start(), date_match.end()
            if any(abs(ds - pos) <= window for pos in dob_positions):
                spans_to_blank.append((ds, de))

        if not spans_to_blank:
            return text

        # Replace matched date spans with spaces (preserve character positions)
        chars = list(text)
        for start, end in spans_to_blank:
            for i in range(start, end):
                chars[i] = ' '
        return ''.join(chars)

    def _extract_date_from_filename(self, filename: str) -> Optional[str]:
        """Extract a date from the filename stem.

        Recognises (in order):
          * ``YYYYMMDD`` at start (e.g. ``20250221_account.pdf``)
          * ``YYYY-MM-DD`` / ``YYYY.MM.DD`` / ``YYYY_MM_DD`` at start
          * ``YYYY-MM`` / ``YYYY.MM`` at start (day defaults to 01)

        Returns ``None`` if nothing matches. The bare year case is left to
        the in-text extractor so we don't shadow it with a false-positive
        date when the filename only contains a year as part of an
        unrelated token.
        """
        if not filename:
            return None
        stem = Path(filename).stem
        # YYYYMMDD followed by a separator or end-of-stem
        m = re.match(r'^(\d{4})(\d{2})(\d{2})(?:[_\-]|$)', stem)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return self._normalize_date(y, mo, d)
        # YYYY[sep]MM[sep]DD
        m = re.match(r'^(\d{4})[\-._](\d{1,2})[\-._](\d{1,2})(?:\D|$)', stem)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if 1 <= mo <= 12 and 1 <= d <= 31:
                return self._normalize_date(y, mo, d)
        # YYYY[sep]MM (no day)
        m = re.match(r'^(\d{4})[\-._](\d{1,2})(?:\D|$)', stem)
        if m:
            y, mo = int(m.group(1)), int(m.group(2))
            if 1 <= mo <= 12:
                return self._normalize_date(y, mo, 1)
        return None

    def _extract_date(self, text: str) -> Optional[str]:
        """Extract and normalize a date to YYYY-MM-DD.

        Priority order:
        1. Billing/service/statement period start (e.g. "Billing Period: Apr 08 - May 07, 2026")
        2. Full word-month date (e.g. "April 8, 2026" or "Apr 08, 2026")
        3. Numeric MM/DD/YYYY (DOB-labeled dates excluded)
        4. ISO YYYY-MM-DD
        5. Standalone 4-digit year (fallback for tax forms)
        """
        # Strip dates that are labeled as date-of-birth before any search
        text = self._strip_dob_dates(text)

        # 1. Billing period range — capture start month+day, year at end of range
        billing = re.search(
            r'(?:billing period|service period|statement period)[:\s]+'
            r'([A-Za-z]{3,9})\s+(\d{1,2})\s*[-–—to]+\s*[A-Za-z]{3,9}\s+\d{1,2}[,\s]+(\d{4})',
            text, re.IGNORECASE,
        )
        if billing:
            month_num = _MONTH_NAMES.get(billing.group(1).lower())
            if month_num:
                return self._normalize_date(int(billing.group(3)), month_num, int(billing.group(2)))

        # 2. Named month (full or abbreviated): "Apr 08, 2026" / "April 8 2026"
        named = re.search(
            r'\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b',
            text, re.IGNORECASE,
        )
        if named:
            month_num = _MONTH_NAMES.get(named.group(1).lower())
            if month_num:
                return self._normalize_date(int(named.group(3)), month_num, int(named.group(2)))

        # 3. Numeric MM/DD/YYYY or MM-DD-YYYY — iterate all matches (first valid wins).
        #    When the first field is > 12 it cannot be a US-format month: try
        #    DD/MM/YYYY (European / LATAM convention used by Peruvian statements).
        for numeric in re.finditer(r'\b(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})\b', text):
            a, b, y = int(numeric.group(1)), int(numeric.group(2)), int(numeric.group(3))
            if 1 <= a <= 12 and 1 <= b <= 31:
                return self._normalize_date(y, a, b)
            if 1 <= b <= 12 and 1 <= a <= 31:  # DD/MM/YYYY fallback
                return self._normalize_date(y, b, a)

        # 4. ISO YYYY-MM-DD
        iso = re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', text)
        if iso:
            return self._normalize_date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))

        # 5. Standalone year only (e.g. tax form year "2021")
        year_only = re.search(r'\b(20\d{2}|19\d{2})\b', text)
        if year_only:
            return f"{year_only.group(1)}-01-01"

        return None

    # ------------------------------------------------------------------
    # Amount extraction
    # ------------------------------------------------------------------

    def _is_valid_amount(self, raw: str) -> bool:
        """Return True if raw string is a plausible financial amount (>= $1.00)."""
        try:
            return float(raw.replace(',', '')) >= _MIN_AMOUNT
        except ValueError:
            return False

    def _extract_amount(self, text: str) -> Optional[str]:
        """Extract monetary amount, preferring labeled totals over bare dollar signs."""
        labels = self.extraction_patterns.get('amount_labels', _DEFAULT_AMOUNT_LABELS)
        for label in labels:
            pattern = re.compile(
                re.escape(label) + r'[:\s]*(?:\$|S/\.?|€|£)?\s*([\d,]+(?:\.\d{1,2})?)',
                re.IGNORECASE,
            )
            m = pattern.search(text)
            if m:
                raw = m.group(1).replace(',', '')
                if self._is_valid_amount(raw):
                    return raw

        # Fallback: first dollar OR soles amount that includes cents
        m = re.search(r'(?:\$|S/\.?)\s*([\d,]+\.\d{2})', text)
        if m:
            raw = m.group(1).replace(',', '')
            if self._is_valid_amount(raw):
                return raw
        return None

    # ------------------------------------------------------------------
    # Account extraction
    # ------------------------------------------------------------------

    def _extract_account(self, text: str) -> dict:
        """Return {'value': str|None, 'masked': bool, 'digits': int}.

        'digits' is the count of significant digits shown (4 for last-4, etc.).
        A full account number has masked=False.
        """
        # Full unmasked account number (6+ consecutive digits near label)
        full = re.search(
            r'(?:account|acct|account\s*#|account\s*number)[:\s#]*(\d{6,})',
            text, re.IGNORECASE,
        )
        if full:
            val = full.group(1)
            return {'value': val, 'masked': False, 'digits': len(val)}

        # "ending in 1234" / "last 4 digits: 1234"
        ending = re.search(
            r'(?:ending\s+in|last\s+(\d+)\s+digits?)[:\s]*(\d{3,4})',
            text, re.IGNORECASE,
        )
        if ending:
            n = int(ending.group(1)) if ending.group(1) else 4
            digits = ending.group(2)
            return {'value': digits, 'masked': True, 'digits': n}

        # XXXX1234 / ****1234 / ####1234
        masked = re.search(r'(?:[Xx*#]{2,})(\d{3,4})', text)
        if masked:
            digits = masked.group(1)
            return {'value': digits, 'masked': True, 'digits': len(digits)}

        # Short suffix near account label
        short = re.search(
            r'(?:account|acct)[\s#]*[\dX*#\s\-]*?(\d{4})\b',
            text, re.IGNORECASE,
        )
        if short:
            return {'value': short.group(1), 'masked': True, 'digits': 4}

        return {'value': None, 'masked': False, 'digits': 0}

    # ------------------------------------------------------------------
    # Owner extraction
    # ------------------------------------------------------------------

    def _extract_owner(self, text: str) -> Optional[str]:
        """Extract owner/cardholder name from greeting or label."""
        patterns = [
            r'(?:Dear|Greetings)[,\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
            r'(?:Cardholder|Account Holder|Account Owner|Holder)[:\s]+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)',
        ]
        for pattern in patterns:
            m = re.search(pattern, text)
            if m:
                return m.group(1)
        return None

    # ------------------------------------------------------------------
    # Issuer extraction
    # ------------------------------------------------------------------

    def _normalize_for_match(self, s: str) -> str:
        """Normalize text for issuer matching.

        & and - are REMOVED (not space-replaced) so 'AT&T'→'att', 'T-Mobile'→'tmobile'.
        Dots and commas become spaces (URL segment separator).
        """
        s = s.lower()
        s = re.sub(r'[&\-]', '', s)        # AT&T→att, t-mobile→tmobile
        s = re.sub(r'[\.\,]', ' ', s)       # deltadentalwa.com → deltadentalwa com
        return re.sub(r'\s+', ' ', s).strip()

    def _match_issuer_key(self, norm_key: str, norm_text: str) -> bool:
        """Match with word boundary at start, flexible at end.

        Prevents 'att' from matching 'seattle' and 'chase' from matching 'purchase',
        while allowing 'deltadental' to match 'deltadentalwa'.
        """
        return bool(re.search(r'\b' + re.escape(norm_key), norm_text))

    def _extract_issuer(self, text: str, filename: str) -> Optional[str]:
        """Match known issuers against document header (first 1500 chars) and filename.

        Limiting to the header prevents false positives from names that appear
        incidentally in the body (e.g. 'AT&T' as employer on a dental EOB).
        Longer keys are tried first so specific names beat short aliases.
        The footer (last 1000 chars) is also searched for card/contract details
        that may appear at document end (e.g. AMEX card type on a contract summary).
        """
        norm_header = self._normalize_for_match(text[:1500])
        norm_footer = self._normalize_for_match(text[-1000:]) if len(text) > 1500 else ''
        norm_filename = self._normalize_for_match(filename)

        for key in sorted(self.known_issuers, key=len, reverse=True):
            norm_key = self._normalize_for_match(key)
            if (self._match_issuer_key(norm_key, norm_header) or
                    self._match_issuer_key(norm_key, norm_footer) or
                    self._match_issuer_key(norm_key, norm_filename)):
                return self.known_issuers[key]
        return None


class PdfTextExtractor:
    """Extract text from PDF files."""
    
    @staticmethod
    def extract_text(pdf_path: Path) -> str:
        """Extract text from PDF."""
        try:
            reader = PdfReader(pdf_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text()
            # Strip null bytes and binary control characters that corrupt keyword matching
            text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', text)
            return text
        except Exception as e:
            return f"ERROR: Could not extract text - {str(e)}"
    
    @staticmethod
    def extract_text_with_confidence(pdf_path: Path) -> tuple[str, float]:
        """Extract text and estimate confidence (basic)."""
        text = PdfTextExtractor.extract_text(pdf_path)
        
        # Simple confidence estimation: more text = likely better OCR
        confidence = min(len(text) / 5000.0, 1.0)  # Max at 5000 chars
        
        return text, confidence
