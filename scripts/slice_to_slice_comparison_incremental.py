# Setup
from __future__ import print_function
import models
import ransac
import os
import numpy as np
import h5py
import json
import random
import sys
from scipy.spatial import distance
import cv2
import time
import glob
import argparse
import utils
from scipy.spatial import Delaunay
from bounding_box import BoundingBox

TILES_PER_MFOV = 61


def compute_features(tile_ts):
    # Load the image
    img_file = tile_ts["mipmapLevels"]["0"]["imageUrl"].replace('file://', '')
    img = cv2.imread(img_file, 0)
    equ = cv2.equalizeHist(img)

    # find the keypoints and descriptors
    surf2 = cv2.SURF(1700, nOctaves=8, nOctaveLayers=2, extended=True, upright=False)
    pts, des = surf2.detectAndCompute(equ, None)

    resps = np.array([p.response for p in pts], dtype=np.float32)
    octas = np.array([p.octave for p in pts], dtype=np.float32)
    allps = np.array([p.pt for p in pts], dtype=np.float32)
    return resps, des, octas, allps
    

def load_features(feature_file, tile_ts):
    # Should have the same name as the following: [tilespec base filename]_[img filename].json/.hdf5
    assert(os.path.basename(os.path.splitext(tile_ts["mipmapLevels"]["0"]["imageUrl"])[0]) in feature_file)

    # print("Loading feature file {} of tile {}, with a transform {}".format(feature_file, tile_ts["mipmapLevels"]["0"]["imageUrl"], tile_ts["transforms"][0]))
    # load the image features
    with h5py.File(feature_file, 'r') as f:
        resps = f['pts']['responses'][:]
        descs = f['descs'][:]
        octas = f['pts']['octaves'][:]
        allps = np.array(f['pts']['locations'])

    #resps, descs, octas, allps = compute_features(tile_ts)

    # If no relevant features are found, return an empty set
    if (len(allps) == 0):
        return (np.array([]).reshape((0, 2)), [], [])


    currentocta = (octas.astype(int) & 0xff)
    currentocta[currentocta > 127] -= 255
    mask = (currentocta == 4) | (currentocta == 5)
    points = allps[mask, :]
    resps = resps[mask]
    descs = descs[mask]

    # Apply the transformation to each point
    newmodel = models.Transforms.from_tilespec(tile_ts["transforms"][0])
    points = newmodel.apply_special(points)

    return (points, resps, descs)


def getcenter(mfov_ts):
    xlocsum, ylocsum, nump = 0, 0, 0
    for tile_ts in mfov_ts.values():
        xlocsum += tile_ts["bbox"][0] + tile_ts["bbox"][1]
        ylocsum += tile_ts["bbox"][2] + tile_ts["bbox"][3]
        nump += 2
    return [xlocsum / nump, ylocsum / nump]



def analyzemfov(mfov_ts, features_dir):
    """Returns all the relevant features of the tiles in a single mfov"""
    allpoints = np.array([]).reshape((0, 2))
    allresps = []
    alldescs = []

    mfov_num = int(mfov_ts.values()[0]["mfov"])
    mfov_string = ("%06d" % mfov_num)
    mfov_feature_files = sorted(glob.glob(os.path.join(os.path.join(features_dir, mfov_string), '*')))
    if len(mfov_feature_files) < TILES_PER_MFOV:
        print("Warning: number of feature files in directory: {} is smaller than {}".format(os.path.join(os.path.join(features_dir, mfov_string)), TILES_PER_MFOV), file=sys.stderr)

    # load each features file, and concatenate all to single lists
    for feature_file in mfov_feature_files:
        # Get the correct tile tilespec from the section tilespec (convert to int to remove leading zeros)
        tile_num = int(feature_file.split('sifts_')[1].split('_')[2])
        (tempoints, tempresps, tempdescs) = load_features(feature_file, mfov_ts[tile_num])
        if type(tempdescs) is not list:
            # concatentate the results
            allpoints = np.append(allpoints, tempoints, axis=0)
            allresps.append(tempresps)
            alldescs.append(tempdescs)
    allpoints = np.array(allpoints)
    return (allpoints, np.concatenate(allresps), np.vstack(alldescs))


