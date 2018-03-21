# See COPYRIGHT file at the top of the source tree.

from __future__ import division, absolute_import, print_function
from past.builtins import xrange

import sys
import traceback

import numpy as np

import lsst.pex.config as pexConfig
import lsst.pipe.base as pipeBase
import lsst.afw.table as afwTable
from lsst.daf.base.dateTime import DateTime
import lsst.daf.persistence.butlerExceptions as butlerExceptions
from lsst.meas.algorithms.sourceSelector import sourceSelectorRegistry

import time

import fgcm

__all__ = ['FgcmBuildStarsConfig', 'FgcmBuildStarsTask']


class FgcmBuildStarsConfig(pexConfig.Config):
    """Config for FgcmBuildStarsTask"""

    remake = pexConfig.Field(
        doc="Remake visit catalog and stars even if they are already in the butler tree.",
        dtype=bool,
        default=False,
    )
    minPerBand = pexConfig.Field(
        doc="Minimum observations per band",
        dtype=int,
        default=2,
    )
    matchRadius = pexConfig.Field(
        doc="Match radius (arcseconds)",
        dtype=float,
        default=1.0,
    )
    isolationRadius = pexConfig.Field(
        doc="Isolation radius (arcseconds)",
        dtype=float,
        default=2.0,
    )
    densityCutNside = pexConfig.Field(
        doc="Density cut healpix nside",
        dtype=int,
        default=128,
    )
    densityCutMaxPerPixel = pexConfig.Field(
        doc="Density cut number of stars per pixel",
        dtype=int,
        default=1000,
    )
    matchNside = pexConfig.Field(
        doc="Healpix Nside for matching",
        dtype=int,
        default=4096,
    )
    coarseNside = pexConfig.Field(
        doc="Healpix coarse Nside for partitioning matches",
        dtype=int,
        default=8,
    )
    zeropointDefault = pexConfig.Field(
        doc="Zeropoint default (arbitrary?)",
        dtype=float,
        default=25.0,
    )
    filterToBand = pexConfig.DictField(
        doc="filterName to band mapping",
        keytype=str,
        itemtype=str,
        default={},
    )
    requiredBands = pexConfig.ListField(
        doc="Bands required for each star",
        dtype=str,
        default=(),
    )
    referenceBand = pexConfig.Field(
        doc="Reference band for primary matches",
        dtype=str,
        default=None
    )
    referenceCCD = pexConfig.Field(
        doc="Reference CCD for scanning visits",
        dtype=int,
        default=13,
    )
    checkAllCcds = pexConfig.Field(
        doc="Check all CCDs.  Necessary for testing",
        dtype=bool,
        default=False,
    )
    visitDataRefName = pexConfig.Field(
        doc="dataRef name for the 'visit' field",
        dtype=str,
        default="visit"
    )
    ccdDataRefName = pexConfig.Field(
        doc="dataRef name for the 'ccd' field",
        dtype=str,
        default="ccd"
    )
    applyJacobian = pexConfig.Field(
        doc="Apply Jacobian correction?",
        dtype=bool,
        default=True
    )
    jacobianName = pexConfig.Field(
        doc="Name of field with jacobian correction",
        dtype=str,
        default="base_Jacobian_value"
    )
    sourceSelector = sourceSelectorRegistry.makeField(
        doc="How to select sources",
        default="science"
    )

    def setDefaults(self):
        sourceSelector = self.sourceSelector["science"]
        sourceSelector.setDefaults()

        sourceSelector.flags.bad = ['base_PixelFlags_flag_edge',
                                    'base_PixelFlags_flag_interpolatedCenter',
                                    'base_PixelFlags_flag_saturatedCenter',
                                    'base_PixelFlags_flag_crCenter',
                                    'base_PixelFlags_flag_bad',
                                    'base_PixelFlags_flag_interpolated',
                                    'base_PixelFlags_flag_saturated',
                                    'slot_Centroid_flag',
                                    'slot_ApFlux_flag']

        sourceSelector.doFlags = True
        sourceSelector.doUnresolved = True
        sourceSelector.doSignalToNoise = True
        sourceSelector.doIsolated = True

        sourceSelector.signalToNoise.fluxField = 'slot_ApFlux_flux'
        sourceSelector.signalToNoise.errField = 'slot_ApFlux_fluxSigma'
        sourceSelector.signalToNoise.minimum = 10.0
        sourceSelector.signalToNoise.maximum = 1000.0

        sourceSelector.unresolved.maximum = 0.5

