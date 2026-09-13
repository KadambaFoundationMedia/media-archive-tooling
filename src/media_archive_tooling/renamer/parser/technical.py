"""Technical annotations, tracking ID, and source sequence extraction."""
import re
import uuid
from typing import Tuple, List, Optional, Any
from ..models import FileMetadata

TRACKING_ID_REGEX = re.compile(r"_ID-([0-9a-fA-F]{8})(?=\.|$)", re.IGNORECASE)
EDITED_REGEX = re.compile(r"_edited(?=\.|$|_ID-)", re.IGNORECASE)
CORRUPTED_REGEX = re.compile(r"\b(corrupted|damaged|broken)\b", re.IGNORECASE)
TECHNICAL_FLAGS_REGEX = re.compile(
    r"\b(recovered|copy|final|hq|part-?\d+|track-?\d+|cd-?\d+)\b",
    re.IGNORECASE
)
# Source/sequence identifiers such as A019, A022F, R09_0004, sr-006
SOURCE_SEQ_REGEX = re.compile(
    r"^(?:([A-Z]\d{3,4}[A-Z]?)|(R\d{2}_\d{4})|(sr-\d{3}))\b",
    re.IGNORECASE
)


def extract_or_generate_tracking_id(filename: str, registry: Optional[Any] = None) -> Tuple[str, str, bool]:
    """Find existing _ID-xxxxxxxx or generate a new 8-hex character ID with registry collision check.
    
    Returns:
        (tracking_id, cleaned_filename, was_existing)
    """
    match = TRACKING_ID_REGEX.search(filename)
    if match:
        tracking_id = match.group(1).lower()
        # Remove the tracking ID token from the working filename
        cleaned = filename[:match.start()] + filename[match.end():]
        return tracking_id, cleaned, True
    else:
        while True:
            new_id = uuid.uuid4().hex[:8].lower()
            if registry is None or not registry.get_file(new_id):
                break
        return new_id, filename, False


def extract_technical_metadata(filename: str) -> Tuple[str, FileMetadata]:
    """Extract technical flags (_edited, corrupted, source ids, annotations).
    
    Returns:
        (cleaned_filename_stem, FileMetadata)
    """
    metadata = FileMetadata()
    working = filename
    
    # Check edited flag
    if EDITED_REGEX.search(working):
        metadata.edited = True
        working = EDITED_REGEX.sub("", working)
        
    # Check corrupted flag
    if CORRUPTED_REGEX.search(working):
        metadata.corrupted = True
        working = CORRUPTED_REGEX.sub("", working)
        
    # Check other technical annotations
    found_annotations = []
    for match in TECHNICAL_FLAGS_REGEX.finditer(working):
        found_annotations.append(match.group(1))
    if found_annotations:
        metadata.other_annotations.extend(found_annotations)
        working = TECHNICAL_FLAGS_REGEX.sub("", working)
        
    # Check source/sequence identifier at start or standalone
    source_match = SOURCE_SEQ_REGEX.search(working.strip())
    if source_match:
        metadata.source_sequence_id = source_match.group(0)
        # Strip from working text if it is a prefix token
        working = working[source_match.end():].lstrip(" _-")
        
    # Check combination clues
    if re.search(r"\b(and|with|&|\+|plus|followed\s+by)\b", working, re.IGNORECASE):
        metadata.possible_combination = True
        
    return working.strip(), metadata
