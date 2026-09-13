# Sample Evaluation Report — Tool 1 (Renamer)

Date: 2026-09-13  
Target Directory: `sample-files/`  
Evaluation Execution: `media-archive renamer sample-files --dry-run`  
Log File: `.renamer/logs/renamer_20260913_144834.jsonl`  
Summary CSV: `.renamer/logs/renamer_20260913_144834_summary.csv`

## 1. Objective Overview (Post-R-022 / R-023 Correction)

Evaluation of Tool 1 across the full local test archive `sample-files/` was executed in standard dry-run analysis mode (`initial`). Following the correction of review findings **R-022** and **R-023**, diagnostic notes and downstream routing needs are strictly separated from immediate human review, and downstream routing respects the fixed pipeline responsibilities:

- Generic unresolved WHAT routes to Tool 2 (Media Database Reviewer) and Tool 5 (Content Discoverer) rather than assuming the recording is already a class;
- Tool 7 (Class Classification) is reserved for items already established as a Class where specific class WHAT is unidentified;
- Tool 2 (Media Database Reviewer) and Tool 3 (Travel Schedule Reviewer) provide database reference reconciliation for missing/provisional metadata;
- Human review is reserved strictly for genuine factual contradictions, unresolvable ambiguities, and corruptions.

| Metric | Count | Percentage |
| :--- | :--- | :--- |
| **Total Media Files Analyzed** | 260 | 100.0% |
| **Safe Automatic Proposals (No human review)** | 255 | 98.1% |
| **Flagged for Immediate Human Review** | 5 | 1.9% |
| **Filename Collisions Detected** | 0 | 0.0% |
| **Combination Candidates Routed to Split** | 2 | 0.8% |

---

## 2. Objective Pipeline Behavior Categories

Every analyzed file is assigned an objective pipeline behavior category:

| Category | Count | Percentage | Description |
| :--- | :--- | :--- | :--- |
| `downstream_enrichment` | 196 | 75.4% | Safe partial improvement (e.g. date/location extracted, or source wording + ID preserved); routed to downstream database review, content discovery, or class classification without blocking human queue. |
| `safe_automatic` | 57 | 21.9% | Complete WHEN, WHAT, WHERE, high confidence, no downstream routing or review reasons. |
| `human_review_required` | 5 | 1.9% | Genuine contradiction requiring human judgment (e.g. filename date conflicts with container folder year). |
| `downstream_split` | 2 | 0.8% | Combination recording clue detected; retains source stem + tracking ID for Tools 5/6 splitting. |
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
| `tool_2_3_media_enrichment` | 151 | WHERE/WHEN is unresolved or provisional; Baserow Media table (Tool 2) or Travel Schedule table (Tool 3) review can supply location/date evidence. |
| `tool_2_media_database_review` | 80 | Generic WHAT is unresolved; Tool 2 Media Database Reviewer checks whether an existing Baserow Media record for this recording supplies the logical title/topic/scripture. |
| `tool_5_content_discovery` | 80 | Generic WHAT remains unresolved into content processing; Tool 5 Content Discoverer audio analysis determines whether content is Class, Mantra singing, or Combination before class-specific resolution. |
| `tool_7_class_classification` | 8 | Established Class items where specific class scripture/topic is unidentified; Tool 7 resolves specific class WHAT. |
| `tool_5_6_split_combination` | 2 | Multi-part or composite recording clue detected; Tools 5/6 will split audio and create sub-items. |

---

## 5. Key Representative Samples

### Clean Automatic Proposals
- `HH Kadamba Kanana Swami - SB 3.6.6 - Sweden - 27_8_15.mp3`
  -> `2015-08-27_KKS_SB-3-6-6_Sweden-se_ID-37d088fe.mp3`
- `2011-08-20_KKS_Jaya-radha-madhava_oslo_fi.mp3`
  -> `2011-08-20_KKS_Jaya-Radha-Madhava_Oslo-no_ID-c6d8a389.mp3`
- `KKS-Home-program_feb-2015_amsterdam.mp3`
  -> `2015-02-DD_KKS_Home-program_Amsterdam-nl_ID-fbdb5680.mp3`

### Safe Downstream Enrichment Routing (Missing WHERE / Provisional)
- `2012-05-13_KKS_BG-8-19.mp3`
  -> `2012-05-13_KKS_BG-8-19_ID-0e43ab80.mp3`
  *(Diagnostic note: `WHERE is unresolved`, downstream: `tool_2_3_media_enrichment`, `needs_review=False`)*

### Safe Generic Unresolved WHAT Routing (R-023)
- `2012-05-13_Sydney.mp3`
  -> `2012-05-13_Sydney_ID-xxxxxxxx.mp3`
  *(Diagnostic note: `WHAT is unresolved`, downstream: `tool_2_media_database_review`, `tool_5_content_discovery`, `needs_review=False`)*

### Established Class with Unidentified Class WHAT Routing (R-023)
- `03 BRNO LEKCE STEREO JET.mp3`
  -> `03 BRNO LEKCE STEREO JET_ID-xxxxxxxx.mp3`
  *(Diagnostic note: `Unidentified class WHAT`, downstream: `tool_7_class_classification`, `needs_review=False`)*

### Unsplit Combination Preservation (R-013 / R-022)
- `2011-08-20_KKS_SB-1-19-31-with-radha-madhava_oslo_fi.wma`
  -> `2011-08-20_KKS_SB-1-19-31-with-radha-madhava_oslo_fi_ID-0b54db8b.wma`
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

# Run full automated regression suite (67 tests)
.venv/bin/pytest -v
```