class FgcmBuildStarsRunner(pipeBase.ButlerInitializedTaskRunner):
    """Subclass of TaskRunner for fgcmBuildStarsTask

    fgcmBuildStarsTask.run() takes a number of arguments, one of which is the
    butler (for persistence and mapper data), and a list of dataRefs
    extracted from the command line.  Note that FGCM runs on a large set of
    dataRefs, and not on single dataRef/tract/patch.
    This class transforms the process arguments generated by the ArgumentParser
    into the arguments expected by FgcmBuildStarsTask.run().
    This runner does not use any parallelization.

    """

    # TaskClass = FgcmBuildStarsTask

    # only need a single butler instance to run on
    @staticmethod
    def getTargetList(parsedCmd):
        """
        Return a list with one element: a tuple with the butler and
        list of dataRefs
        """
        # we want to combine the butler with any (or no!) dataRefs
        return [(parsedCmd.butler, parsedCmd.id.refList)]

    def precall(self, parsedCmd):
        return True

    def __call__(self, args):
        """
        Parameters
        ----------
        args: Tuple with (butler, dataRefList)

        Returns
        -------
        None if self.doReturnResults is False
        A pipe.base.Struct containing these fields if self.doReturnResults is True:
           dataRefList: the provided data references
        """
        butler, dataRefList = args

        task = self.TaskClass(config=self.config, log=self.log)

        exitStatus = 0
        if self.doRaise:
            results = task.run(butler, dataRefList)
        else:
            try:
                results = task.run(butler, dataRefList)
            except Exception as e:
                exitStatus = 1
                task.log.fatal("Failed: %s" % e)
                if not isinstance(e, pipeBase.TaskError):
                    traceback.print_exc(file=sys.stderr)

        task.writeMetadata(butler)

        if self.doReturnResults:
            return [pipeBase.Struct(exitStatus=exitStatus,
                                    results=results)]
        else:
            return [pipeBase.Struct(exitStatus=exitStatus)]

    # turn off any multiprocessing

    def run(self, parsedCmd):
        """
        Run the task, with no multiprocessing

        Parameters
        ----------
        parsedCmd: ArgumentParser parsed command line
        """

        resultList = []

        if self.precall(parsedCmd):
            # profileName = parsedCmd.profile if hasattr(parsedCmd, "profile") else None
            # log = parsedCmd.log
            targetList = self.getTargetList(parsedCmd)
            # And call the runner on the first (and only) item in the list,
            #  which is a tuple of the butler and any dataRefs
            resultList = self(targetList[0])

        return resultList


