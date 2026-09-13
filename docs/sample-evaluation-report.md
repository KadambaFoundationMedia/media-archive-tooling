# Sample Evaluation Report — Tool 1 (Renamer)

Date: 2026-09-13  
Target Directory: `sample-files/`  
Evaluation Execution: `media-archive renamer sample-files --dry-run`  
Log File: `.renamer/logs/renamer_20260913_121311.jsonl`  
Summary CSV: `.renamer/logs/renamer_20260913_121311_summary.csv`

## 1. Objective Overview

Evaluation of Tool 1 across the full local test archive `sample-files/` was executed in standard dry-run analysis mode (`initial`). No ground-truth human labeling exists for this uncurated archive set; therefore, metrics reflect objective pipeline behavior, resolution confidence, and review flagging without asserting subjective correctness claims.

| Metric | Count | Percentage |
| :--- | :--- | :--- |
| **Total Media Files Analyzed** | 260 | 100.0% |
| **Clean Automatic Proposals** (No review flags) | 88 | 33.8% |
| **Flagged for Human Review** | 172 | 66.2% |
| **Filename Collisions Detected** | 0 | 0.0% |
| **Combination Candidates Held for Splitting** | 2 | 0.8% |

---

## 2. Objective Confidence Categories

Every analyzed file is assigned a pipeline behavior category based on parsing state, confidence, and whether manual review or downstream splitting is needed:

| Category | Count | Description |
| :--- | :--- | :--- |
| `automatic_candidate` | 61 | Complete WHEN, WHAT, WHERE, high confidence, no conflicts or review reasons. |
| `provisional_candidate` | 56 | Contains valid interpretations but with partial/provisional confidence (e.g. approximate year/month date or geocoded location). |
| `review_candidate` | 131 | Blocked on missing key fields, explicit folder/filename conflict, or combination clues. |
| `unresolved_candidate` | 12 | Completely unresolved or bare recorder identifiers requiring human input or downstream enrichment. |

---

## 3. Specific Breakdown of Review Reasons

A total of 247 review reason triggers were recorded across the 172 flagged files (some files have multiple reasons):

| Trigger Reason | Frequency | Description & Remediation Path |
| :--- | :--- | :--- |
| `WHERE is unresolved` | 141 | Location could not be determined from filename, folder context, or geocoding. Awaiting Tool 2/3 transcription context or human review. |
| `WHAT is unresolved` | 80 | Title, topic, or scripture verse missing or ambiguous. Awaiting Tool 2/3 classification or human portal review. |
| `WHEN is unresolved` | 13 | Recording date missing entirely. Awaiting metadata/transcript inference or human entry. |
| `WHEN resolution is provisional` | 7 | Ambiguous date format resolved provisionally using regional context (e.g. non-US DD-MM-YYYY precedence). |
| `Filename date conflicts with folder year` | 5 | Filename indicates late December (e.g. 2011-12-29/30/31) while container folder indicates subsequent year (2012). Flagged for human verification. |
| `File has combination clue` | 2 | Source filename indicates multi-part or composite recording (e.g. `JRM and class`). Retains source stem + ID and held for Tools 5/6. |

---

## 4. Key Representative Samples

### Clean Automatic Proposals
- `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
  -> `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-c5a53738.mp3`
- `2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3`
  -> `2011-08-20_KKS_Jaya-Radha-Madhava_Oslo-no_ID-1066f9ca.mp3`
- `KKS-Home-program_feb-2015_amsterdam.mp3`
  -> `2015-02-DD_KKS_Home-program_Amsterdam-nl_ID-95f0302f.mp3`

### Unsplit Combination Preservation (R-013)
- `2011-08-20_KKS_SB-1-19-31-with-radha-madhava_oslo_fi.wma`
  -> `2011-08-20_KKS_SB-1-19-31-with-radha-madhava_oslo_fi_ID-b3be1274.wma`
  *(Preserves original descriptive stem and tracking ID until Tools 5/6 split it)*

### Specific Scripture WHAT Preservation (R-012)
- `2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney_edited.mp3`
  -> `2012-05-13_KKS_BG-8-19-Sundayfeast_Sydney-au_edited_ID-xxxxxxxx.mp3`
  *(Preserves specific Sundayfeast descriptor without dropping it)*

### Direct Location Evidence Outranking Folder Context (R-014)
- `A022F 03-10-25 SB 4.9.11 Nezkracena Farma KD.mp3` in `Prague-Oct-2003/`
  -> `2003-10-25_KKS_SB-4-9-11_Krsna-Dvur-cz_ID-xxxxxxxx.mp3`
  *(Direct filename evidence `Farma KD` resolves to `Krsna-Dvur-cz`, correctly outranking ancestor folder `Praha`)*

---

## 5. Verification Reproducibility

To re-run and verify this evaluation report locally:

```bash
# Execute dry-run analysis on sample-files
.venv/bin/media-archive renamer sample-files --dry-run

# Run full automated regression suite
.venv/bin/pytest -v
```
