#!/usr/bin/env python
# See COPYRIGHT file at the top of the source tree.
from lsst.fgcmcal.fgcmFitCycle import FgcmFitCycleTask

import matplotlib
matplotlib.use("Agg")  # noqa

FgcmFitCycleTask.parseAndRun()
