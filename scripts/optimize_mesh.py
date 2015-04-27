import sys
import ujson as json
import glob
import os.path
import time
import numpy as np
from numpy.linalg import lstsq
import cPickle as pickle
from scipy.spatial import cKDTree as KDTree  # for searching surrounding points
from collections import defaultdict
from progressbar import ProgressBar, ETA, Bar, Counter

import pyximport
pyximport.install()
import mesh_derivs

FLOAT_TYPE = np.float64


sys.setrecursionlimit(10000)  # for grad

class MeshParser(object):
    def __init__(self, mesh_file, multiplier=100):
        # load the mesh
        self.mesh = json.load(open(mesh_file))
        self.pts = np.array([(p["x"], p["y"]) for p in self.mesh["points"]], dtype=FLOAT_TYPE)
        self.rowcols = np.array([(p["row"], p["col"]) for p in self.mesh["points"]])
        self.layer_scale = float(self.mesh["layerScale"])

        print("# points in base mesh {}".format(self.pts.shape[0]))

        self.multiplier = multiplier
        # seed rowcolidx to match mesh_pts array
        self._rowcolidx = {}
        for idx, pt in enumerate(self.pts):
            self._rowcolidx[int(pt[0] * multiplier), int(pt[1] * multiplier)] = idx

        # Build the KDTree for neighbor searching
        self.kdt = KDTree(self.pts, leafsize=3)

        mesh_p_x = self.pts[:, 0]
        mesh_p_y = self.pts[:, 1]
        self.long_row_min_x = min(mesh_p_x)
        self.long_row_min_y = min(mesh_p_y)
        self.long_row_max_x = max(mesh_p_x)
        self.long_row_max_y = max(mesh_p_y)
        print("Mesh long-row boundary values: min_x: {}, miny: {}, max_x: {}, max_y: {}".format(
            self.long_row_min_x, self.long_row_min_y, self.long_row_max_x, self.long_row_max_y))
        self.short_row_min_x = min(mesh_p_x[mesh_p_x > self.long_row_min_x])
        self.short_row_min_y = min(mesh_p_y[mesh_p_y > self.long_row_min_y])
        self.short_row_max_x = max(mesh_p_x[mesh_p_x < self.long_row_max_x])
        self.short_row_max_y = max(mesh_p_y[mesh_p_y < self.long_row_max_y])
        print("Mesh short-row boundary values: min_x: {}, miny: {}, max_x: {}, max_y: {}".format(
            self.short_row_min_x, self.short_row_min_y, self.short_row_max_x, self.short_row_max_y))

    def rowcolidx(self, xy):
        return self._rowcolidx[int(xy[0] * self.multiplier), int(xy[1] * self.multiplier)]

    def query_internal(self, points, k):
        """Returns the k nearest neighbors, while taking into account the mesh formation.
        If a point is on the boudaries of the mesh, only the relevant neighbors will be returned,
        and all others will have distance 0, and neighbor index == the query point"""
        dists, surround_indices = self.kdt.query(points, k + 1)  # we're querying against ourselves, usually

        # Find out if the point is on the "boundary"
        for i, p in enumerate(points):
            # on the left side of the mesh
            if p[0] < self.short_row_min_x:
                # remove any surrounding point that is to the right of short_row_min_x
                point_surround_indices = surround_indices[i].astype(np.int32)
                tris_x = self.pts[point_surround_indices, 0]
                dists[i][tris_x > self.short_row_min_x] = 0.0
                surround_indices[i][tris_x > self.short_row_min_x] = surround_indices[i][0]
            # on the left side of the mesh
            if p[0] > self.short_row_max_x:
                # remove any surrounding point that is to the left of short_row_max_x
                point_surround_indices = surround_indices[i].astype(np.int32)
                tris_x = self.pts[point_surround_indices, 0]
                dists[i][tris_x < self.short_row_max_x] = 0.0
                surround_indices[i][tris_x < self.short_row_max_x] = surround_indices[i][0]
            # on the upper side of the mesh
            if p[1] < self.short_row_min_y:
                # remove any surrounding point that is above short_row_min_y
                point_surround_indices = surround_indices[i].astype(np.int32)
                tris_y = self.pts[point_surround_indices, 1]
                dists[i][tris_y > self.short_row_min_y] = 0.0
                surround_indices[i][tris_y > self.short_row_min_y] = surround_indices[i][0]
            # on the lower side of the mesh
            if p[1] > self.short_row_max_y:
                # remove any surrounding point that is under short_row_max_y
                point_surround_indices = surround_indices[i].astype(np.int32)
                tris_y = self.pts[point_surround_indices, 1]
                dists[i][tris_y < self.short_row_max_y] = 0.0
                surround_indices[i][tris_y < self.short_row_max_y] = surround_indices[i][0]

        return dists[:, 1:], surround_indices[:, 1:]  # chop off first closest (= self)

    def query_cross(self, points, k):
        """Returns the k nearest neighbors"""
        dists, surround_indices = self.kdt.query(points, k)

        return dists, surround_indices


