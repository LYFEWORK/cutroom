"""_rvm.py: the person matte behind personmask and personmatte on Linux: Robust Video Matting (mobilenetv3, ONNX) through onnxruntime.

One session per run. `Matter.alpha(rgb)` takes an H x W x 3 uint8 RGB frame and returns the alpha matte as H x W uint8 (255 = person).
The model is recurrent: consecutive frames of one shot share its state, which is what keeps edges steady; a size change resets it.
`quality` picks the internal working width the model sees (the output is always full size): fast 256, balanced 384, accurate 512.
"""
import numpy as np

WIDTHS = {"fast": 256, "balanced": 384, "accurate": 512}


class Matter:
    def __init__(self, ort, model_path, quality="accurate"):
        self.sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self.width = WIDTHS.get(quality, WIDTHS["accurate"])
        self.state = None
        self.size = None

    def reset(self):
        self.state = [np.zeros((1, 1, 1, 1), np.float32) for _ in range(4)]

    def alpha(self, rgb):
        h, w = rgb.shape[:2]
        if self.state is None or self.size != (w, h):
            self.reset()
            self.size = (w, h)
        src = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None].astype(np.float32) / 255.0)
        ratio = np.array([min(1.0, self.width / max(w, h))], np.float32)
        feeds = {"src": src, "r1i": self.state[0], "r2i": self.state[1], "r3i": self.state[2], "r4i": self.state[3], "downsample_ratio": ratio}
        fgr, pha, *rec = self.sess.run(["fgr", "pha", "r1o", "r2o", "r3o", "r4o"], feeds)
        self.state = [np.ascontiguousarray(r) for r in rec]
        return np.clip(pha[0, 0] * 255.0 + 0.5, 0, 255).astype(np.uint8)
