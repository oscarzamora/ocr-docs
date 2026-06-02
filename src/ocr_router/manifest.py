"""Manifest generation and tracking."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class ManifestEntry:
    """Single manifest entry for a processed document."""
    
    def __init__(self, 
                 filename: str,
                 category: Optional[str] = None,
                 issuer: Optional[str] = None,
                 owner: Optional[str] = None,
                 account: Optional[str] = None,
                 date: Optional[str] = None,
                 amount: Optional[str] = None,
                 ocr_confidence: float = 0.0,
                 routed_to: Optional[str] = None,
                 status: str = "processed"):
        self.filename = filename
        self.category = category
        self.issuer = issuer
        self.owner = owner
        self.account = account
        self.date = date
        self.amount = amount
        self.ocr_confidence = ocr_confidence
        self.routed_to = routed_to
        self.status = status
        self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            'filename': self.filename,
            'category': self.category,
            'issuer': self.issuer,
            'owner': self.owner,
            'account': self.account,
            'date': self.date,
            'amount': self.amount,
            'ocr_confidence': self.ocr_confidence,
            'routed_to': self.routed_to,
            'status': self.status,
            'timestamp': self.timestamp,
        }


class ManifestWriter:
    """Write manifest entries to CSV and JSONL."""
    
    def __init__(self, output_dir: Path, config: dict):
        self.output_dir = Path(output_dir)
        self.config = config.get('manifest', {})
        self.csv_path = self.output_dir / self.config.get('csv_path', 'manifest.csv')
        self.jsonl_path = self.output_dir / self.config.get('jsonl_path', 'manifest.jsonl')
        self.include_fields = self.config.get('include_fields', [])
        
        # Initialize CSV if needed
        if not self.csv_path.exists():
            self._init_csv()
    
    def _init_csv(self):
        """Initialize CSV with headers."""
        headers = self.include_fields or [
            'filename', 'category', 'issuer', 'owner', 'account', 
            'date', 'amount', 'ocr_confidence', 'routed_to', 'timestamp'
        ]
        
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
    
    def append_entry(self, entry: ManifestEntry):
        """Append entry to CSV and JSONL."""
        data = entry.to_dict()
        
        # Write to CSV
        headers = self.include_fields or list(data.keys())
        with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writerow({k: data.get(k, '') for k in headers})
        
        # Write to JSONL
        with open(self.jsonl_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data) + '\n')
    
    def get_entries(self) -> list[Dict[str, Any]]:
        """Read all manifest entries from JSONL."""
        entries = []
        if self.jsonl_path.exists():
            with open(self.jsonl_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        entries.append(json.loads(line))
        return entries
