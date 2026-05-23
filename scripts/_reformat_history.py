"""One-shot script: reformat PROCESSED_PDFS.md with standard Markdown tables."""

OUT = r"C:\Users\ozamo\OneDrive\Documents\__downloads__\PROCESSED_PDFS.md"


def md_table(rows, headers):
    """Render rows as a standard GitHub-Flavored Markdown table."""
    def esc(s):
        return str(s).replace("|", "\\|")
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    sep   = "| " + " | ".join("-" * w for w in widths) + " |"
    head  = "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |"
    lines = [head, sep]
    for row in rows:
        lines.append("| " + " | ".join(esc(str(cell)).ljust(widths[i]) for i, cell in enumerate(row)) + " |")
    return "\n".join(lines)


H = ["#", "Original File", "Category", "Issuer", "New Name", "Amount", "Destination"]

B1 = [
    ("1",  "911 Carrera T.pdf",                          "—",                          "—",                    "2025.12.29 - 911 Carrera T.pdf",                                "—",          r"__downloads__ (renamed in place)"),
    ("2",  "canon receipt.pdf",                          "Receipts",                   "B&H Photo",            r"2025.11.18 - B&H Photo Receipt - $549.99.pdf",                 "$549.99",    r"Receipts, Payment, Warranty"),
    ("3",  "canon support page.pdf",                     "—",                          "—",                    "—",                                                             "—",          "DELETED"),
    ("4",  "ContratoTarjetaCredito-BIP-4221.pdf",        "Credit Card Statements",     "AMEX Gold",            r"2024.08.10 - AMEX Gold Contract.pdf",                          "—",          r"Credit Card Statements\AMEX Gold"),
    ("5",  "DetailedBillMay2026.pdf",                    "Bills",                      "T-Mobile",             r"2026.04 - T-Mobile Monthly - $304.29 - Detailed.pdf",          "$304.29",    r"Bills\T-Mobile\2026"),
    ("6",  "eob (1).pdf",                                "Health Statements & Results","Delta Dental",         r"2026.01.29 - Delta Dental EOB - $139.00.pdf",                  "$139.00",    r"Health Statements & Results\2026"),
    ("7",  "eob (2).pdf",                                "Health Statements & Results","Delta Dental",         r"2026.05.07 - Delta Dental EOB - $349.00.pdf",                  "$349.00",    r"Health Statements & Results\2026"),
    ("8",  "eob (3).pdf",                                "Health Statements & Results","Delta Dental",         r"2026.02.05 - Delta Dental EOB - $3900.00.pdf",                 "$3900.00",   r"Health Statements & Results\2026"),
    ("9",  "eob.pdf",                                    "Health Statements & Results","Delta Dental",         r"2026.01.22 - Delta Dental EOB - $1183.00.pdf",                 "$1183.00",   r"Health Statements & Results\2026"),
    ("10", "f5500ez.pdf",                                "Tax Returns",                "—",                    r"2021 - 5500-EZ Return.pdf",                                    "—",          r"Tax Returns\Forms"),
    ("11", "fuerzapopular.pdf",                          "—",                          "—",                    "—",                                                             "—",          "DELETED"),
    ("12", "Invoice #10515.pdf",                         "Bills",                      "Pure Water Pool Svc",  r"2026.05 - Pure Water Pool Service Monthly - $120.00.pdf",      "$120.00",    r"Bills\Pure Water Pool Service\2026"),
    ("13", "North Hills Weston Hills - Arch Docs.pdf",   "Real Estate & HOA",          "Weston Hills",         r"2006.06.01 - Weston Hills HOA - Architectural Design.pdf",     "—",          r"Weston Hills"),
    ("14", "pest control.pdf (OCR)",                     "Rental Expenses",            "—",                    r"2026.05 - Pest Control Service - $100.00.pdf",                 "$100.00",    r"Rental\Rental Expenses\2026"),
    ("15", "Proof of Payment.pdf",                       "Receipts",                   "Chase",                r"2026.04.28 - Chase Proof of Payment - $280.00.pdf",            "$280.00",    r"Receipts, Payment, Warranty"),
    ("16", "Report_000977.pdf (OCR)",                    "Health Statements & Results","—",                    r"2026.05.08 - FamilyMemberB Contact Lens Prescription.pdf",           "—",          r"Health Statements & Results\2026"),
    ("17", "Report_000980.pdf (OCR)",                    "Credit Card Statements",     "Fidelity",             r"2026.04 - Fidelity Rewards Visa Contract.pdf",                 "—",          r"Credit Card Statements\Fidelity"),
    ("18", "Report_000984.pdf (OCR)",                    "Credit Card Statements",     "Citibank",             r"2026.05 - Citibank Credit - $1650.00.pdf",                     "$1650.00",   r"FamilyMemberB\Credit Cards"),
    ("19", "Report_000985.pdf (OCR)",                    "Health Statements & Results","BlueCross BlueShield", r"2026.05.05 - BlueCross BlueShield EOB.pdf",                    "—",          r"Health Statements & Results\2026"),
    ("20", "Report_001013.pdf (OCR)",                    "Mortgage & Home Equity",     "Citibank",             r"2021.05 - Citibank Mortgage - $439091.00.pdf",                 "$439091.00", r"Mortgage & Home Equity Accounts\3047\2021"),
    ("21", "Report_001034.pdf (OCR)",                    "—",                          "—",                    r"2026.05.12 - Parking Ticket Tallahassee - $35.00.pdf",         "$35.00",     r"FamilyMemberA\__Expenses__"),
    ("22", "Statement for Apr 30, 2026.pdf",             "Paystubs",                   "Microsoft",            r"2026.04.16 - Microsoft Paycheck - $4789.99.pdf",               "$4789.99",   r"Paystubs\2026"),
    ("23", "Statement for May 15, 2026.pdf",             "Paystubs",                   "Microsoft",            r"2026.05.01 - Microsoft Paycheck - $4543.76.pdf",               "$4543.76",   r"Paystubs\2026"),
    ("24", "Structural Improvements V21.pdf",            "Real Estate & HOA",          "Weston Hills",         r"2006.06.01 - Weston Hills HOA - Structural Improvements.pdf",  "—",          r"Weston Hills"),
    ("25", "SummaryBillMay2026.pdf",                     "Bills",                      "T-Mobile",             r"2026.04 - T-Mobile Monthly - $304.29 - Summary.pdf",           "$304.29",    r"Bills\T-Mobile\2026"),
]

