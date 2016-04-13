# Iterates over a directory that contains json files, and creates the sift features of each file.
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


def create_layer_sift_features(tiles_fname, out_fname, jar_file, meshes_dir=None, conf=None, threads_num=4):

    meshes_str = ''
    if meshes_dir is not None:
        meshes_str = '--meshesDir "{0}"'.format(meshes_dir)

    tiles_url = utils.path2url(os.path.abspath(tiles_fname))
    conf_args = utils.conf_args_from_file(conf, 'ComputeSiftFeatures')
    # Compute the Sift features `for each tile in the tile spec file
    java_cmd = 'java -Xmx11g -XX:ParallelGCThreads=1 -Djava.awt.headless=true -cp "{0}" org.janelia.alignment.ComputeLayerSiftFeatures --url {1} --targetPath {2} --threads {3} {4} {5}'.format(\
        jar_file, tiles_url, out_fname, threads_num, meshes_str, conf_args)
    utils.execute_shell_command(java_cmd)




def main():
    # Command line parser
    parser = argparse.ArgumentParser(description='Iterates over a directory that contains json files, \
        and creates the sift features of each file. \
        The output is either in the same directory or in a different, user-provided, directory \
        (in either case, we use a different file name).')
    parser.add_argument('tiles_fname', metavar='tiles_json', type=str, 
                        help='a tile_spec file that contains the images to create sift features for, in json format')
    parser.add_argument('-o', '--output_file', type=str, 
                        help='an output feature_spec file, that will include the sift features for all tiles (default: ./siftFeatures.json)',
                        default='./siftFeatures.json')
    parser.add_argument('-j', '--jar_file', type=str, 
                        help='the jar file that includes the render (default: ../target/render-0.0.1-SNAPSHOT.jar)',
                        default='../target/render-0.0.1-SNAPSHOT.jar')
    parser.add_argument('-c', '--conf_file_name', type=str, 
                        help='the configuration file with the parameters for each step of the alignment process in json format (uses default parameters, if not supplied)',
                        default=None)
    parser.add_argument('-t', '--threads_num', type=int, 
                        help='the number of threads to use (default: 1)',
                        default=1)
    parser.add_argument('-md', '--meshes_dir', type=str, 
                        help='the directory that contains precomputed and serialized meshes of the layer (optional, default: None)',
                        default=None)


    args = parser.parse_args()

    #print args

    create_layer_sift_features(args.tiles_fname, args.output_file, args.jar_file,
        meshes_dir=args.meshes_dir,
        conf=args.conf_file_name, threads_num=args.threads_num)

if __name__ == '__main__':
    main()

