# Stage 3.5.9 ETF tracking review addendum

- Prefer official Tracking_Error when present.
- If Tracking_Error is missing but Tracking_Difference is present, treat ETF tracking quality as partially available, not missing.
- Do not claim Tracking_Difference equals Tracking_Error.
- If ETF_Tracking_Quality_Mode is Tracking_Difference_Fallback, note it as a labelled fallback.
- Only call tracking quality missing when both Tracking_Error and Tracking_Difference are blank.
