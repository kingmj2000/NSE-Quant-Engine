# ETF Metadata Review Addendum - Stage 3.5.6

When reviewing ETF metadata:

- Treat AUM as valid only if it is a realistic crore value.
- If AUM is between 100000 and 200000 and integer-like, suspect it is an AMFI Scheme_Code, not AUM.
- If AUM equals AMFI_Scheme_Code, reject it.
- Do not call AUM resolved just because AUM_Cr is populated.
- Prefer `AUM_Validation` if present.
- If AUM is rejected as scheme-code-like, say: "AUM source parsed incorrectly; AUM remains unresolved."
- Do not downgrade the stock universe because of ETF metadata issues.