class FgcmBuildStarsTask(pipeBase.CmdLineTask):
    """
    Build stars for the FGCM global calibration
    """

    ConfigClass = FgcmBuildStarsConfig
    RunnerClass = FgcmBuildStarsRunner
    _DefaultName = "fgcmBuildStars"

    def __init__(self, butler=None, **kwargs):
        """
        Instantiate an FgcmBuildStarsTask.

        Parameters
        ----------
        butler : lsst.daf.persistence.Butler
        """

        pipeBase.CmdLineTask.__init__(self, **kwargs)
        self.makeSubtask("sourceSelector")
        # Only log fatal errors from the sourceSelector
        self.sourceSelector.log.setLevel(self.sourceSelector.log.FATAL)


    @classmethod
    def _makeArgumentParser(cls):
        """Create an argument parser"""

        parser = pipeBase.ArgumentParser(name=cls._DefaultName)
        parser.add_id_argument("--id", "calexp", help="Data ID, e.g. --id visit=6789 (optional)")

        return parser

    # no saving of the config for now
    # def _getConfigName(self):
    #     return None

    # no saving of metadata for now
    def _getMetadataName(self):
        return None

    @pipeBase.timeMethod
    def run(self, butler, dataRefs):
        """
        Cross-match and make star list for FGCM Input

        Parameters
        ----------
        butler:  lsst.daf.persistence.Butler
        dataRefs: list of lsst.daf.persistence.ButlerDataRef
           Data references for the input visits
           If this is an empty list, all visits with src catalogs in
           the repository are used.
           Only one individual dataRef from a visit need be specified
           and the code will find the other source catalogs from
           each visit

        Returns
        -------
        pipe.base.Struct
            struct containing:
            * dataRefs: the provided data references consolidated
        """

        # Make the visit catalog if necessary
        if self.config.remake or not butler.datasetExists('fgcmVisitCatalog'):
            # we need to build visitCat
            visitCat = self._fgcmMakeVisitCatalog(butler, dataRefs)
        else:
            self.log.info("Found fgcmVisitCatalog.")
            visitCat = butler.get('fgcmVisitCatalog')

        # Compile all the stars
        if self.config.remake or not butler.datasetExists('fgcmStarObservations'):
            self._fgcmMakeAllStarObservations(butler, visitCat)
        else:
            self.log.info("Found fgcmStarObservations")

        if self.config.remake or (not butler.datasetExists('fgcmStarIds') or
                                  not butler.datasetExists('fgcmStarIndices')):
            self._fgcmMatchStars(butler, visitCat)
        else:
            self.log.info("Found fgcmStarIds and fgcmStarIndices")

        # The return value could be the visitCat, if anybody wants that.
        return visitCat

    def _fgcmMakeVisitCatalog(self, butler, dataRefs):
        """
        Make a visit catalog with all the key data from each visit

        Parameters
        ----------
        butler: lsst.daf.persistence.Butler
        dataRefs: list of lsst.daf.persistence.ButlerDataRef
           Data references for the input visits
           If this is an empty list, all visits with src catalogs in
           the repository are used.
           Only one individual dataRef from a visit need be specified
           and the code will find the other source catalogs from
           each visit

        Returns
        -------
        visitCat: afw.table.BaseCatalog
        """

        startTime = time.time()

        camera = butler.get('camera')
        nCcd = len(camera)

        if len(dataRefs) == 0:
            # We did not specify any datarefs, so find all of them
            if not self.config.checkAllCcds:
                # Faster mode, scan through referenceCCD
                allVisits = butler.queryMetadata('src',
                                                 format=[self.config.visitDataRefName, 'filter'],
                                                 dataId={self.config.ccdDataRefName:
                                                             self.config.referenceCCD})
                srcVisits = []
                srcCcds = []
                for dataset in allVisits:
                    if (butler.datasetExists('src', dataId={self.config.visitDataRefName: dataset[0],
                                                            self.config.ccdDataRefName:
                                                                self.config.referenceCCD})):
                        srcVisits.append(dataset[0])
                        srcCcds.append(self.config.referenceCCD)
            else:
                # Slower mode, check all CCDs
                allVisits = butler.queryMetadata('src',
                                                 format=[self.config.visitDataRefName, 'filter'])
                srcVisits = []
                srcCcds = []

                for dataset in allVisits:
                    if dataset[0] in srcVisits:
                        continue
                    for ccd in xrange(nCcd):
                        if (butler.datasetExists('src', dataId={self.config.visitDataRefName: dataset[0],
                                                                self.config.ccdDataRefName:
                                                                    ccd})):
                            srcVisits.append(dataset[0])
                            srcCcds.append(ccd)
                            # Once we find that a butler dataset exists, break out
                            break
        else:
            # get the visits from the datarefs, only for referenceCCD
            srcVisits = [d.dataId[self.config.visitDataRefName] for d in dataRefs if
                         d.dataId[self.config.ccdDataRefName] == self.config.referenceCCD]
            srcCcds = [self.config.referenceCCD] * len(srcVisits)

        # Sort the visits for searching/indexing
        srcVisits.sort()

        self.log.info("Found %d visits in %.2f s" %
                      (len(srcVisits), time.time()-startTime))

        schema = afwTable.Schema()
        schema.addField('visit', type=np.int32, doc="Visit number")
        schema.addField('filtername', type=str, size=2, doc="Filter name")
        schema.addField('telra', type=np.float64, doc="Pointing RA (deg)")
        schema.addField('teldec', type=np.float64, doc="Pointing Dec (deg)")
        schema.addField('telha', type=np.float64, doc="Pointing Hour Angle (deg)")
        schema.addField('mjd', type=np.float64, doc="MJD of visit")
        schema.addField('exptime', type=np.float32, doc="Exposure time")
        schema.addField('pmb', type=np.float32, doc="Pressure (millibar)")
        schema.addField('fwhm', type=np.float32, doc="Seeing FWHM?")
        schema.addField('deepflag', type=np.int32, doc="Deep observation")

        visitCat = afwTable.BaseCatalog(schema)
        visitCat.table.preallocate(len(srcVisits))

        startTime = time.time()
        # reading in a small bbox is marginally faster in the scan
        # bbox = afwGeom.BoxI(afwGeom.PointI(0, 0), afwGeom.PointI(1, 1))

        # now loop over visits and get the information
        for i, srcVisit in enumerate(srcVisits):
            # Note that I found the raw access to be more reliable and faster
            #   than calexp_sub to get visitInfo().  This may not be the same
            #   for all repos and processing.
            # At least at the moment, getting raw is faster than any other option
            #  because it is uncompressed on disk.  This will probably change in
            #  the future.
            # Try raw first, fall back to calexp if not available.

            try:
                exp = butler.get('raw', dataId={self.config.visitDataRefName: srcVisit,
                                                self.config.ccdDataRefName: srcCcds[i]})
            except butlerExceptions.NoResults:
                exp = butler.get('calexp', dataId={self.config.visitDataRefName: srcVisit,
                                                   self.config.ccdDataRefName: srcCcds[i]})

            visitInfo = exp.getInfo().getVisitInfo()

            rec = visitCat.addNew()
            rec['visit'] = srcVisit
            rec['filtername'] = exp.getInfo().getFilter().getName()
            radec = visitInfo.getBoresightRaDec()
            rec['telra'] = radec.getRa().asDegrees()
            rec['teldec'] = radec.getDec().asDegrees()
            rec['telha'] = visitInfo.getBoresightHourAngle().asDegrees()
            rec['mjd'] = visitInfo.getDate().get(system=DateTime.MJD)
            rec['exptime'] = visitInfo.getExposureTime()
            # convert from Pa to millibar
            # Note that I don't know if this unit will need to be per-camera config
            rec['pmb'] = visitInfo.getWeather().getAirPressure() / 100
            rec['fwhm'] = 0.0
            rec['deepflag'] = 0

        self.log.info("Found all VisitInfo in %.2f s" % (time.time() - startTime))

        # and now persist it
        butler.put(visitCat, 'fgcmVisitCatalog')

        return visitCat

    def _fgcmMakeAllStarObservations(self, butler, visitCat):
        """
        Compile all good star observations from visits in visitCat

        Parameters
        ----------
        butler: lsst.daf.persistence.Butler
        visitCat: afw.table.BaseCatalog
           Catalog with visit data for FGCM

        Returns
        -------
        None
        """

        startTime = time.time()

        # create our source schema
        sourceSchema = butler.get('src_schema', immediate=True).schema

        # create a mapper to the preferred output
        sourceMapper = afwTable.SchemaMapper(sourceSchema)

        # map to ra/dec
        sourceMapper.addMapping(sourceSchema.find('coord_ra').key, 'ra')
        sourceMapper.addMapping(sourceSchema.find('coord_dec').key, 'dec')

        # and add the fields we want
        sourceMapper.editOutputSchema().addField(
            "visit", type=np.int32, doc="Visit number")
        sourceMapper.editOutputSchema().addField(
            "ccd", type=np.int32, doc="CCD number")
        sourceMapper.editOutputSchema().addField(
            "mag", type=np.float32, doc="Raw magnitude")
        sourceMapper.editOutputSchema().addField(
            "magerr", type=np.float32, doc="Raw magnitude error")

        # we need to know the ccds...
        camera = butler.get('camera')

        started = False

        # loop over visits
        for visit in visitCat:
            self.log.info("Reading sources from visit %d" % (visit['visit']))

            expTime = visit['exptime']

            nStarInVisit = 0

            # loop over CCDs
            for detector in camera:
                ccdId = detector.getId()

                try:
                    # Need to cast visit['visit'] to python int because butler
                    # can't use numpy ints
                    sources = butler.get('src', dataId={self.config.visitDataRefName:
                                                            int(visit['visit']),
                                                        self.config.ccdDataRefName: ccdId},
                                         flags=afwTable.SOURCE_IO_NO_FOOTPRINTS)
                except butlerExceptions.NoResults:
                    # this is not a problem if this ccd isn't there
                    continue

                if not started:
                    # get the keys for quicker look-up

                    # Calibration is based on ApFlux.  Maybe this should be configurable
                    # in the future.
                    fluxKey = sources.getApFluxKey()
                    fluxErrKey = sources.getApFluxErrKey()

                    if self.config.applyJacobian:
                        jacobianKey = sources.schema[self.config.jacobianName].asKey()
                    else:
                        jacobianKey = None

                    outputSchema = sourceMapper.getOutputSchema()
                    visitKey = outputSchema['visit'].asKey()
                    ccdKey = outputSchema['ccd'].asKey()
                    magKey = outputSchema['mag'].asKey()
                    magErrKey = outputSchema['magerr'].asKey()

                    # and the final part of the sourceMapper
                    sourceMapper.addMapping(sources.schema['slot_Centroid_x'].asKey(), 'x')
                    sourceMapper.addMapping(sources.schema['slot_Centroid_y'].asKey(), 'y')

                    # Create a stub of the full catalog
                    fullCatalog = afwTable.BaseCatalog(sourceMapper.getOutputSchema())

                    started = True

                goodSrc = self.sourceSelector.selectSources(sources)

                tempCat = afwTable.BaseCatalog(fullCatalog.schema)
                tempCat.table.preallocate(len(goodSrc.sourceCat))
                tempCat.extend(goodSrc.sourceCat, mapper=sourceMapper)
                tempCat[visitKey][:] = visit['visit']
                tempCat[ccdKey][:] = ccdId
                # Compute "magnitude" by scaling flux with exposure time.
                # Add an arbitrary zeropoint that needs to be investigated.
                tempCat[magKey][:] = (self.config.zeropointDefault -
                                      2.5 * np.log10(goodSrc.sourceCat[fluxKey]) +
                                      2.5 * np.log10(expTime))
                tempCat[magErrKey][:] = (2.5 / np.log(10.)) * (goodSrc.sourceCat[fluxErrKey] /
                                                               goodSrc.sourceCat[fluxKey])

                if self.config.applyJacobian:
                    tempCat[magKey][:] -= 2.5 * np.log10(goodSrc.sourceCat[jacobianKey])

                fullCatalog.extend(tempCat)

                nStarInVisit += len(tempCat)

            self.log.info("  Found %d good stars in visit %d" %
                          (nStarInVisit, visit['visit']))

        self.log.info("Found all good star observations in %.2f s" %
                      (time.time() - startTime))

        butler.put(fullCatalog, 'fgcmStarObservations')

        self.log.info("Done with all stars in %.2f s" %
                      (time.time() - startTime))
        return None

    def _fgcmMatchStars(self, butler, visitCat):
        """
        Use FGCM code to match observations into unique stars.

        Parameters
        ----------
        butler: lsst.daf.persistence.Butler
        visitCat: afw.table.BaseCatalog
           Catalog with visit data for FGCM

        Returns
        -------
        None
        """

        obsCat = butler.get('fgcmStarObservations')

        # get filter names into a numpy array...
        visitFilterNames = np.zeros(len(visitCat), dtype='a2')
        for i in xrange(len(visitCat)):
            visitFilterNames[i] = visitCat[i]['filtername']

        # match to put filterNames with observations
        visitIndex = np.searchsorted(visitCat['visit'],
                                     obsCat['visit'])

        obsFilterNames = visitFilterNames[visitIndex]

        # make the fgcm starConfig dict

        starConfig = {'logger': self.log,
                      'filterToBand': self.config.filterToBand,
                      'requiredBands': self.config.requiredBands,
                      'minPerBand': self.config.minPerBand,
                      'matchRadius': self.config.matchRadius,
                      'isolationRadius': self.config.isolationRadius,
                      'matchNSide': self.config.matchNside,
                      'coarseNSide': self.config.coarseNside,
                      'densNSide': self.config.densityCutNside,
                      'densMaxPerPixel': self.config.densityCutMaxPerPixel,
                      'referenceBand': self.config.referenceBand,
                      'zpDefault': self.config.zeropointDefault}

        # initialize the FgcmMakeStars object
        fgcmMakeStars = fgcm.FgcmMakeStars(starConfig)

        # make the reference stars
        #  note that the ra/dec native Angle format is radians
        fgcmMakeStars.makeReferenceStars(np.rad2deg(obsCat['ra']),
                                         np.rad2deg(obsCat['dec']),
                                         filterNameArray=obsFilterNames,
                                         bandSelected=False)

        # and match all the stars
        fgcmMakeStars.makeMatchedStars(np.rad2deg(obsCat['ra']),
                                       np.rad2deg(obsCat['dec']),
                                       obsFilterNames)

        # now persist

        # afwTable for objects
        objSchema = afwTable.Schema()
        objSchema.addField('fgcm_id', type=np.int32, doc='FGCM Unique ID')
        # FIXME: should be angle?
        objSchema.addField('ra', type=np.float64, doc='Mean object RA')
        objSchema.addField('dec', type=np.float64, doc='Mean object Dec')
        objSchema.addField('obsarrindex', type=np.int32,
                           doc='Index in obsIndexTable for first observation')
        objSchema.addField('nobs', type=np.int32, doc='Total number of observations')

        # make catalog and records
        fgcmStarIdCat = afwTable.BaseCatalog(objSchema)
        fgcmStarIdCat.table.preallocate(fgcmMakeStars.objIndexCat.size)
        for i in xrange(fgcmMakeStars.objIndexCat.size):
            fgcmStarIdCat.addNew()

        # fill the catalog
        fgcmStarIdCat['fgcm_id'][:] = fgcmMakeStars.objIndexCat['FGCM_ID']
        fgcmStarIdCat['ra'][:] = fgcmMakeStars.objIndexCat['RA']
        fgcmStarIdCat['dec'][:] = fgcmMakeStars.objIndexCat['DEC']
        fgcmStarIdCat['obsarrindex'][:] = fgcmMakeStars.objIndexCat['OBSARRINDEX']
        fgcmStarIdCat['nobs'][:] = fgcmMakeStars.objIndexCat['NOBS']

        butler.put(fgcmStarIdCat, 'fgcmStarIds')

        # afwTable for observation indices
        obsSchema = afwTable.Schema()
        obsSchema.addField('obsindex', type=np.int32, doc='Index in observation table')

        fgcmStarIndicesCat = afwTable.BaseCatalog(obsSchema)
        fgcmStarIndicesCat.table.preallocate(fgcmMakeStars.obsIndexCat.size)
        for i in xrange(fgcmMakeStars.obsIndexCat.size):
            fgcmStarIndicesCat.addNew()

        fgcmStarIndicesCat['obsindex'][:] = fgcmMakeStars.obsIndexCat['OBSINDEX']

        butler.put(fgcmStarIndicesCat, 'fgcmStarIndices')

        # and we're done with the stars
        return None
