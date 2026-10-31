// textboxes: every line of text Apple Vision can read in each image, with its box.
// usage: textboxes a.png b.png ...  ->  <path>\t<x>\t<y>\t<w>\t<h>\t<text>   (pixels, top-left origin; nothing for no text)
import Foundation
import Vision
import AppKit

for path in CommandLine.arguments.dropFirst() {
    guard let img = NSImage(contentsOfFile: path), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else { continue }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = false
    do { try VNImageRequestHandler(cgImage: cg, options: [:]).perform([req]) } catch { continue }
    let W = CGFloat(cg.width), H = CGFloat(cg.height)
    for obs in req.results ?? [] {
        guard let top = obs.topCandidates(1).first, top.confidence > 0.3 else { continue }
        let b = obs.boundingBox
        let x = b.origin.x * W, w = b.width * W, h = b.height * H, y = (1 - b.origin.y - b.height) * H
        let text = top.string.replacingOccurrences(of: "\t", with: " ")
        print("\(path)\t\(Int(x))\t\(Int(y))\t\(Int(w))\t\(Int(h))\t\(text)")
    }
}