def barycentric(pt, verts_x, verts_y):
    '''computes the barycentric weights to reconstruct an array of points in an
    array of triangles.

    '''
    x, y = pt.T
    x_1, x_2, x_3 = verts_x.T
    y_1, y_2, y_3 = verts_y.T

    # from wikipedia
    den = ((y_2 - y_3) * (x_1 - x_3) + (x_3 - x_2) * (y_1 - y_3))
    l1 = ((y_2 - y_3) * (x - x_3) + (x_3 - x_2) * (y - y_3)) / den
    l2 = ((y_3 - y_1) * (x - x_3) + (x_1 - x_3) * (y - y_3)) / den
    l3 = 1 - l1 - l2
    mask = (den == 0)
    l1[mask] = l2[mask] = l3[mask] = 1.0 / 3
    return l1.reshape((-1, 1)).astype(np.float32), l2.reshape((-1, 1)).astype(np.float32), l3.reshape((-1, 1)).astype(np.float32)

def load_matches(matches_files, mesh, url_to_layerid):
    pbar = ProgressBar(widgets=['Loading matches: ', Counter(), ' / ', str(len(matches_files)), " ", Bar(), ETA()])

    for midx, mf in enumerate(pbar(matches_files)):
        for m in json.load(open(mf)):
            if not m['shouldConnect']:
                continue
            # parse matches file, and get p1's mesh x and y points
            orig_p1s = np.array([(pair["p1"]["l"][0], pair["p1"]["l"][1]) for pair in m["correspondencePointPairs"]], dtype=FLOAT_TYPE)

            p1_rc_indices = [mesh.rowcolidx(p1) for p1 in orig_p1s]

            p2_locs = np.array([pair["p2"]["l"] for pair in m["correspondencePointPairs"]])
            dists, surround_indices = mesh.query_cross(p2_locs, 3)
            surround_indices = surround_indices.astype(np.uint32)
            tris_x = mesh.pts[surround_indices, 0]
            tris_y = mesh.pts[surround_indices, 1]
            w1, w2, w3 = barycentric(p2_locs, tris_x, tris_y)

            # TODO: figure out why this is needed
            reorder = np.argsort(p1_rc_indices)
            p1_rc_indices = np.array(p1_rc_indices).astype(np.uint32)[reorder]
            surround_indices = surround_indices[reorder, :]
            weights = np.hstack((w1, w2, w3)).astype(FLOAT_TYPE)[reorder, :]
            assert p1_rc_indices.shape[0] == weights.shape[0]
            yield url_to_layerid[m["url1"]], url_to_layerid[m["url2"]], p1_rc_indices, surround_indices, weights

def linearize_grad(positions, gradients):
    '''perform a least-squares fit, then return the values from that fit'''
    positions = np.hstack((positions, np.ones((positions.shape[0], 1))))
    XTX = np.dot(positions.T, positions)
    XTY = np.dot(positions.T, gradients)
    Beta = np.dot(np.linalg.inv(XTX), XTY)
    return np.dot(positions, Beta)

def blend(a, b, t):
    '''at t=0, return a, at t=1, return b'''
    return a + (b - a) * t

def mean_offset(all_pairs, all_mesh_pts, bary_indices, bary_weights, lo, hi):
    num_pts = all_mesh_pts.shape[1]
    means = []
    for id1, id2, baryoff1, baryoff2 in all_pairs:
        if id1 >= hi or id2 >= hi:
            continue
        if id1 < lo and id2 < lo:
            continue
        if abs(int(id1) - int(id2)) == 1:
            mesh1 = all_mesh_pts[id1, ...]
            mesh2 = all_mesh_pts[id2, ...]
            bindices = bary_indices[baryoff1:(baryoff1 + num_pts), ...]
            mesh_1_matches = mesh2[bindices, ...]
            mesh_1_matches *= bary_weights[baryoff1:(baryoff1 + num_pts), ..., np.newaxis]
            delta = (mesh1 - mesh_1_matches.sum(axis=1)) ** 2
            means.append(np.sqrt(delta.sum(axis=1)[bindices[:, 0] != -1]).mean())

            bindices = bary_indices[baryoff2:(baryoff2 + num_pts), ...]
            mesh_2_matches = mesh1[bindices, ...]
            mesh_2_matches *= bary_weights[baryoff2:(baryoff2 + num_pts), ..., np.newaxis]
            delta = (mesh2 - mesh_2_matches.sum(axis=1)) ** 2
            means.append(np.median(np.sqrt(delta.sum(axis=1)[bindices[:, 0] != -1])))
    return np.mean(means)


