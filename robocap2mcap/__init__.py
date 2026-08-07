import sys
from pathlib import Path

# Make robocap2mcap/foxglove/* importable as top-level `foxglove` package
# (required by grpc-compiled pb2 files which use `from foxglove import ...`)
_pkg_dir = Path(__file__).parent
if str(_pkg_dir) not in sys.path:
    sys.path.insert(0, str(_pkg_dir))
