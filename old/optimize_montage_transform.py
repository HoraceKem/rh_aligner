# Iterates over a directory that contains correspondence list json files, and optimizes the montage by perfroming the transform on each file.
# The output is either in the same directory or in a different, user-provided, directory
# (in either case, we use a different file name)
#
# requires:
# - java (executed from the command line)
# - 

import sys
import os
import glob
import argparse
from subprocess import call
import utils


def optimize_montage_transform(correspondence_files_list, tilespec_file, fixed_tiles, output_file, jar_file, conf_fname=None, threads_num=None):

    corr_url = utils.path2url(correspondence_files_list)
    tiles_url = utils.path2url(tilespec_file)
    conf_args = utils.conf_args_from_file(conf_fname, 'OptimizeMontageTransform')

    fixed_str = ""
    if fixed_tiles != None:
        fixed_str = "--fixedTiles {0}".format(" ".join(map(str, fixed_tiles)))

    threads_str = ""
    if threads_num != None:
        threads_str = "--threads {0}".format(threads_num)

    java_cmd = 'java -Xmx5g -XX:ParallelGCThreads=1 -Djava.awt.headless=true -cp "{0}" org.janelia.alignment.OptimizeMontageTransform {1} --corrfileslst {2} --tilespecfile {3} {4} {5} --targetPath {6}'.format(\
        jar_file, conf_args, corr_url, tiles_url, fixed_str, threads_str, output_file)
    utils.execute_shell_command(java_cmd)




def main():
    # Command line parser
    parser = argparse.ArgumentParser(description='Takes a correspondence list json file, \
        and optimizes the montage by perfroming the transform on each tile in the file.')
    parser.add_argument('correspondence_files_list', metavar='correspondence_files_list', type=str, 
                        help='a correspondence_spec file list in a single txt file')
    parser.add_argument('tilespec_file', metavar='tilespec_file', type=str, 
                        help='a tilespec file containing all the tiles')
    parser.add_argument('-o', '--output_file', type=str, 
                        help='the output file',
                        default='./opt_montage_transform.json')
    parser.add_argument('-f', '--fixed_tiles', type=str, nargs='+',
                        help='a space separated list of fixed tile indices (default: 0)',
                        default="0")
    parser.add_argument('-j', '--jar_file', type=str, 
                        help='the jar file that includes the render (default: ../target/render-0.0.1-SNAPSHOT.jar)',
                        default='../target/render-0.0.1-SNAPSHOT.jar')
    parser.add_argument('-c', '--conf_file_name', type=str, 
                        help='the configuration file with the parameters for each step of the alignment process in json format (uses default parameters, if not supplied)',
                        default=None)
    parser.add_argument('-t', '--threads_num', type=int,
                        help='the number of threads to use (default: the number of cores in the system)',
                        default=None)


    args = parser.parse_args()

    #print args

    try:
        optimize_montage_transform(args.correspondence_files_list, args.tilespec_file, args.fixed_tiles, args.output_file, args.jar_file,
            conf_fname=args.conf_file_name, threads_num=args.threads_num)
    except:
        sys.exit("Error while executing: {0}".format(sys.argv))

if __name__ == '__main__':
    main()