def optimize_meshes(mesh_file, matches_files, url_to_layerid, conf_dict={}):
    # set default values
    cross_slice_weight = conf_dict.get("cross_slice_weight", 1.0)
    cross_slice_winsor = conf_dict.get("cross_slice_winsor", 1000)
    intra_slice_weight = conf_dict.get("intra_slice_weight", 1.0 / 6)
    intra_slice_winsor = conf_dict.get("intra_slice_winsor", 200)

    block_size = conf_dict.get("block_size", 35)
    block_step = conf_dict.get("block_step", 25)
    rigid_iterations = conf_dict.get("rigid_iterations", 50)
    min_iterations = conf_dict.get("min_iterations", 200)
    max_iterations = conf_dict.get("max_iterations", 2000)
    num_threads = conf_dict.get("optimization_threads", 4)

    # Load the mesh
    mesh = MeshParser(mesh_file)
    num_pts = mesh.pts.shape[0]

    # Adjust winsor values according to layer scale
    cross_slice_winsor = cross_slice_winsor * mesh.layer_scale
    intra_slice_winsor = intra_slice_winsor * mesh.layer_scale

    # load the slice-to-slice matches
    cross_links = dict(((v[0], v[1]), v[2:]) for v in load_matches(matches_files, mesh, url_to_layerid))

    # find all the slices represented
    present_slices = sorted(list(set(k[0] for k in cross_links) | set(k[1] for k in cross_links)))
    num_meshes = len(present_slices)

    # build mesh array for all meshes
    all_mesh_pts = np.concatenate([mesh.pts[np.newaxis, ...]] * len(present_slices), axis=0)
    mesh_pt_offsets = dict(zip(present_slices, np.arange(len(present_slices))))

    # Build internal structural mesh
    dists, neighbor_indices = mesh.query_internal(mesh.pts, 6)
    neighbor_indices = neighbor_indices.astype(np.uint32)
    dists = dists.astype(FLOAT_TYPE)

    # cross-mesh links

    # we assume nearly every point has a match, so we store a set of 3
    # barycentric neighbor indices & weights for each point in the mesh, and
    # use neighbor[0] == -1 to indicate no match.
    bary_indices = []
    bary_weights = []
    bary_offsets = {}
    for (id1, id2), (m1_indices, m2_surround_indices, m2_weights) in sorted(cross_links.iteritems()):
        cur_bary_indices = -np.ones((num_pts, 3), np.int32)
        cur_bary_weights = np.zeros((num_pts, 3), FLOAT_TYPE)
        for idx, bn_indices, bn_weights in zip(m1_indices, m2_surround_indices, m2_weights):
            cur_bary_indices[idx, :] = bn_indices
            cur_bary_weights[idx, :] = bn_weights
        assert m1_indices.shape[0] == m2_surround_indices.shape[0] == m2_weights.shape[0]
        bary_indices.append(cur_bary_indices)
        bary_weights.append(cur_bary_weights)
        bary_offsets[id1, id2] = (len(bary_indices) - 1) * num_pts

    bary_indices = np.vstack(bary_indices)
    bary_weights = np.vstack(bary_weights)

    # build the list of all pairs with their offsets
    all_pairs = np.vstack(sorted([(mesh_pt_offsets[id1],
                                   mesh_pt_offsets[id2],
                                   bary_offsets[id1, id2],
                                   bary_offsets[id2, id1])
                                  for id1, id2 in bary_offsets.keys() if id1 < id2])).astype(np.uint32)
    assert 2 * len(all_pairs) == len(cross_links)
    between_mesh_weights = np.array([cross_slice_weight / float(abs(int(id1) - int(id2)))
                                     for id1, id2, _, _ in all_pairs],
                                    dtype=FLOAT_TYPE)


    oldtick = time.time()

    pbar = ProgressBar(widgets=[Bar(), ETA()])

    for block_lo in (range(0, max(1, num_meshes - block_size + 1), block_step)):
        print
        block_hi = min(block_lo + block_size, num_meshes)

        gradient = np.empty_like(all_mesh_pts[block_lo:block_hi, ...])
        cost = mesh_derivs.all_derivs(all_mesh_pts,
                                      gradient,
                                      neighbor_indices,
                                      dists,
                                      bary_indices,
                                      bary_weights,
                                      between_mesh_weights,
                                      intra_slice_weight,
                                      cross_slice_winsor,
                                      intra_slice_winsor,
                                      all_pairs,
                                      block_lo, block_hi,
                                      num_threads)

        m_o = mean_offset(all_pairs, all_mesh_pts, bary_indices, bary_weights, block_lo, block_hi)
        print "BEFORE", "C", cost, "MO", m_o

        # first, do some rigid alignment, slice at a time
        for single_slice_idx in range(block_lo, block_hi):
            gradient = np.empty_like(all_mesh_pts[single_slice_idx:(single_slice_idx + 1), ...])
            step_size = 0.1
            prev_cost = np.inf
            for iter in range(rigid_iterations):
                cost = mesh_derivs.all_derivs(all_mesh_pts,
                                              gradient,
                                              neighbor_indices,
                                              dists,
                                              bary_indices,
                                              bary_weights,
                                              between_mesh_weights,
                                              intra_slice_weight,
                                              cross_slice_winsor,
                                              intra_slice_winsor,
                                              all_pairs,
                                              single_slice_idx, single_slice_idx + 1,
                                              1)
                if cost < prev_cost:
                    lin_grad = linearize_grad(all_mesh_pts[single_slice_idx, ...],
                                              gradient[0, ...])
                    all_mesh_pts[single_slice_idx, ...] -= step_size * lin_grad
                    step_size = min(1.0, step_size * 1.1)
                else:
                    all_mesh_pts[single_slice_idx, ...] += step_size * lin_grad
                    step_size = 0.5 * step_size
                prev_cost = cost

        gradient_with_momentum = 0
        stepsize = 0.1
        prev_cost = np.inf
        gradient = np.empty_like(all_mesh_pts[block_lo:block_hi, ...])

        for iter in range(max_iterations):
            cost = mesh_derivs.all_derivs(all_mesh_pts,
                                          gradient,
                                          neighbor_indices,
                                          dists,
                                          bary_indices,
                                          bary_weights,
                                          between_mesh_weights,
                                          intra_slice_weight,
                                          cross_slice_winsor,
                                          intra_slice_winsor,
                                          all_pairs,
                                          block_lo, block_hi,
                                          4)

            if iter % 100 == 0:
                m_o = mean_offset(all_pairs, all_mesh_pts, bary_indices, bary_weights, block_lo, block_hi)
                print iter, "SL:", block_lo, block_hi, num_meshes, "COST:", cost, "MO:", m_o, "SZ:", stepsize, "T:", time.time() - oldtick
                oldtick = time.time()
                if (iter >= min_iterations) and (m_o < .75):
                    break

            # relaxation of the mesh
            # initially, mesh is held rigid (all points transform together).
            # mesh is allowed to deform as iterations progress.
            relaxation_end = int(min_iterations)
            if iter < relaxation_end:
                # for each mesh, compute a linear fit to the gradient
                for meshidx in range(block_lo, block_hi):
                    gidx = meshidx - block_lo
                    linearized = linearize_grad(all_mesh_pts[meshidx, ...], gradient[gidx, ...])
                    gradient[gidx, ...] = blend(linearized, gradient[gidx, ...], iter / float(relaxation_end))

            # step size adjustment
            if cost <= prev_cost:
                stepsize *= 1.1
                if stepsize > 1.0:
                    stepsize = 1.0
                # update with new gradients
                gradient_with_momentum = (gradient + 0.5 * gradient_with_momentum)
                all_mesh_pts[block_lo:block_hi, ...] -= stepsize * gradient_with_momentum
                prev_cost = cost
            else:  # we took a bad step: undo it, scale down stepsize, and start over
                all_mesh_pts[block_lo:block_hi, ...] += stepsize * gradient_with_momentum
                stepsize *= 0.5
                gradient_with_momentum = 0.0
                prev_cost = np.inf

    # Prepare per-layer output
    out_positions = {}

    for url, layerid in url_to_layerid.iteritems():
        if layerid in present_slices:
            meshidx = mesh_pt_offsets[layerid]
            out_positions[url] = [(mesh.pts / mesh.layer_scale).tolist(),
                                  (all_mesh_pts[meshidx, :] / mesh.layer_scale).tolist()]
        else:
            out_positions[url] = [(mesh.pts / mesh.layer_scale).tolist(),
                                  (mesh.pts / mesh.layer_scale).tolist()]

    return out_positions


if __name__ == '__main__':
    mesh_file = sys.argv[1]
    matches_files = glob.glob(os.path.join(sys.argv[2], '*W02_sec0[012]*W02_sec0[012]*.json'))
    print("Found {} match files".format(len(matches_files)))
    url_to_layerid = None
    new_positions = optimize_meshes(mesh_file, matches_files, url_to_layerid)

    out_file = sys.argv[3]
    json.dump(new_positions, open(out_file, "w"), indent=1)