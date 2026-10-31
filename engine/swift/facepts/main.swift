// facepts — prints the largest face per image with its two eye centers, using Apple Vision landmarks (no downloads, no ML deps).
// usage: facepts img1.jpg img2.jpg ...   ->  one line per image: <path>\t<x>\t<y>\t<w>\t<h>\t<lx>\t<ly>\t<rx>\t<ry>   ("none" when no face)
// Pixels, top-left origin, sub-pixel floats. An eye center is the mean of the eye's contour, not the pupil, so a glance
// sideways does not move it. Vision's landmarks request returns roll and yaw as 0, so neither is printed: the eye line carries the tilt.
import Foundation
import Vision
import AppKit

func center(_ r: VNFaceLandmarkRegion2D?, _ size: CGSize) -> CGPoint? {
    guard let r = r, r.pointCount > 2 else { return nil }
    let pts = r.pointsInImage(imageSize: size)
    let n = CGFloat(pts.count)
    return CGPoint(x: pts.map { $0.x }.reduce(0, +) / n, y: size.height - pts.map { $0.y }.reduce(0, +) / n)
}

for path in CommandLine.arguments.dropFirst() {
    autoreleasepool {   // one frame of a whole cut at a time: without it every decoded image stays in memory until the last one
        guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            print("\(path)\tnone"); return
        }
        let req = VNDetectFaceLandmarksRequest()
        let handler = VNImageRequestHandler(cgImage: cg, options: [:])
        do { try handler.perform([req]) } catch { print("\(path)\tnone"); return }
        let size = CGSize(width: cg.width, height: cg.height)
        guard let best = (req.results ?? []).max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }),
              let l = center(best.landmarks?.leftEye, size), let r = center(best.landmarks?.rightEye, size) else {
            print("\(path)\tnone"); return
        }
        let b = best.boundingBox   // normalized, origin bottom-left
        let x = b.origin.x * size.width, w = b.width * size.width, h = b.height * size.height
        let y = (1 - b.origin.y - b.height) * size.height
        let f = { (v: CGFloat) in String(format: "%.2f", Double(v)) }
        print("\(path)\t\(f(x))\t\(f(y))\t\(f(w))\t\(f(h))\t\(f(l.x))\t\(f(l.y))\t\(f(r.x))\t\(f(r.y))")
    }
}
