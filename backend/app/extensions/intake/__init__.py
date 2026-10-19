"""Mixed-format intake: one Document shape for docx, pdf, images and text.

Public surface::

    from app.extensions.intake import read, Document
    from app.extensions.intake.batch import main as batch_main
"""

from .model import Block, Document, Issue, Table
from .readers import read, sha256_file
from .router import route

__all__ = ["Block", "Document", "Issue", "Table", "read", "route", "sha256_file"]
