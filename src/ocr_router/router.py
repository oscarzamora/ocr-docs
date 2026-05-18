"""Document routing logic."""

import re
from pathlib import Path
from typing import Optional


class DocumentRouter:
    """Route documents to folders based on classification."""

    def __init__(self, config: dict):
        self.config = config
        self.categories = config.get('categories', {})
        self.route_templates = config.get('route_templates', {})
        self.owners = config.get('owners', [])

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify_document(self, text: str) -> Optional[str]:
        """Score each category by keyword matches; return best match above threshold."""
        text_lower = text.lower()
        min_score: int = self.config.get('min_classification_score', 2)
        scores: dict[str, int] = {}
        for category, keywords in self.categories.items():
            hits = sum(1 for kw in keywords if kw.lower() in text_lower)
            if hits >= min_score:
                scores[category] = hits
        return max(scores, key=scores.get) if scores else 'Uncategorized'

    # ------------------------------------------------------------------
    # Route path
    # ------------------------------------------------------------------

    def build_route_path(self, category: str, metadata: dict) -> str:
        """Build destination folder path from template, stripping Unknown segments."""
        template = self.route_templates.get(
            category,
            self.route_templates.get('default', '{category}/{issuer}/{year}'),
        )

        year = 'Unknown'
        if metadata.get('date') and len(metadata['date']) >= 4:
            year = metadata['date'][:4]

        # Blank/template IRS forms: Tax Returns + year-only date + no amount
        # → route to Tax Returns\Forms instead of the flat root
        if (category == 'Tax Returns'
                and metadata.get('date_year_only')
                and not metadata.get('amount')):
            return 'Tax Returns\\Forms'

        # CC/Bank contracts (no amount, contrato/contract keyword) → issuer root, no year
        contract_keywords = self.config.get('contract_keywords',
            ['contrato', 'contract terms', 'cardmember agreement', 'account agreement',
             'terms and conditions', 'hoja resumen'])
        text_lower = metadata.get('_text_lower', '')   # populated by classify if available
        is_contract = (
            category in ('Credit Card Statements', 'Bank Account & Statements')
            and not metadata.get('amount')
        )
        if is_contract:
            issuer_seg = metadata.get('issuer') or 'Unknown'
            template = self.route_templates.get(category,
                self.route_templates.get('default', '{category}/{issuer}/{year}'))
            # Use issuer-level path (drop {year} segment)
            path = template.replace('/{year}', '').replace('{issuer}', issuer_seg)
            # strip any remaining Unknown segments
            parts = [p for p in path.replace('\\', '/').split('/') if p and p != 'Unknown']
            return '\\'.join(parts)

        replacements = {
            'category': category or 'Uncategorized',
            'issuer': metadata.get('issuer') or 'Unknown',
            'owner': metadata.get('owner') or 'Unknown',
            'account': metadata.get('account') or 'Unknown',
            'year': year,
            'date': metadata.get('date') or '',
            'amount': metadata.get('amount') or '',
        }

        path = template
        for key, value in replacements.items():
            path = path.replace(f'{{{key}}}', str(value))

        # Remove any path segment whose value is 'Unknown'
        parts = re.split(r'[/\\]', path)
        parts = [p for p in parts if p and p != 'Unknown']
        return '\\'.join(parts)

    # ------------------------------------------------------------------
    # Filename normalization
    # ------------------------------------------------------------------

    def normalize_filename(self, filename: str, metadata: dict) -> str:
        """Build a normalized filename using the user naming convention.

        Rules:
        - Monthly categories (Bills, CC statements, etc.): YYYY.MM
        - All others: YYYY.MM.DD
        - Account # appended for Bills, Credit Cards, Mortgage/HELOC
          Full account → (726251363)
          Masked account → (Last 4 1234)
        - Amount appended as $X.XX when present
        - Parts joined with ' - '
        """
        category = metadata.get('category', '')
        ext = Path(filename).suffix

        monthly_cats = set(self.config.get('monthly_categories', ['Bills']))
        account_cats = set(self.config.get('account_in_filename_categories', [
            'Bills', 'Credit Card Statements', 'Mortgage & Home Equity Accounts',
        ]))
        doc_types: dict[str, str] = self.config.get('doc_types', {})

        # Contracts: no amount + CC/Bank → override doc type, force dated (not monthly) format
        is_contract = (
            category in ('Credit Card Statements', 'Bank Account & Statements')
            and not metadata.get('amount')
        )
        effective_doc_type = 'Contract' if is_contract else doc_types.get(category, '')
        effective_monthly = monthly_cats - ({'Credit Card Statements', 'Bank Account & Statements'}
                                            if is_contract else set())

        # --- Date component ---
        date_str = metadata.get('date') or ''  # ISO YYYY-MM-DD
        date_year_only = metadata.get('date_year_only', False)
        date_part = ''
        if len(date_str) >= 4:
            year = date_str[:4]
            month = date_str[5:7] if len(date_str) >= 7 else ''
            day = date_str[8:10] if len(date_str) >= 10 else ''
            if date_year_only:
                date_part = year                            # e.g. 2021 (tax form, year only)
            elif category in effective_monthly:
                date_part = f"{year}.{month}" if month else year
            elif day:
                date_part = f"{year}.{month}.{day}"
            elif month:
                date_part = f"{year}.{month}"
            else:
                date_part = year

        # --- Smart name: Issuer + DocType ---
        issuer = (metadata.get('issuer') or '').strip()
        doc_type = effective_doc_type
        name_parts = [p for p in [issuer, doc_type] if p]
        smart_name = ' '.join(name_parts) if name_parts else Path(filename).stem[:50]

        # --- Account component (only for applicable categories) ---
        account_part = ''
        if category in account_cats:
            account = (metadata.get('account') or '').strip()
            if account:
                if metadata.get('account_masked'):
                    n = metadata.get('account_digits') or 4
                    account_part = f"(Last{n} {account})"   # e.g. (Last4 1234)
                else:
                    account_part = f"({account})"

        # --- Amount component (always 2 decimal places) ---
        amount_part = ''
        no_amount_cats = set(self.config.get('no_amount_categories', []))
        raw_amount = metadata.get('amount')
        currency = metadata.get('currency', '$')
        if raw_amount and category not in no_amount_cats:
            try:
                amount_part = f"{currency}{float(raw_amount):.2f}"
            except (ValueError, TypeError):
                pass

        # --- Assemble ---
        components = [p for p in [date_part, smart_name, account_part, amount_part] if p]
        normalized = ' - '.join(components)

        # Strip characters invalid in Windows filenames (preserve $, parens, dots, spaces)
        normalized = re.sub(r'[<>:"/\\|?*]', '_', normalized)

        return f"{normalized}{ext}"
