"""Corrected nanoMINER reproduction with a Kimi K3 core agent."""

from .config import KimiSettings
from .pipeline import ExtractionRun, run_extraction

__all__ = ["ExtractionRun", "KimiSettings", "run_extraction"]
__version__ = "0.1.0"