def generatematches_crosscheck_cv2(allpoints1, allpoints2, alldescs1, alldescs2, actual_params):
    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=True)
    matches = matcher.knnMatch(np.array(alldescs1), np.array(alldescs2), k=1)
    goodmatches = [m for m in matches if len(m) > 0]
    match_points = np.array([
        np.array([allpoints1[[m[0].queryIdx for m in goodmatches]]][0]),
        np.array([allpoints2[[m[0].trainIdx for m in goodmatches]]][0])])
    return match_points



def compare_features(section1_pts_resps_descs, section2_pts_resps_descs, actual_params):
    [allpoints1, allresps1, alldescs1] = section1_pts_resps_descs
    [allpoints2, allresps2, alldescs2] = section2_pts_resps_descs
    # print("lengths: len(allpoints1): {}, alldescs1.shape: {}".format(len(allpoints1), alldescs1.shape))
    # print("lengths: len(allpoints2): {}, alldescs2.shape: {}".format(len(allpoints2), alldescs2.shape))
    match_points = generatematches_crosscheck_cv2(allpoints1, allpoints2, alldescs1, alldescs2, actual_params)

    if match_points.shape[0] == 0 or match_points.shape[1] == 0:
        return (None, 0, 0, 0, len(allpoints1), len(allpoints2))
    
    model_index = actual_params["model_index"]
    iterations = actual_params["iterations"]
    max_epsilon = actual_params["max_epsilon"]
    min_inlier_ratio = actual_params["min_inlier_ratio"]
    min_num_inlier = actual_params["min_num_inlier"]
    max_trust = actual_params["max_trust"]
    model, filtered_matches = ransac.filter_matches(match_points, model_index, iterations, max_epsilon, min_inlier_ratio, min_num_inlier, max_trust)
    if filtered_matches is None:
        filtered_matches = np.zeros((0, 0))
    return (model, filtered_matches.shape[1], float(filtered_matches.shape[1]) / match_points.shape[1], match_points.shape[1], len(allpoints1), len(allpoints2))


def load_mfovs_features(indexed_ts, features_dir, mfovs_idx):
    all_points, all_resps, all_descs = np.array([]).reshape((0, 2)), [], []
    for idx in mfovs_idx:
        mfov_points, mfov_resps, mfov_descs = analyzemfov(indexed_ts[idx], features_dir)
        all_points = np.append(all_points, mfov_points, axis=0)
        all_resps.append(mfov_resps)
        all_descs.append(mfov_descs)
    return all_points, all_resps, all_descs
 


