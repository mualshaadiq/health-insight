"""GET /api/data - range data, analysis and today's stored briefing."""
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)]

from _handler import make_handler  # noqa: E402

import core  # noqa: E402

handler = make_handler(core.handle_data)