B2 = [
    ("1",  "2025.10 - Interbank Amex Gold (Last4 0234).pdf",                     "Credit Card Statements",     "AMEX Gold",          "same", "—",        r"Credit Card Statements\AMEX Gold\2025"),
    ("2",  "2025.10.30 - DTCC Badge Acknowledgment.pdf",                         "Notices",                    "DTCC",               "same", "—",        r"Careers\DTCC"),
    ("3",  "2025.11 - Interbank Amex Gold (Last4 0234).pdf",                     "Credit Card Statements",     "AMEX Gold",          "same", "—",        r"Credit Card Statements\AMEX Gold\2025"),
    ("4",  "2025.12 - Interbank Amex Gold (Last4 0234) S/2.51.pdf",              "Credit Card Statements",     "AMEX Gold",          "same", "S/2.51",   r"Credit Card Statements\AMEX Gold\2025"),
    ("5",  "2026.01 - Interbank Amex Gold (Last4 0234) S/5.01.pdf",              "Credit Card Statements",     "AMEX Gold",          "same", "S/5.01",   r"Credit Card Statements\AMEX Gold\2026"),
    ("6",  "2026.02 - Interbank Amex Gold (Last4 0234).pdf",                     "Credit Card Statements",     "AMEX Gold",          "same", "—",        r"Credit Card Statements\AMEX Gold\2026"),
    ("7",  "2026.02.18 - Premera Claim Update - OwnerB.pdf",                   "Health Statements & Results","Premera",            "same", "—",        r"OwnerB"),
    ("8",  "2026.02.20 - Identity Verification - DTCC.pdf",                      "Notices",                    "DTCC",               "same", "—",        r"Careers\DTCC"),
    ("9",  "2026.03 - Interbank Amex Gold (Last4 0234).pdf",                     "Credit Card Statements",     "AMEX Gold",          "same", "—",        r"Credit Card Statements\AMEX Gold\2026"),
    ("10", "2026.04 - Fidelity Rewards Visa FamilyMemberA $932.02.pdf",                "Credit Card Statements",     "Fidelity",           "same", "$932.02",  r"FamilyMemberA\Fidelity"),
    ("11", "2026.04 - FPL Electric $320.98.pdf",                                 "Bills",                      "FPL",                "same", "$320.98",  r"Bills\FPL\2026"),
    ("12", "2026.04 - Interbank Amex Gold (Last4 0234) S/314.09.pdf",            "Credit Card Statements",     "AMEX Gold",          "same", "S/314.09", r"Credit Card Statements\AMEX Gold\2026"),
    ("13", "2026.04 - Pure Water Pool Service $180.00.pdf",                      "Bills",                      "Pure Water Pool Svc","same", "$180.00",  r"Bills\Pure Water Pool Service\2026"),
    ("14", "2026.04.22 - ContactsDirect Acuvue Oasys for FamilyMemberB $125.57.pdf",  "Receipts",                   "ContactsDirect",     "same", "$125.57",  r"FamilyMemberB"),
    ("15", "2026.06.20 - Wedding Invitation - Daniel & Prosha.pdf",              "Personal",                   "—",                  "same", "—",        r"Personal"),
    ("16", "2025.12.29 - 911 Carrera T.pdf",                                     "—",                          "—",                  "—",    "—",        "KEPT IN PLACE (temp file)"),
]

t1 = md_table(B1, H)
t2 = md_table(B2, H)

content = (
    "# Processing History\n\n"
    "---\n\n"
    "## 2025-06-29 — 25 files processed\n\n"
    "**Notes:**\n"
    "- Files 3 & 11 deleted (temp/junk: canon support page, fuerzapopular)\n"
    "- File 1 renamed in place (temp vehicle doc: 911 Carrera T)\n"
    "- Files 13 & 24 moved to Weston Hills with appended descriptors\n"
    "- File 15 (Proof of Payment) redirected to Receipts, Payment, Warranty\n"
    "- File 16 (Report_000977 - FamilyMemberB contact lens RX) held for iteration/reclassification\n"
    "- File 21: parking ticket — FamilyMemberA drives Oscar's Tesla\n"
    "- T-Mobile DetailedBill / SummaryBill filed with -Detailed/-Summary suffix\n\n"
    "**Pending iteration:**\n"
    "- `Report_000977.pdf` — FamilyMemberB's contact lens Rx (2026.05.08). Date erroneously picks up DOB (2007-05-10). "
    "Fix: add vision/RX keywords to Health category; exclude DATE OF BIRTH label from date extraction.\n\n"
    + t1 + "\n\n"
    "---\n\n"
    "## 2026-05-17 — 16 files processed\n\n"
    "All files were previously named correctly and only needed routing to proper folders.\n\n"
    + t2 + "\n"
)

with open(OUT, "w", encoding="utf-8", newline="\r\n") as f:
    f.write(content)
print("Written OK —", OUT)
