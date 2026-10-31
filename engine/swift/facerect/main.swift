// facerect — prints the largest face rect per image using Apple Vision (no downloads, no ML deps).
// usage: facerect img1.png img2.png ...   ->  one line per image: <path>\t<x>\t<y>\t<w>\t<h>  (pixels, top-left origin; "none" when no face)
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("\(path)\tnone"); continue
    }
    let req = VNDetectFaceRectanglesRequest()
    let handler = VNImageRequestHandler(cgImage: cg, options: [:])
    do { try handler.perform([req]) } catch { print("\(path)\tnone"); continue }
    let W = CGFloat(cg.width), H = CGFloat(cg.height)
    if let best = (req.results ?? []).max(by: { $0.boundingBox.width * $0.boundingBox.height < $1.boundingBox.width * $1.boundingBox.height }) {
        let b = best.boundingBox   // normalized, origin bottom-left
        let x = b.origin.x * W, w = b.width * W, h = b.height * H
        let y = (1 - b.origin.y - b.height) * H
        print("\(path)\t\(Int(x))\t\(Int(y))\t\(Int(w))\t\(Int(h))")
    } else { print("\(path)\tnone") }
}
