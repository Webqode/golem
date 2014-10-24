import logging
import random
import os
import subprocess
import pickle


from GNRTask import GNRTaskBuilder, GNRTask
from GNREnv import GNREnv
from golem.task.TaskBase import ComputeTaskDef
from golem.core.Compress import decompress
from testtasks.pbrt.takscollector import PbrtTaksCollector, exr_to_pil
from examples.gnr.RenderingEnvironment import ThreeDSMaxEnvironment

import OpenEXR, Imath
from PIL import Image, ImageChops
from collections import OrderedDict


logger = logging.getLogger(__name__)

class MentalRayRendererOptions:
    def __init__( self ):
        try:
            dsmaxpath = os.environ.get('ADSK_3DSMAX_x64_2015')
            presetFile = os.path.join( dsmaxpath,  'renderpresets\mental.ray.daylighting.high.rps')
            self.preset = presetFile
        except:
            self.preset = ""

    def addToResources( self, resources ):
        if os.path.isfile( self.preset ):
            resources.append( os.path.normpath( self.preset ) )
        return resources

class MentalRayTaskBuilder( GNRTaskBuilder ):

    def build( self ):
        mainSceneDir = os.path.dirname( self.taskDefinition.mainSceneFile )

        mentalRayTask = MentalRayTask(self.clientId,
                                   self.taskDefinition,
                                   mainSceneDir,
                                   6,
                                   32,
                                   4,
                                   "temp",
                                   self.rootPath
                                   )
        return mentalRayTask

