// personmatte — per-frame person segmentation mattes with Apple Vision (no downloads).
// usage: personmatte <video.mp4> <outdir> [quality: fast|balanced|accurate]  -> outdir/m%05d.png (8-bit gray, frame size)
import Foundation
import AVFoundation
import Vision
import CoreImage
import AppKit

let args = CommandLine.arguments
guard args.count >= 3 else { print("usage: personmatte <video> <outdir> [fast|balanced|accurate]"); exit(1) }
let url = URL(fileURLWithPath: args[1]); let outDir = args[2]
let q = args.count > 3 ? args[3] : "accurate"
try? FileManager.default.createDirectory(atPath: outDir, withIntermediateDirectories: true)
let asset = AVAsset(url: url)
guard let track = asset.tracks(withMediaType: .video).first else { print("no video track"); exit(1) }
let reader = try! AVAssetReader(asset: asset)
let out = AVAssetReaderTrackOutput(track: track, outputSettings: [kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA])
reader.add(out); reader.startReading()
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = q == "fast" ? .fast : q == "balanced" ? .balanced : .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
let ctx = CIContext()
let size = track.naturalSize.applying(track.preferredTransform)
let W = Int(abs(size.width)), H = Int(abs(size.height))
var n = 0
while let sb = out.copyNextSampleBuffer() {
    guard let pb = CMSampleBufferGetImageBuffer(sb) else { continue }
    let handler = VNImageRequestHandler(cvPixelBuffer: pb, orientation: .up, options: [:])
    do { try handler.perform([req]) } catch { print("frame \(n): \(error)"); n += 1; continue }
    guard let mask = req.results?.first?.pixelBuffer else { n += 1; continue }
    var ci = CIImage(cvPixelBuffer: mask)
    let sx = CGFloat(CVPixelBufferGetWidth(pb)) / ci.extent.width, sy = CGFloat(CVPixelBufferGetHeight(pb)) / ci.extent.height
    ci = ci.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
    let path = String(format: "%@/m%05d.png", outDir, n)
    if let cg = ctx.createCGImage(ci, from: ci.extent) {
        let rep = NSBitmapImageRep(cgImage: cg)
        if let data = rep.representation(using: .png, properties: [:]) { try? data.write(to: URL(fileURLWithPath: path)) }
    }
    n += 1
    if n % 60 == 0 { print("frames: \(n)"); fflush(stdout) }
}
print("done: \(n) frames, \(W)x\(H)")
