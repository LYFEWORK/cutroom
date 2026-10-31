"""_shim.py: what the Linux stand-ins for the Apple Vision helpers share.

On a Mac the engine's face boxes, person mattes, on-screen text and HDR conversion come from the six Swift helpers in engine/swift/,
built on first use. On Linux (Windows through WSL2) the same six names resolve to the executables in this folder, with the same command
lines and the same output, so no caller changes: engine/swift/swiftbin.py and engine/captions/swiftbin.py hand back the stand-in instead of a
binary. The stand-ins use OpenCV (faces), onnxruntime with the Robust Video Matting model (person mattes), Tesseract (text) and ffmpeg's
tone mapper (HDR). Their Python packages live in the word-timing environment (config.json venv_python); a stand-in started under another
python re-runs itself there.

Every stand-in answers `--check` with one `ok <name>: ...` line and exit 0 when it can run (packages importable, model present, tool on PATH),
else one `STOP: <name>: ...` line and exit 2. Model files live in $CUTROOM_MODELS, else ~/.cutroom/models (install.sh puts them there; a
stand-in downloads a missing one itself and says so).
"""
import importlib.util
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
if ENGINE not in sys.path:
    sys.path.insert(0, ENGINE)
import support  # noqa: E402  (the Python version guard, and where the venv is)
import common  # noqa: E402

YUNET = ("face_detection_yunet_2023mar.onnx",
         "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx", 232589)
RVM = ("rvm_mobilenetv3_fp32.onnx", "https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx", 14975696)


def fail(name, msg):
    print(f"STOP: {name}: {msg}", file=sys.stderr)
    sys.exit(2)


def reexec_if_missing(modules):
    """Re-run this script under the word-timing python when the current one cannot import `modules`. Returns the names still missing
    (the caller stops with a plain line) or [] when every module is there."""
    missing = [m for m in modules if importlib.util.find_spec(m) is None]
    if not missing:
        return []
    venv = support.venv_python(must_exist=False)
    # "already in the venv" is judged by the interpreter's prefix, never by real paths: a venv's bin/python is a symlink to the system
    # python3.12 on Ubuntu, so the real paths match while the site-packages do not
    in_venv = bool(venv) and os.path.abspath(sys.prefix) == os.path.abspath(os.path.dirname(os.path.dirname(venv)))
    if venv and os.access(venv, os.X_OK) and not in_venv and not os.environ.get("CUTROOM_SHIM_REEXEC"):
        os.environ["CUTROOM_SHIM_REEXEC"] = "1"
        os.execv(venv, [venv, os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    return missing


def models_dir():
    return os.environ.get("CUTROOM_MODELS") or str(common.CONF_DIR / "models")


def model(name, url, size, who):
    """the path of a model file, downloaded into the models folder when it is not there yet (size-checked, written as .part then moved)"""
    path = os.path.join(models_dir(), name)
    if os.path.isfile(path) and os.path.getsize(path) == size:
        return path
    os.makedirs(models_dir(), exist_ok=True)
    part = path + ".part"
    print(f"{who}: fetching {name} ({size / 1e6:.1f} MB) into {models_dir()}", file=sys.stderr)
    try:
        urllib.request.urlretrieve(url, part)
    except Exception as e:   # noqa: BLE001 - any network failure is one plain line
        fail(who, f"could not download {name} from {url} ({type(e).__name__}: {e}); run install.sh with network, or put the file at {path}")
    if os.path.getsize(part) != size:
        os.remove(part)
        fail(who, f"{name} downloaded with the wrong size (expected {size} bytes); try again")
    os.replace(part, path)
    return path


def imread(cv2, path):
    """an image as BGR, or None (a path that is not an image prints `none`, as the Apple helper does)"""
    try:
        return cv2.imread(path, cv2.IMREAD_COLOR)
    except Exception:   # noqa: BLE001
        return None


def yunet(cv2, model_path, w, h):
    """OpenCV's YuNet face detector sized to one image: boxes plus five landmarks, the largest face is the speaker"""
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)   # OpenCV 5 warns about a backend target it ignores anyway
    except AttributeError:
        pass
    det = cv2.FaceDetectorYN.create(model_path, "", (w, h), 0.7, 0.3, 5000)
    det.setInputSize((w, h))
    return det


def largest_face(faces):
    """the row of the biggest face box, or None"""
    if faces is None or len(faces) == 0:
        return None
    return max(faces, key=lambda f: float(f[2]) * float(f[3]))


def clamp_box(x, y, w, h, W, H):
    """a box kept inside the image, as the Apple helpers' normalized boxes always are"""
    x0, y0 = max(0.0, x), max(0.0, y)
    x1, y1 = min(float(W), x + w), min(float(H), y + h)
    return x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0)


def ok(name, detail):
    print(f"ok {name}: {detail}")
    sys.exit(0)
