// hdr2sdr: HDR (HLG / PQ / Dolby Vision 8.x) -> SDR BT.709 with Apple's own tone mapping (VideoToolbox, through AVFoundation).
// usage: hdr2sdr <in> <out.mov|mp4> [--codec hevc|h264|prores] [--max-short N] [--mbps N] [--info]
// Video only. Every frame keeps its presentation time (no retiming, no frame-rate cap), so the file lines up with the source's own
// audio and with any frame index measured on it. The picture comes back as 10-bit (hevc, prores) or 8-bit (h264) tagged BT.709.
// A source that is already SDR is converted the same way (a re-encode, tags unchanged in meaning): the caller decides whether to call.
// No downloads, no ML: Apple's decoder converts primaries + transfer + tone maps when asked for BT.709 pixel buffers.
import Foundation
import AVFoundation
import CoreMedia
import CoreVideo
import VideoToolbox

func fail(_ m: String) -> Never {
    FileHandle.standardError.write(("STOP: hdr2sdr: " + m + "\n").data(using: .utf8)!)
    exit(2)
}

var args = Array(CommandLine.arguments.dropFirst())
guard args.count >= 2 else { fail("usage: hdr2sdr <in> <out.mov|mp4> [--codec hevc|h264|prores] [--max-short N] [--mbps N]") }
let inPath = args.removeFirst(), outPath = args.removeFirst()
var codec = "hevc", maxShort = 0, mbps = 0.0
var i = 0
while i < args.count {
    switch args[i] {
    case "--codec": i += 1; codec = args[i]
    case "--max-short": i += 1; maxShort = Int(args[i]) ?? 0
    case "--mbps": i += 1; mbps = Double(args[i]) ?? 0
    default: fail("unknown option \(args[i])")
    }
    i += 1
}
guard FileManager.default.fileExists(atPath: inPath) else { fail("no such file: \(inPath)") }
try? FileManager.default.removeItem(atPath: outPath)

let asset = AVURLAsset(url: URL(fileURLWithPath: inPath))
guard let vtrack = asset.tracks(withMediaType: .video).first else { fail("no video track in \(inPath)") }
let nat = vtrack.naturalSize
var W = Int(nat.width.rounded()), H = Int(nat.height.rounded())
if maxShort > 0 && min(W, H) > maxShort {
    let k = Double(maxShort) / Double(min(W, H))
    W = Int((Double(W) * k / 2).rounded()) * 2; H = Int((Double(H) * k / 2).rounded()) * 2
}
let fps = max(1.0, Double(vtrack.nominalFrameRate))

let bt709: [String: Any] = [
    AVVideoColorPrimariesKey: AVVideoColorPrimaries_ITU_R_709_2,
    AVVideoTransferFunctionKey: AVVideoTransferFunction_ITU_R_709_2,
    AVVideoYCbCrMatrixKey: AVVideoYCbCrMatrix_ITU_R_709_2,
]
let tenBit = codec != "h264"
let pixfmt: OSType = codec == "prores" ? kCVPixelFormatType_422YpCbCr10
                   : (tenBit ? kCVPixelFormatType_420YpCbCr10BiPlanarVideoRange : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange)
let readerSettings: [String: Any] = [
    kCVPixelBufferPixelFormatTypeKey as String: pixfmt,
    AVVideoColorPropertiesKey: bt709,
]
guard let reader = try? AVAssetReader(asset: asset) else { fail("cannot open \(inPath) for reading") }
let output = AVAssetReaderTrackOutput(track: vtrack, outputSettings: readerSettings)
output.alwaysCopiesSampleData = false
guard reader.canAdd(output) else { fail("the reader refused the BT.709 output settings") }
reader.add(output)