def iterative_search(actual_params, layer1, layer2, indexed_ts1, indexed_ts2, features_dir1, features_dir2, mfovs_nums1, centers_mfovs_nums2, section2_mfov_bboxes, num_mfovs2, assumed_model=None):
    # Load the features from the mfovs in section 1
    all_points1, all_resps1, all_descs1 = load_mfovs_features(indexed_ts1, features_dir1, mfovs_nums1)
    section1_pts_resps_descs = [all_points1, np.concatenate(all_resps1), np.vstack(all_descs1)]
    print("Section {} - mfovs: {}, {} features loaded.".format(layer1, mfovs_nums1, len(all_points1)))

    # Make sure we have enough features from section 1
    if (len(all_points1) < actual_params["min_features_num"]):
        print("Number of features in Section {} mfov(s) {} is {}, and smaller than {}. Skipping feature matching".format(
            layer1, mfovs_nums1, len(all_points1), actual_params["min_features_num"]))
        return None, 0, 0, 0, 0, 0

    # Take the mfovs in the middle of the 2nd section as the initial matched area
    # (on each iteration, increase the matched area, by taking all the mfovs that overlap
    # with the bounding box of the previous matched area)
    current_area = BoundingBox.fromList(section2_mfov_bboxes[centers_mfovs_nums2[0] - 1].toArray())
    print("Adding area 0: {}".format(section2_mfov_bboxes[centers_mfovs_nums2[0] - 1].toArray()))
    for i in range(1, len(centers_mfovs_nums2)):
        center_mfov_num2 = centers_mfovs_nums2[i]
        current_area.extend(BoundingBox.fromList(section2_mfov_bboxes[center_mfov_num2 - 1].toArray()))
        print("Adding area {}: {}".format(i, section2_mfov_bboxes[center_mfov_num2 - 1].toArray()))
    current_mfovs = set(centers_mfovs_nums2)
    current_features_pts, current_features_resps, current_features_descs = np.array([]).reshape((0, 2)), [], []
    for center_mfov_num2 in centers_mfovs_nums2:
        print("loading features for mfov: {}".format(center_mfov_num2))
        mfov_points, mfov_resps, mfov_descs = analyzemfov(indexed_ts2[center_mfov_num2], features_dir2)
        current_features_pts = np.append(current_features_pts, mfov_points, axis=0)
        current_features_resps.append(mfov_resps)
        current_features_descs.append(mfov_descs)
    print("Features loaded")
    current_features = (current_features_pts, np.concatenate(current_features_resps), np.vstack(current_features_descs))

    match_found = False
    match_iteration = 0
    best_transform = None
    model = None
    num_filtered = 0
    filter_rate = 0
    num_rod = 0
    num_m1 = 0
    num_m2 = 0
    # Save the best model that we find through the iterations
    saved_model = {
        'model' : None,
        'num_filtered' : 0
    }
    saved_model_num_filtered = 0
    while not match_found:
        match_iteration += 1
        print("Iteration {}: using {} mfovs from section {} ({} features)".format(match_iteration, len(current_mfovs), layer2, len(current_features_pts)))
        # Try to match the 3-mfovs features of section1 to the current features of section2
        (model, num_filtered, filter_rate, num_rod, num_m1, num_m2) = compare_features(section1_pts_resps_descs, current_features, actual_params)

        if model is None:
            print("Could not find a valid model between Sec{} and Sec{} in iteration {}".format(layer1, layer2, match_iteration))
        else:
            print("Found a model {} (with {} matches) between Sec{} and Sec{} in iteration {}, need to verify cutoff".format(model.to_str(), num_filtered, layer1, layer2, match_iteration))
            if num_filtered > saved_model_num_filtered:
                saved_model['model'] = model
                saved_model['num_filtered'] = num_filtered
                saved_model['filter_rate'] = filter_rate
                saved_model['num_rod'] = num_rod
                saved_model['num_m1'] = num_m1
                saved_model['num_m2'] = num_m2

        if num_filtered > (actual_params["num_filtered_percent"] * len(all_points1) / len(mfovs_nums1)) and filter_rate > actual_params["filter_rate_cutoff"]:
            best_transform = model
            match_found = True
        else:
            # Find the mfovs that are overlapping with the current area
            print("len(mfovs_nums1)", len(mfovs_nums1))
            print("threshold wasn't met: num_filtered: {} > {} and filter_rate: {} > {}".format(num_filtered, (actual_params["num_filtered_percent"] * len(all_points1) / len(mfovs_nums1)), filter_rate, actual_params["filter_rate_cutoff"]))
            overlapping_mfovs = set()
            for i in range(1, num_mfovs2 + 1):
                if current_area.overlap(section2_mfov_bboxes[i - 1]):
                    overlapping_mfovs.add(i)

            new_mfovs = overlapping_mfovs - current_mfovs
            if len(new_mfovs) == 0:
                # No new mfovs were found, giving up
                print("No model found between sections {} and {}, and no more mfovs were found. Giving up!".format(layer1, layer2))
                break

            # Add the new mfovs features
            print("Adding {} mfovs ({}) to the second layer".format(len(new_mfovs), new_mfovs))
            for i in new_mfovs:
                mfov_points, mfov_resps, mfov_descs = analyzemfov(indexed_ts2[i], features_dir2)
                current_features_pts = np.append(current_features_pts, mfov_points, axis=0)
                current_features_resps.append(mfov_resps)
                current_features_descs.append(mfov_descs)

                # Expand the current area
                current_area.extend(section2_mfov_bboxes[i - 1])
            print("Combining features")
            current_features = (current_features_pts, np.concatenate(current_features_resps), np.vstack(current_features_descs))
            current_mfovs = overlapping_mfovs

    if best_transform is None and saved_model['model'] is not None:
        best_transform = saved_model['model']
        num_filtered = saved_model['num_filtered']
        filter_rate = saved_model['filter_rate']
        num_rod = saved_model['num_rod']
        num_m1 = saved_model['num_m1']
        num_m2 = saved_model['num_m2']

    return best_transform, num_filtered, filter_rate, num_rod, num_m1, num_m2




