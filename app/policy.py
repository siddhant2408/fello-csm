"""Worklist policy shared by the product query and the historical evaluation, so the
backtest measures the same decision a CSM sees."""

CAPACITY = 10  # account reviews per CSM per week
WINDOW_DAYS = (60, 180)  # renewal due this many days ahead; >=60 leaves time to act
COOLDOWN_DAYS = 28  # a reviewed account leaves the worklist for four weeks
