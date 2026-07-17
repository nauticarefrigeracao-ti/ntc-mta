"""Garante que o root do repo esteja no sys.path para os testes importarem slack_notify."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