def analyze_slices(tiles_fname1, tiles_fname2, features_dir1, features_dir2, actual_params):
    # Read the tilespecs
    ts1 = utils.load_tilespecs(tiles_fname1)
    ts2 = utils.load_tilespecs(tiles_fname2)
    indexed_ts1 = utils.index_tilespec(ts1)
    indexed_ts2 = utils.index_tilespec(ts2)

    num_mfovs1 = len(indexed_ts1)
    num_mfovs2 = len(indexed_ts2)

    layer1 = indexed_ts1.values()[0].values()[0]["layer"]
    layer2 = indexed_ts2.values()[0].values()[0]["layer"]
    to_ret = []
    model_arr = np.zeros((num_mfovs1, num_mfovs2), dtype=models.RigidModel)
    num_filter_arr = np.zeros((num_mfovs1, num_mfovs2))
    filter_rate_arr = np.zeros((num_mfovs1, num_mfovs2))
    best_transform = None

    # Get all the centers of each section
    #print("Fetching sections centers")
    centers1 = np.array([getcenter(indexed_ts1[i]) for i in range(1, num_mfovs1 + 1)])
    centers2 = np.array([getcenter(indexed_ts2[i]) for i in range(1, num_mfovs2 + 1)])

    # Take the mfov closest to the middle of each section
    section_center1 = np.mean(centers1, axis=0)
    section_center2 = np.mean(centers2, axis=0)
    # Find the 3 closest mfovs to the center of section 1
    closest_mfovs_nums1 = np.argpartition([((c[0] - section_center1[0])**2 + (c[1] - section_center1[1])**2) for c in centers1], 3)[:3]
    closest_mfovs_nums1 = [n + 1 for n in closest_mfovs_nums1]
    # Find the closest mfov to the center of section 2
    centers_mfovs_nums2 = [np.argmin([((c[0] - section_center2[0])**2 + (c[1] - section_center2[1])**2) for c in centers2])]
    centers_mfovs_nums2 = [n + 1 for n in centers_mfovs_nums2]
    
    # Compute per-mfov bounding box for the 2nd section
    section2_mfov_bboxes = [BoundingBox.read_bbox_from_ts(indexed_ts2[i].values()) for i in range(1, num_mfovs2 + 1)]

    print("Comparing Sec{} (mfovs: {}) and Sec{} (starting from mfovs: {})".format(layer1, closest_mfovs_nums1, layer2, centers_mfovs_nums2))
    initial_search_start_time = time.clock()
    # Do an iterative search of the 3 mfovs closest to the center of section 1 to the mfovs of section2 (starting from the center)
    best_transform, num_filtered, filter_rate, _, _, _ = iterative_search(actual_params, layer1, layer2, indexed_ts1, indexed_ts2,
                         features_dir1, features_dir2, closest_mfovs_nums1, centers_mfovs_nums2, section2_mfov_bboxes, num_mfovs2)
    initial_search_end_time = time.clock()


    
    if best_transform is None:
        print("Could not find a preliminary transform between sections: {} and {}, after {} seconds.".format(layer1, layer2, initial_search_end_time - initial_search_start_time))
        return to_ret

    best_transform_matrix = best_transform.get_matrix()
    print("Found a preliminary transform between sections: {} and {} (filtered matches#: {}, rate: {}), with model: {} in {} seconds".format(layer1, layer2, num_filtered, filter_rate, best_transform_matrix, initial_search_end_time - initial_search_start_time))


    # Iterate throught the mfovs of section1, and try to find
    # for each mfov the transformation to section 2
    # (do an iterative search as was done in the previous phase)
    for i in range(0, num_mfovs1):
        # Find the location of all mfovs in section 2 that "overlap" the current mfov from section 1
        # (according to the estimated transform)
        #section1_mfov_bbox = BoundingBox.read_bbox_from_ts(indexed_ts1[i + 1].values())
        #print("section1_mfov_bbox: {}".format(section1_mfov_bbox.toStr()))
        #bbox_points = np.array([[section1_mfov_bbox.from_x, section1_mfov_bbox.from_y, 1.0],
        #                        [section1_mfov_bbox.from_x, section1_mfov_bbox.to_y, 1.0],
        #                        [section1_mfov_bbox.to_x, section1_mfov_bbox.from_y, 1.0],
        #                        [section1_mfov_bbox.to_x, section1_mfov_bbox.to_y, 1.0]])
        #bbox_points_projected = [np.dot(best_transform_matrix, p)[0:2] for p in bbox_points]
        #projected_min_x, projected_min_y = np.min(bbox_points_projected, axis=0)
        #projected_max_x, projected_max_y = np.max(bbox_points_projected, axis=0)
        #projected_mfov_bbox = BoundingBox(projected_min_x, projected_max_x, projected_min_y, projected_max_y)
        #print("projected_mfov_bbox: {}".format(projected_mfov_bbox.toStr()))
        #relevant_mfovs_nums2 = []
        #for j, section2_mfov_bbox in enumerate(section2_mfov_bboxes):
        #    if projected_mfov_bbox.overlap(section2_mfov_bbox):
        #        relevant_mfovs_nums2.append(j + 1)

        # Find the "location" of mfov i's center on section2
        center1 = centers1[i]
        center1_transformed = np.dot(best_transform_matrix, np.append(center1, [1]))[0:2]
        distances = np.array([np.linalg.norm(center1_transformed - centers2[j]) for j in range(num_mfovs2)])
        print("distances:", [str(x) + ":" + str(d) for x, d in enumerate(distances)])
        relevant_mfovs_nums2 = [np.argsort(distances)[0] + 1]
        print("Initial assumption Section {} mfov {} will match Section {} mfovs {}".format(layer1, i + 1, layer2, relevant_mfovs_nums2))
        # Do an iterative search of the mfov from section 1 to the "corresponding" mfov of section2
        mfov_search_start_time = time.clock()
        mfov_transform, num_filtered, filter_rate, num_rod, num_m1, num_m2 = iterative_search(actual_params, layer1, layer2, indexed_ts1, indexed_ts2,
                             features_dir1, features_dir2, [i + 1], relevant_mfovs_nums2, section2_mfov_bboxes, num_mfovs2, assumed_model=best_transform)
        mfov_search_end_time = time.clock()
        if mfov_transform is None:
            # Could not find a transformation for the given mfov
            print("Could not find a transformation between Section {} mfov {}, to Section {} (after {} seconds), skipping the mfov".format(layer1, i + 1, layer2, mfov_search_end_time - mfov_search_start_time))
        else:
            print("Found a transformation between section {} mfov {} to section {} (filtered matches#: {}, rate: {}), with model: {}".format(layer1, i + 1, layer2, num_filtered, filter_rate, mfov_transform.get_matrix()))
            #best_transform_matrix = mfov_transform.get_matrix()
            dictentry = {}
            dictentry['mfov1'] = i + 1
            dictentry['section2_center'] = center1_transformed.tolist()
            #dictentry['mfov2'] = checkindices[j] + 1
            dictentry['features_in_mfov1'] = num_m1
            #dictentry['features_in_mfov2'] = num_m2
            dictentry['transformation'] = {
                "className": mfov_transform.class_name,
                "matrix": mfov_transform.get_matrix().tolist()
            }
            dictentry['matches_rod'] = num_rod
            dictentry['matches_model'] = num_filtered
            dictentry['filter_rate'] = filter_rate
            dictentry['mfov_search_time'] = mfov_search_end_time - mfov_search_start_time
            to_ret.append(dictentry)


    return to_ret

