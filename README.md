# rh_aligner
This is a repo forked from [Rhoana](https://github.com/Rhoana/rh_aligner)
## Description
A Rhoana tool for 2D stitching and 3D alignemnt of a stack of EM images.
Currently supports 2D rigid stitching, and 3D elastic (non-affine) alignment.

## Requirements
- Python version = 3.x (3.7 is recommended)
- Requires OpenCV 3.x 
   - I suggest to install opencv using conda and set PKG_CONFIG_PATH as the path in conda envs.  
**Tips:**
   - **DON'T** use OpenCV2.x because of python3.
   - **DON'T** use OpenCV4.x because there are some significant changes that could lead to conflicts.
   - A later version will support OpenCV4.  
- Python requirements in [requirements.txt](requirements.txt)

## Installation
### Install OpenCV3(Check your PKG_CONFIG_PATH if you have installed before)
```
$ conda create -n YOUR_ENV python=3.7
$ conda install opencv=3.4
$ export PKG_CONFIG_PATH=/path/to/conda envs/YOUR_ENV/lib/pkgconfig/
```
### Install rh_renderer
Following the [README.md](https://github.com/HoraceKem/rh_renderer) in rh_renderer repo.
### Install this repo
```
$ git clone git@github.com:HoraceKem/rh_aligner.git
$ cd rh_aligner
$ pip install -r requirements.txt
$ pip install --editable .
```

## Usage

TODO.

