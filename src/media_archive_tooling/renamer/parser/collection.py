"""Collection and sibling grammar analysis for a batch of files in a directory."""
import re
from pathlib import Path
from typing import List, Dict, Any, Optional

SOURCE_PREFIX_PATTERN = re.compile(r"^([A-Z]\d{3,4}[A-Z]?)\b", re.IGNORECASE)
SEQUENCE_PREFIX_PATTERN = re.compile(r"^(\d{1,3})\b")
DATE_PATTERN_YY_MM_DD = re.compile(r"\b(\d{2})[-._](\d{2})[-._](\d{2})\b")


class CollectionGrammar:
    """Inferred structural grammar across siblings in a directory."""
    def __init__(self, directory: Path, filenames: List[str]):
        self.directory = directory
        self.filenames = [f for f in filenames if not f.startswith(".")]
        self.has_source_prefix = False
        self.has_sequence_prefix = False
        self.repeated_date_format: Optional[str] = None
        self.common_tokens: List[str] = []
        self._analyze()

    def _analyze(self):
        if len(self.filenames) < 2:
            return

        source_matches = 0
        sequence_matches = 0
        yy_mm_dd_matches = 0

        for name in self.filenames:
            stem = Path(name).stem
            if SOURCE_PREFIX_PATTERN.match(stem):
                source_matches += 1
            elif SEQUENCE_PREFIX_PATTERN.match(stem):
                sequence_matches += 1
            if DATE_PATTERN_YY_MM_DD.search(stem):
                yy_mm_dd_matches += 1

        total = len(self.filenames)
        # If majority have source prefix (e.g. A019, A020):
        if source_matches / total >= 0.5:
            self.has_source_prefix = True

        # If majority have sequence prefix (e.g. 07, 08):
        if sequence_matches / total >= 0.5:
            self.has_sequence_prefix = True

        # If majority have YY-MM-DD pattern:
        if yy_mm_dd_matches / total >= 0.5:
            self.repeated_date_format = "YY-MM-DD"

    def describe(self) -> str:
        parts = []
        if self.has_source_prefix:
            parts.append("<source-id>")
        elif self.has_sequence_prefix:
            parts.append("<sequence-index>")
        if self.repeated_date_format:
            parts.append(f"<{self.repeated_date_format}>")
        return " ".join(parts) if parts else "heterogeneous"
