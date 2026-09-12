"""Normalising the name a document is stored and served under.

The upload's file name is chosen entirely by the caller. It becomes a database
key and part of a URL, so it is reduced to a plain base name before it is used
for anything.
"""

import os
import re

# Control characters (a NUL among them) have no place in a name that is stored
# and echoed back in JSON and HTML.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_LENGTH = 200
_FALLBACK = "document"


def safe_document_name(filename: str | None) -> str:
    """A directory-free, control-character-free, length-bounded document name.

    "../../../etc/passwd" becomes "passwd": the traversal is meaningless here
    because nothing is written to that path, but a name that reads like an
    escape attempt should not be stored, echoed back, or put in a URL.
    """
    name = _CONTROL_CHARS_RE.sub("", (filename or "").replace("\\", "/")).strip()
    name = os.path.basename(name).strip(". ")
    if not name:
        return _FALLBACK
    if len(name) > _MAX_LENGTH:
        stem, extension = os.path.splitext(name)
        extension = extension[:16]
        name = stem[: _MAX_LENGTH - len(extension)] + extension
    return name or _FALLBACK