def match_layers_sift_features(tiles_fname1, features_dir1, tiles_fname2, features_dir2, out_fname, conf_fname=None):
    params = utils.conf_from_file(conf_fname, 'MatchLayersSiftFeaturesAndFilter')
    if params is None:
        params = {}
    actual_params = {}
    # Parameters for the matching
    actual_params["max_attempts"] = params.get("max_attempts", 10)
    actual_params["num_filtered_percent"] = params.get("num_filtered_percent", 0.25)
    actual_params["filter_rate_cutoff"] = params.get("filter_rate_cutoff", 0.25)
    actual_params["ROD_cutoff"] = params.get("ROD_cutoff", 0.92)
    actual_params["min_features_num"] = params.get("min_features_num", 40)

    # Parameters for the RANSAC
    actual_params["model_index"] = params.get("model_index", 1)
    actual_params["iterations"] = params.get("iterations", 500)
    actual_params["max_epsilon"] = params.get("max_epsilon", 500.0)
    actual_params["min_inlier_ratio"] = params.get("min_inlier_ratio", 0.01)
    actual_params["min_num_inlier"] = params.get("min_num_inliers", 7)
    actual_params["max_trust"] = params.get("max_trust", 3)

    print("Matching layers: {} and {}".format(tiles_fname1, tiles_fname2))

    starttime = time.clock()

    # Match the two sections
    retval = analyze_slices(tiles_fname1, tiles_fname2, features_dir1, features_dir2, actual_params)

    # Save the output
    jsonfile = {}
    jsonfile['tilespec1'] = tiles_fname1
    jsonfile['tilespec2'] = tiles_fname2
    jsonfile['matches'] = retval
    jsonfile['runtime'] = time.clock() - starttime
    with open(out_fname, 'w') as out:
        json.dump(jsonfile, out, indent=4)
    print("Done.")