class MentalRayTask( GNRTask ):

    def __init__( self, clientId, taskDefinition, mainSceneDir, totalTasks, numSubtasks, numCores,
                  outfilebasename, rootPath, returnAddress = "", returnPort = 0 ):

        self.taskDefinition = taskDefinition

        srcFile = open( self.taskDefinition.mainProgramFile, "r")
        srcCode = srcFile.read()


        resourceSize = 0
        for resource in self.taskDefinition.resources:
            resourceSize += os.stat(resource).st_size

        GNRTask.__init__( self,
                          srcCode,
                          clientId,
                          self.taskDefinition.id,
                          returnAddress,
                          returnPort,
                          ThreeDSMaxEnvironment.getId(),
                          self.taskDefinition.fullTaskTimeout,
                          self.taskDefinition.subtaskTimeout,
                          resourceSize )

        self.taskResources = self.taskDefinition.resources
     #   self.taskResources.append( os.path.normpath( self.taskDefinition.rendererOptions.preset ) )
        self.estimatedMemory = self.taskDefinition.estimatedMemory
        self.outputFormat = self.taskDefinition.outputFormat
        self.outputFile = self.taskDefinition.outputFile
        self.mainSceneDir = mainSceneDir
        self.mainProgramFile = self.taskDefinition.mainProgramFile
        self.outfilebasename = outfilebasename

        self.rootPath = rootPath
        self.numCores = numCores
        self.totalTasks = totalTasks
        self.lastTask = 0
        self.numFailedSubtasks = 0
        self.failedSubtasks     = set()
        self.numSubtasks = numSubtasks
        self.sceneFileSrc = ""
        self.previewFilePath    = None

        self.collector          = PbrtTaksCollector()
        self.collectedFileNames = {}
        self.subTasksGiven      = {}
        self.numTasksReceived = 0

        self.tmpCnt = 0;


    #######################
    def queryExtraData( self, perfIndex, numCores = 0 ):

        if ( self.lastTask != self.totalTasks ):
            self.lastTask += 1
            startTask = self.lastTask
            endTask = self.lastTask
        else:
            subtask = self.failedSubtasks.pop()
            self.numFailedSubtasks -= 1
            endTask = subtask.endChunk
            startTask = subtask.startChunk

        if numCores == 0:
            numCores = self.numCores

        commonPathPrefix = os.path.commonprefix( self.taskResources )
        commonPathPrefix = os.path.dirname( commonPathPrefix )

        sceneFile = os.path.basename( self.taskDefinition.mainSceneFile )
        presetFile = os.path.basename( self.taskDefinition.rendererOptions.preset)

        extraData =          {      "pathRoot" : self.mainSceneDir,
                                    "startTask" : startTask,
                                    "endTask" : endTask,
                                    "totalTasks" : self.totalTasks,
                                    "numSubtasks" : self.numSubtasks,
                                    "numCores" : numCores,
                                    "outfilebasename" : self.outfilebasename,
                                    "sceneFile" : sceneFile,
                                    "sceneFileSrc" : self.sceneFileSrc,
                                    "width" : self.taskDefinition.resolution[0],
                                    "height": self.taskDefinition.resolution[1],
                                    "presetFile": presetFile
                                }



        hash = "{}".format( random.getrandbits(128) )
        self.subTasksGiven[ hash ] = extraData

        ctd = ComputeTaskDef()
        ctd.taskId              = self.header.taskId
        ctd.subtaskId           = hash
        ctd.extraData           = extraData
        ctd.returnAddress       = self.header.taskOwnerAddress
        ctd.returnPort          = self.header.taskOwnerPort
        ctd.shortDescription    = self.__shortExtraDataRepr( perfIndex, extraData )
        ctd.srcCode             = self.srcCode
        ctd.performance         = perfIndex

        ctd.workingDirectory    = os.path.relpath( self.mainProgramFile, commonPathPrefix )
        ctd.workingDirectory    = os.path.dirname( ctd.workingDirectory )

        logger.debug(ctd.workingDirectory)

        # ctd.workingDirectory = ""

        return ctd

    #######################
    def queryExtraDataForTestTask( self ):
        extraData =          {      "pathRoot" : self.mainSceneDir,
                                    "startTask" : 0,
                                    "endTask" : 1,
                                    "totalTasks" : self.totalTasks,
                                    "numSubtasks" : self.numSubtasks,
                                    "numCores" : self.numCores,
                                    "outfilebasename" : self.outfilebasename,
                                    "sceneFile" : self.taskDefinition.mainSceneFile,
                                    "sceneFileSrc": self.sceneFileSrc,
                                    "width" : self.taskDefinition.resolution[0],
                                    "height": self.taskDefinition.resolution[1],
                                    "presetFile": self.taskDefinition.rendererOptions.preset
                                }

        hash = "{}".format( random.getrandbits(128) )

        ctd = ComputeTaskDef()
        ctd.taskId              = self.header.taskId
        ctd.subtaskId           = hash
        ctd.extraData           = extraData
        ctd.returnAddress       = self.header.taskOwnerAddress
        ctd.returnPort          = self.header.taskOwnerPort
        ctd.shortDescription    = self.__shortExtraDataRepr( 0, extraData )
        ctd.srcCode             = self.srcCode
        ctd.performance         = 0

        self.testTaskResPath = GNREnv.getTestTaskPath( self.rootPath )
        logger.debug( self.testTaskResPath )
        if not os.path.exists( self.testTaskResPath ):
            os.makedirs( self.testTaskResPath )

        ctd.workingDirectory    = os.path.relpath( self.mainProgramFile, self.testTaskResPath)
        ctd.workingDirectory    = os.path.dirname( ctd.workingDirectory )

        return ctd

     #######################
    def __shortExtraDataRepr( self, perfIndex, extraData ):
        l = extraData
        return "pathRoot: {}, startTask: {}, endTask: {}, totalTasks: {}, numSubtasks: {}, numCores: {}, outfilebasename: {}, sceneFile: {}".format( l["pathRoot"], l["startTask"], l["endTask"], l["totalTasks"], l["numSubtasks"], l["numCores"], l["outfilebasename"], l["sceneFile"] )

  #######################
    def computationFinished( self, subtaskId, taskResult, env = None ):

        tmpDir = env.getTaskTemporaryDir( self.header.taskId )

        if len( taskResult ) > 0:
            for trp in taskResult:
                tr = pickle.loads( trp )
                fh = open( os.path.join( tmpDir, tr[ 0 ] ), "wb" )
                fh.write( decompress( tr[ 1 ] ) )
                fh.close()
                num = self.subTasksGiven[ subtaskId ][ 'startTask' ]
                self.collectedFileNames[ num ] = os.path.join(tmpDir, tr[0] )
                self.numTasksReceived += 1

                self.__updatePreview( os.path.join( tmpDir, tr[ 0 ] ), num )

        if self.numTasksReceived == self.totalTasks:
            outputFileName = u"{}".format( self.outputFile, self.outputFormat )

            pth, filename =  os.path.split(os.path.realpath(__file__))
            taskCollectorPath = os.path.join(pth, "..\..\..\\tools\\taskcollector\Release\\taskcollector.exe")
            logger.debug( "taskCollector path: {}".format( taskCollectorPath ) )

            self.collectedFileNames = OrderedDict( sorted( self.collectedFileNames.items() ) )
            files = " ".join( self.collectedFileNames.values() )
            cmd = u"{} mr {} {}".format(taskCollectorPath, outputFileName, files )
            logger.debug("cmd = {}".format( cmd ) )
            pc = subprocess.Popen( cmd )
            pc.wait()


   #######################
    def __updatePreview( self, newChunkFilePath, chunkNum ):

        if newChunkFilePath.endswith(".exr"):
            img = exr_to_pil( newChunkFilePath )
        else:
            img = Image.open( newChunkFilePath )

        imgOffset = Image.new("RGB", (self.taskDefinition.resolution[0], self.taskDefinition.resolution[1]))
        try:
            imgOffset.paste(img, (0, (chunkNum - 1) * (self.taskDefinition.resolution[1]) / self.totalTasks ) )
        except Exception, err:
            logger.error("Can't generate preview {}".format( str(err) ))


        tmpDir = GNREnv.getTmpPath( self.header.clientId, self.header.taskId, self.rootPath )

        self.previewFilePath = "{}".format( os.path.join( tmpDir, "current_preview") )

        if os.path.exists( self.previewFilePath ):
            imgCurrent = Image.open( self.previewFilePath )
            imgCurrent = ImageChops.add( imgCurrent, imgOffset )
            imgCurrent.save( self.previewFilePath, "BMP" )
        else:
            imgOffset.save( self.previewFilePath, "BMP" )

