
An Open Sea Dragon web viewer for a single connectome section.

Assumes the existance of a directory "tiles" that contains an osd.js file with a getSourceTiles() function. The osd.js file can be created using the create_zoomed_tiles.py (with the -s flag).

To run, just execute: "python -m SimpleHTTPServer [PortNum]" from the local directory, and browse to the newly created web server.

