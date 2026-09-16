"""`python -m presentation_designer.cli …` — то же, что консольная команда presentation-designer."""

import sys

from presentation_designer.cli.main import main

sys.exit(main())