let ext = (outPath as NSString).pathExtension.lowercased()
guard let writer = try? AVAssetWriter(outputURL: URL(fileURLWithPath: outPath), fileType: ext == "mp4" ? .mp4 : .mov) else { fail("cannot write \(outPath)") }
let bitrate = Int((mbps > 0 ? mbps : min(120.0, max(12.0, Double(W * H) * min(fps, 60.0) * 0.20 / 1e6))) * 1e6)
var comp: [String: Any] = [:]
let vcodec: AVVideoCodecType
switch codec {
case "h264": vcodec = .h264; comp = [AVVideoAverageBitRateKey: bitrate, AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel]
case "prores": vcodec = .proRes422
default: vcodec = .hevc; comp = [AVVideoAverageBitRateKey: bitrate, AVVideoProfileLevelKey: kVTProfileLevel_HEVC_Main10_AutoLevel as String]
}
var ws: [String: Any] = [AVVideoCodecKey: vcodec, AVVideoWidthKey: W, AVVideoHeightKey: H, AVVideoColorPropertiesKey: bt709]
if !comp.isEmpty { ws[AVVideoCompressionPropertiesKey] = comp }
let input = AVAssetWriterInput(mediaType: .video, outputSettings: ws)
input.expectsMediaDataInRealTime = false
input.transform = vtrack.preferredTransform
guard writer.canAdd(input) else { fail("the writer refused \(codec) at \(W)x\(H)") }
writer.add(input)
guard writer.startWriting() else { fail("writer: \(writer.error?.localizedDescription ?? "?")") }
guard reader.startReading() else { fail("reader: \(reader.error?.localizedDescription ?? "?")") }
// The session starts at the first real frame's presentation time, so the writer adds no frame ahead of it; the file's first frame is then
// at 0 and "first_pts" (printed, in seconds) is how far the source's picture sat after 0 (apply it back with ffmpeg -itsoffset).
// AVAssetReader hands back a frame at movie time 0 for a clip whose first frame sits later (a stream-copied excerpt, an edit-list
// shift): a duplicate of the first frame, held to fill the track's leading EMPTY EDIT (an iPhone clip's video starts 0.025 to 0.035 s into
// the movie). It is dropped when the track opens with an empty edit and the frame sits inside it, and, for any other shift, when the gap
// to the next frame is not about one frame duration (under 0.5 or over 1.6). The gap rule alone kept the iPhone's: 0.027 s is 0.8 of a 30 fps frame.
var n = 0
var firstPTS = 0.0
var started = false
var held: CMSampleBuffer? = nil
let frameDur = 1.0 / fps
var leadingEmpty = 0.0      // how long the track's first edit is empty: the movie time before the first real frame, seconds
if let seg = vtrack.segments.first, seg.isEmpty { leadingEmpty = CMTimeGetSeconds(seg.timeMapping.target.duration) }
// HLG / PQ sources carry viewing-environment and mastering metadata on every frame; the picture leaving here is plain SDR, so none of it travels on.
func scrub(_ sb: CMSampleBuffer) {
    guard let pb = CMSampleBufferGetImageBuffer(sb) else { return }
    for key in [kCVImageBufferAmbientViewingEnvironmentKey, kCVImageBufferMasteringDisplayColorVolumeKey, kCVImageBufferContentLightLevelInfoKey] {
        CVBufferRemoveAttachment(pb, key)
    }
}

func push(_ sb: CMSampleBuffer) {
    scrub(sb)
    if !started {
        let p = CMSampleBufferGetPresentationTimeStamp(sb)
        firstPTS = CMTimeGetSeconds(p)
        writer.startSession(atSourceTime: p)
        started = true
    }
    while !input.isReadyForMoreMediaData { usleep(500) }
    if !input.append(sb) { fail("append failed at frame \(n): \(writer.error?.localizedDescription ?? "?")") }
    n += 1
}
while reader.status == .reading {
    guard let sb = output.copyNextSampleBuffer() else { break }
    if let h = held {
        let hp = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(h))
        let gap = CMTimeGetSeconds(CMSampleBufferGetPresentationTimeStamp(sb)) - hp
        held = nil
        let heldInEmptyEdit = leadingEmpty > 0 && hp < leadingEmpty - 0.001
        if !heldInEmptyEdit && gap >= 0.5 * frameDur && gap <= 1.6 * frameDur { push(h) }   // else: the held frame was the movie-time-0 duplicate
        push(sb)
    } else if n == 0 && !started { held = sb }
    else { push(sb) }
}
if let h = held { push(h) }
if !started { fail("the video track decoded no frames") }
if reader.status == .failed { fail("read failed: \(reader.error?.localizedDescription ?? "?")") }
input.markAsFinished()
let sem = DispatchSemaphore(value: 0)
writer.finishWriting { sem.signal() }
sem.wait()
if writer.status != .completed { fail("write failed: \(writer.error?.localizedDescription ?? "?")") }
print("hdr2sdr: frames=\(n) first_pts=\(String(format: "%.6f", firstPTS)) size=\(W)x\(H) codec=\(codec) -> \(outPath)")