def main():
    print(sys.argv)
    # Command line parser
    parser = argparse.ArgumentParser(description='Iterates over the mfovs in 2 tilespecs of two sections, computing matches for each overlapping mfov.')
    parser.add_argument('tiles_file1', metavar='tiles_file1', type=str,
                        help='the first layer json file containing tilespecs')
    parser.add_argument('features_dir1', metavar='features_dir1', type=str,
                        help='the first layer features directory')
    parser.add_argument('tiles_file2', metavar='tiles_file2', type=str,
                        help='the second layer json file containing tilespecs')
    parser.add_argument('features_dir2', metavar='features_dir2', type=str,
                        help='the second layer features directory')
    parser.add_argument('-o', '--output_file', type=str,
                        help='an output correspondent_spec file, that will include the matches between the sections (default: ./matches.json)',
                        default='./matches.json')
    parser.add_argument('-c', '--conf_file_name', type=str,
                        help='the configuration file with the parameters for each step of the alignment process in json format (uses default parameters, if not supplied)',
                        default=None)

    args = parser.parse_args()

    match_layers_sift_features(args.tiles_file1, args.features_dir1,
                               args.tiles_file2, args.features_dir2, args.output_file,
                               conf_fname=args.conf_file_name)


if __name__ == '__main__':
    main()