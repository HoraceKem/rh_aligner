import sys
import json
import subprocess
import multiprocessing
from subprocess import PIPE


def prelimmatchingworker((cmd, cmdid)):
    p = subprocess.Popen([cmd], stdin=PIPE, stdout=PIPE, stderr=PIPE, shell=True)
    p.wait()
    output, error = p.communicate()
    print "Job Done: " + str(cmdid)
    print error


def main():
    script, conffile = sys.argv
    with open(conffile) as conf_file:
        conf = json.load(conf_file)
    slicemin = conf["driver_args"]["slicemin"]
    slicemax = conf["driver_args"]["slicemax"]
    numforwardback = conf["driver_args"]["numforwardback"]
    datadir = conf["driver_args"]["datadir"]
    workdir = conf["driver_args"]["workdir"]
    imgdir = conf["driver_args"]["imgdir"]
    outdir = conf["driver_args"]["outdir"]
    conffile = conf["driver_args"]["conffile"]
    #'''
    # Preliminary MFOV Matching
    commands = []
    cmdid = 0
    for slice1 in range(slicemin, slicemax + 1):
        for slice2 in range(slicemin, slicemax + 1):
            if abs(slice1 - slice2) <= numforwardback and slice1 != slice2:
                cmdid += 1
                commands.append(("python /n/home10/adisuis/Fiji/FijiBento_multibeam/scripts/slice_to_slice_comparison.py " + str(slice1) + " " + str(slice2) + " " + datadir + " " + workdir + " " + conffile, cmdid))

    print "Expected Number of Jobs in Prelim Matching: " + str(cmdid)
    print commands
    pool = multiprocessing.Pool(conf["driver_args"]["numconcurrent"])
    res = pool.map(prelimmatchingworker, commands)
    print "Preliminary MFOV Matching Done"
    #'''
    # Image template Matching
    commands = []
    cmdid = 0
    for slice1 in range(slicemin, slicemax + 1):
        for slice2 in range(slicemin, slicemax + 1):
            if abs(slice1 - slice2) <= numforwardback and slice1 != slice2:
                cmdid += 1
                commands.append(("python /n/home10/adisuis/Fiji/FijiBento_multibeam/scripts/match_images_between_slices.py " + str(slice1) + " " + str(slice2) + " " + conffile, cmdid))

    print "Expected Number of Jobs in Template Matching: " + str(cmdid)
    print commands
    pool = multiprocessing.Pool(conf["driver_args"]["numconcurrent"])
    res = pool.map(prelimmatchingworker, commands)
    print "Image Template Matching Done"

if __name__ == '__main__':
    main()
