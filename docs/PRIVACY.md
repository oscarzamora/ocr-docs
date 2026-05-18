# Privacy & Security Policy

## Sensitive Data

This repository processes and stores sensitive document metadata:
- Personal names (owners, cardholders)
- Account numbers (last 4 digits)
- Financial amounts
- Dates and issuer information
- OCR confidence scores

## Guidelines

### DO NOT Commit:
- `.env` files with credentials
- `manifest.csv` or `manifest.jsonl` files (contains personal data)
- `human-feedback.csv` (may contain edited personal data)
- `learned-rules.json` if it contains patterns from real documents
- Personal or test PDFs
- Database files

### DO Commit:
- `.env.example` (template only)
- `config/routing-config.yaml` (no real data)
- Code and tests
- Documentation

### Repository Access:
- Keep this repository **PRIVATE**
- Only share with authorized team members
- Do not push to public repositories
- Review git history before sharing

### Data Handling:
- Generated manifests are temporary - delete after processing
- Archive processed PDFs separately from source control
- Use external storage (cloud) for long-term document retention
- Consider encrypting sensitive backup files

### Git Safety:
```powershell
# Configure git to prevent accidental commits
git config core.sshCommand "ssh -i YOUR_PRIVATE_KEY"
git config user.email "your.email@private.com"
git config user.name "Your Name"

# Review before any commit
git diff --cached
```

## Best Practices

1. **Before each commit:** Review changes with `git diff --cached`
2. **Use .gitignore:** Ensure all sensitive patterns are ignored
3. **Backup manifests:** Store separately from git repository
4. **Encrypt archives:** Use 7z or similar with password for historical data
5. **Audit access:** Regularly review who has repository access

## Compliance

If handling documents for:
- Healthcare (HIPAA)
- Finance (PCI-DSS, SOX)
- Legal (attorney-client privilege)
- Personal data (GDPR, CCPA)

Consult compliance requirements before using in production.
