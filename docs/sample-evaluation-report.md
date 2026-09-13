# Sample Evaluation Report — Tool 1 (Renamer)

Date: 2026-09-13  
Target Directory: `sample-files/`  
Evaluation Execution: `media-archive renamer sample-files --dry-run`  
Log File: `.renamer/logs/renamer_20260913_134300.jsonl`  
Summary CSV: `.renamer/logs/renamer_20260913_134300_summary.csv`

## 1. Objective Overview (Post-R-022 Correction)

Evaluation of Tool 1 across the full local test archive `sample-files/` was executed in standard dry-run analysis mode (`initial`). Following the correction of review finding **R-022**, diagnostic notes and downstream routing needs (missing/provisional WHERE, unidentified class WHAT, combination splitting) are strictly separated from immediate human review.

Files with missing or provisional metadata continue safely through the progressive pipeline without blocking the human review queue. Human review is reserved strictly for genuine factual contradictions, unresolvable ambiguities, and corruptions.

| Metric | Count | Percentage |
| :--- | :--- | :--- |
| **Total Media Files Analyzed** | 260 | 100.0% |
| **Safe Automatic Proposals (No human review)** | 255 | 98.1% |
| **Flagged for Immediate Human Review** | 5 | 1.9% |
| **Filename Collisions Detected** | 0 | 0.0% |
| **Combination Candidates Routed to Split** | 2 | 0.8% |

---

## 2. Objective Pipeline Behavior Categories

Every analyzed file is assigned an objective pipeline behavior category per R-022:

| Category | Count | Percentage | Description |
| :--- | :--- | :--- | :--- |
| `safe_automatic` | 61 | 23.5% | Complete WHEN, WHAT, WHERE, high confidence, no conflicts or review reasons. |
| `downstream_enrichment` | 192 | 73.8% | Safe partial improvement (e.g. date/location extracted, or source wording + ID preserved); routed to Tool 2/3 (media enrichment) or Tool 7 (class classification) without blocking human queue. |
| `downstream_split` | 2 | 0.8% | Combination recording clue detected; retains source stem + tracking ID for Tools 5/6 splitting. |
| `human_review_required` | 5 | 1.9% | Genuine contradiction requiring human judgment (e.g. filename date conflicts with container folder year). |
| `blocked_error` | 0 | 0.0% | Unhandled exceptions, validation failures, or fatal processing errors. |

---

## 3. Specific Breakdown of Human Review Reasons

Only 5 files require human review across the 260 analyzed files. All 5 are genuine factual contradictions between filename date and container folder year:

| Review Reason | Frequency | Description & Remediation Path |
| :--- | :--- | :--- |
| `Filename date '2011-12-29' conflicts with folder year '2012'` | 2 | File stem indicates December 2011 but parent folder is 2012. Requires human portal verification of correct year. |
| `Filename date '2011-12-30' conflicts with folder year '2012'` | 2 | File stem indicates December 2011 but parent folder is 2012. Requires human portal verification of correct year. |
| `Filename date '2011-12-31' conflicts with folder year '2012'` | 1 | File stem indicates December 2011 but parent folder is 2012. Requires human portal verification of correct year. |

---

## 4. Downstream Pipeline Routing Counts

Files that do not require human review but have unresolved or provisional metadata are tagged with explicit downstream tool routing:

| Downstream Target | File Count | Description |
| :--- | :--- | :--- |
| `tool_2_3_media_enrichment` | 154 | WHERE is unresolved or provisional; audio transcript / recording context in Tools 2/3 can identify location. |
| `tool_7_class_classification` | 80 | WHAT is unresolved (e.g. lecture title/topic missing); audio classification in Tool 7 will identify scripture/topic. |
| `tool_5_6_split_combination` | 2 | Multi-part or composite recording clue detected; Tools 5/6 will split audio and create sub-items. |

---

## 5. Key Representative Samples

### Clean Automatic Proposals
- `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
  -> `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-f345b4ea.mp3`
- `2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3`
  -> `2011-08-20_KKS_Jaya-Radha-Madhava_Oslo-no_ID-f80233b6.mp3`
- `KKS-Home-program_feb-2015_amsterdam.mp3`
  -> `2015-02-DD_KKS_Home-program_Amsterdam-nl_ID-a2429fd0.mp3`

### Safe Downstream Enrichment Routing (Missing WHERE / Provisional)
- `2012-05-13_KKS_BG-8-19.mp3`
  -> `2012-05-13_KKS_BG-8-19_ID-ca7fec22.mp3`
  *(Diagnostic note: `WHERE is unresolved`, downstream: `tool_2_3_media_enrichment`, `needs_review=False`)*

### Safe Downstream Class Routing (Missing WHAT)
- `2012-05-13_Sydney.mp3`
  -> `2012-05-13_Sydney_ID-xxxxxxxx.mp3`
  *(Diagnostic note: `WHAT is unresolved`, downstream: `tool_7_class_classification`, `needs_review=False`)*

### Unsplit Combination Preservation (R-013 / R-022)
- `2011-08-20_KKS_SB-1-19-31-with-radha-madhava_oslo_fi.wma`
  -> `2011-08-20_KKS_SB-1-19-31-with-radha-madhava_oslo_fi_ID-f6196366.wma`
  *(Diagnostic note: combination clue, downstream: `tool_5_6_split_combination`, `needs_review=False`)*

### Genuine Human Review Queue (Conflict)
- `2012/2011-12-30_KKS_Lecture.mp3`
  -> `2011-12-30_KKS_Lecture_ID-xxxxxxxx.mp3`
  *(Review reason: `Filename date '2011-12-30' conflicts with folder year '2012'`, `needs_review=True`)*

---

## 6. Verification Reproducibility

To re-run and verify this evaluation report locally:

```bash
# Execute dry-run analysis on sample-files
.venv/bin/media-archive renamer sample-files --dry-run

# Run full automated regression suite (66 tests)
.venv/bin/pytest -v
```
