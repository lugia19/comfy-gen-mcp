import sys
import os

sys.argv.append("--http")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "comfyui-image-gen"))
from server.main import main

main()
