# Media Archive Tooling Overview

This document records the current project-wide tool sequence. Detailed behavior remains governed by each tool's finalized build plan once that plan exists.

## Current sequence

1. **Tool 1 — Renamer**
2. **Tool 2 — Media Database Reviewer**
3. **Tool 3 — Travel Schedule Reviewer**
4. **Tool 4 — Media Database Updater**
5. **Tool 1 — Renamer (update/enrich pass)**
6. **Tool 5 — Content Discoverer** — determine Class, mantra singing/kirtan, or combination content
7. **Tool 6 — File Cutter** — split combination files
8. **Tool 7 — Class Type Discoverer**
9. **Tool 8 — Class Trimmer**
10. **Tool 9 — Class Gain Booster**
11. **Tool 10 — Questions Gain Booster**
12. **Tool 11 — Processed Media Organiser**
13. **Tool 1 — Renamer (final update/enrich pass)**

The repeated Tool 1 entries are additional passes of the same Renamer, not separate tool numbers.

## Tool 11 boundary currently established

Tool 11 is the **Processed Media Organiser**. Its purpose is to move fully processed media into the correct final archive location based on the resolved media category / class or kirtan type and the corresponding configured category path.

Detailed Tool 11 planning is intentionally deferred until the project reaches that stage. No destination-path grammar, move/commit policy, retention policy, or implementation details are finalized here.
