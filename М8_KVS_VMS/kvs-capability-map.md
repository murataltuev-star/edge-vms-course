# What else Kinesis Video Streams gives you

**Cloud VMS · Capability Survey**

Every KVS capability that a video management system can actually use, ordered by how far it sits from the MVP you've already built — from one changed query parameter to an entirely separate subsystem.

---

## Your MVP uses six of them

The course covers stream provisioning, ingestion through `kvssink`, fragment listing, and on-demand HLS playback. Everything below is additive to that.

`CreateStream` · `DescribeStream` · `GetDataEndpoint` · `PutMedia` · `ListFragments` · `GetHLSStreamingSessionURL`

---

## Already reachable — change a parameter

*No new code*

These use the exact call your `/api/hls` route already makes. The arguments change; nothing else does.

### `PlaybackMode: LIVE / LIVE_REPLAY` (parameter)

The single biggest gap in the MVP. Your route hardcodes `ON_DEMAND`, which is why the system can only review the past. **LIVE** gives you a continuously-updating playlist — actual live view. **LIVE_REPLAY** starts at a past moment and keeps rolling forward as new fragments land, which is what "follow the incident from here" means in a real VMS.

Live mode needs no `HLSFragmentSelector` at all, so the change is genuinely a few lines.

- **Modes** — LIVE · LIVE_REPLAY · ON_DEMAND
- **Selector** — optional for LIVE
- **Playlist size** — 5 frags live, 1000 on-demand, 5000 max

### `GetDASHStreamingSessionURL` (archived media)

MPEG-DASH alongside HLS, same call shape and same selector logic. Worth knowing about mostly because some players and some enterprise environments want DASH; for browsers, hls.js already covers you.

- **Rate** — 25 TPS
- **Modes** — same three as HLS

### HLS session tuning (parameters)

Parameters your route currently leaves at defaults. **DisplayFragmentTimestamp** burns the timecode into the player — a genuine surveillance-console feature you'd otherwise build yourself. **ContainerFormat** switches between fragmented MP4 and MPEG-TS. **DiscontinuityMode** controls how gaps are signalled to the player, which matters exactly where your timeline shows gaps.

- **Expires** — 300–43200 s (300 default & minimum)
- **Container** — FRAGMENTED_MP4 · MPEG_TS
- **Discontinuity** — ALWAYS · NEVER · ON_DISCONTINUITY

---

## One new call on the client you already build

*A lesson each*

All three go through `archived_client()` exactly as written — same endpoint resolution, same `PRODUCER_TIMESTAMP` discipline, same `ClientError` handling.

### `GetClip` (archived media)

Downloads a time range as an **MP4 file**. This is the classic VMS export — "give me that incident as a file I can send to someone" — and it's the highest value-per-line addition on this whole page. Your timeline already produces exactly the start/end pair it needs.

Note the constraints are stricter than HLS: the codec private data and track layout must stay consistent across the whole range, so a stream that switched from video-only to video+audio mid-range will fail.

- **Output** — MP4
- **Cap** — first 100 MB or 200 fragments
- **Video** — H.264 or H.265
- **Audio** — AAC or MS Wave
- **Requires** — retention > 0

### `GetImages` (archived media)

Extracts still frames from a range as JPEG or PNG. Two direct uses in what you've built: **thumbnail previews on timeline hover**, and cheap frames to feed a detection model without pulling video. Returns images at a sampling interval you choose, resized server-side.

- **Format** — JPEG · PNG
- **Range** — ≤ 300 s per request
- **Sampling** — ≥ 200 ms (5/sec)
- **Results** — 25 default, 100 max
- **Size** — W 1–3840 · H 1–2160

### `GetMediaForFragmentList` (archived media)

Returns the raw MKV bytes for specific fragment numbers — the fragment numbers `ListFragments` already hands you and your merge step currently discards. This is the door to custom processing: your own transcoding, frame extraction, or analysis, without going through a player.

- **Input** — fragment numbers
- **Output** — MKV byte stream

---

## Stream configuration — set once, changes behaviour

*Control-plane calls*

Properties you set on the stream itself. No per-request work; the service does something different from then on.

### `UpdateImageGenerationConfiguration` (control plane)

Automatic, continuous frame extraction delivered straight to **S3** — no polling, no Lambda. Crucially it is *selective*: images are only generated for fragments carrying the `AWS_KINESISVIDEO_IMAGE_GENERATION` MKV tag, so a camera that tags only motion fragments produces thumbnails only for motion events. Custom tags on the fragment are preserved as S3 object metadata.

- **Destination** — S3 URI + region
- **Trigger** — per-fragment MKV tag
- **Delay** — ≥ 1 min after config change
- **Key** — prefix_acct_stream_timecode_id.jpg

### `UpdateNotificationConfiguration` (control plane)

Publishes to **SNS when fragments become available**. This replaces polling: instead of your frontend asking every 10 seconds whether anything arrived, the stream tells you. It's also the hook for anything event-driven — alerting, indexing into a database, kicking off analysis.

- **Target** — Amazon SNS
- **Fires on** — fragment availability
- **Status** — ENABLED · DISABLED

### Warm storage tier (new · Nov 2025)

A cheaper storage class for long retention, with the standard tier now called "hot". AWS states it keeps **sub-second access latency**, so unlike a typical archive tier it doesn't impose a restore step. For any VMS keeping weeks or months of footage this is the single biggest lever on the bill. Set via `UpdateStreamStorageConfiguration`.

- **Tiers** — hot · warm
- **Latency** — sub-second (both)
- **Regions** — all except GovCloud

### `UpdateDataRetention · TagStream · TagResource` (control plane)

Retention is changeable after creation, so a "keep this incident for a year" feature doesn't need a separate stream. Tags are how you organise cameras once there are more than a handful — site, floor, owner, retention policy — and they're what IAM conditions can key off.

- **Retention** — 0, or 1 hour – 10 years

---

## Producer-side — where search actually comes from

*Needs SDK work*

These require changing what the edge agent sends, not what the server asks for. This is the tier that turns a recorder into a *management* system.

### Fragment metadata (producer SDK)

Key–value pairs attached to individual fragments as MKV tags. **Non-persistent** metadata tags one fragment — a motion event, a door opening, a detected plate. **Persistent** metadata rides every subsequent fragment until cancelled — GPS position, camera mode, operator on shift.

This is the honest answer to "how do I search my footage": you write the searchable facts in at ingest time, then read them back with the Stream Parser Library. Nothing else in KVS gives you event search.

- **Per fragment** — 10 items max
- **Name** — ≤ 128 bytes
- **Value** — ≤ 256 bytes
- **Reserved** — names can't start with "AWS"
- **Read via** — MkvTagProcessor

### Multi-track ingestion (producer SDK)

A stream can carry up to three tracks — most usefully video plus audio. Your current pipeline strips audio (`-an` in the clip generation, no audio branch in the pipeline), which is the right MVP call but leaves a real VMS feature on the table.

- **Tracks** — 3 max per stream
- **Audio for GetClip** — AAC or MS Wave

### Producer SDKs & transports (ingestion)

Beyond the C++ SDK your `kvssink` build wraps, there are Java and Android SDKs, prebuilt Docker images for Ubuntu, macOS and Raspberry Pi, and the raw `PutMedia` HTTP API if you'd rather write the container yourself. The Raspberry Pi image in particular makes a much cheaper classroom demo than a full SDK build.

- **SDKs** — C++ · Java · Android
- **PutMedia** — 1 connection/stream, 12.5 MB/s

---

## Separate subsystems

*Own module territory*

Different APIs, different mental model, different clients. Each is a course module in its own right rather than an addition to an existing one.

### WebRTC — signaling channels (subsystem)

Sub-second live viewing and **two-way audio**, which the HLS path structurally cannot do — HLS latency is measured in fragments. This is how consumer camera apps feel instant, and how a VMS gets a talk-back button.

It's a genuinely separate resource type: signaling channels rather than streams, with their own create/describe/list/delete operations and their own endpoint call. **Multi-viewer** (Nov 2025) lets several people watch without the camera doing more work, since forwarding happens in the cloud.

- **Viewers** — 3 concurrent
- **Session** — 1 hour max
- **Storage-enabled channels** — 100/account
- **Audio** — two-way

### WebRTC ingestion & storage (subsystem)

The bridge between the two halves. A signaling channel mapped to a stream via `UpdateMediaStorageConfiguration` records its WebRTC session into ordinary KVS storage — so the same footage is both live-viewable at low latency *and* queryable by everything in the tiers above. Masters call `JoinStorageSession`; viewers call `JoinStorageSessionAsViewer`.

- **Maps** — channel → stream
- **Then** — normal archived-media APIs apply

### Edge Agent (subsystem)

Records from **any RTSP IP camera with no firmware change**, retains locally, and uploads to the cloud on a schedule you define. For a real deployment this is the piece that makes the economics work: you don't pay to stream 24/7 from forty cameras, you keep footage on-site and pull up what you need.

Notably, it replaces your simulated camera with real hardware — which makes it the natural "now do it for real" capstone. Deploys as an IoT Greengrass V2 component, on Snowball Edge, in Docker on EC2, or natively via IoT Core.

- **Cameras** — RTSP, unmodified
- **Does** — local record · local retain · scheduled upload
- **APIs** — StartEdgeConfigurationUpdate · DescribeEdgeConfiguration · DeleteEdgeConfiguration · ListEdgeAgentConfigurations

---

## Downstream — analysis on top of the archive

*Other services*

Not KVS features so much as things KVS is built to feed. Worth knowing the seams exist before designing around them.

### Amazon Rekognition Video (integration)

Built-in integration for face detection and recognition against a stream. The shortest path from "we record video" to "we notice things in video" without writing any model code.

- **Consumes** — KVS stream directly

### Stream Parser Library & SageMaker (integration)

The Java parser library reads fragments and their metadata for your own pipelines, with documented paths into MXNet, TensorFlow and OpenCV; SageMaker covers custom models. This is also the library that reads back the fragment metadata from tier 4.

- **Language** — Java
- **Reads** — fragments + MKV tags

### Security & operations (platform)

KMS encryption at rest and TLS in transit are on by default; S3 is the underlying store. IAM scopes per stream ARN — which your course already teaches. CloudWatch carries the metrics you'd alarm on. IPv6 support for the Streams capability arrived in Oct 2025, and WebRTC reached GovCloud in Apr 2026.

- **At rest** — AWS KMS
- **In transit** — TLS
- **Store** — Amazon S3

---

## Limits that shape a VMS design

*Plan around these*

The numbers that decide architecture — particularly the one-stream-per-camera question and how many people can watch at once.

| Constraint | Value | Why it matters |
|---|---|---|
| Streams per account | 10,000 | Default; raisable past 100,000. One stream per camera is the intended model. |
| Retention | 0, or 1 h – 10 yr | Long retention is a pricing question, not a technical one — see the warm tier. |
| Fragment duration | 1 – 20 s | Sets your timeline's finest possible resolution and your seek granularity. |
| Fragment size | 50 MB | Caps bitrate × duration; high-bitrate cameras need shorter fragments. |
| PutMedia per stream | 1 connection | Exactly why your course forbids two publishers on one stream. |
| PutMedia bandwidth | 12.5 MB/s | 100 Mbps ceiling per stream. |
| GetMedia per stream | 3 connections | Direct media reads, separate from HLS sessions. |
| Tracks per stream | 3 | Video + audio + one more. |
| HLS / DASH session mint | 25 TPS | Minting rate, not viewer count. |
| Fragment media quota | 500 pts/s | The real concurrency ceiling — AWS cites ~250 concurrent live HLS sessions at 1-second fragments. |

---

## If you're picking what to build next

*My read, not AWS's*

1. **Live view first.** Switching `PlaybackMode` to `LIVE` is a handful of lines and closes the most conspicuous gap in the finished project — right now students build a system that can only look backwards. It also teaches a real lesson: the same API answers a completely different product question depending on one argument.

2. **Then GetClip.** Export-to-MP4 is the feature every real VMS has, it reuses everything Lesson 11 and 12 already teach, and the timeline hands it the exact inputs it needs. Highest value for the least new material.

3. **Then fragment metadata, if you want a Module 8.** It's the only route to searchable events, and it's conceptually the richest thing left — it forces students to think about what has to be written at ingest time because it can never be recovered later. That's a genuinely good engineering lesson, not just an API.

The Edge Agent is the other strong candidate, for a different reason: it swaps the simulated camera for real hardware, which is the most satisfying possible ending to a course that has been honest about using a stand-in since lesson one.

---

## Sources

- [Archived Media API operations](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/API_Operations_Amazon_Kinesis_Video_Streams_Archived_Media.html) · [Control-plane API operations](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/API_Operations_Amazon_Kinesis_Video_Streams.html)
- [GetClip](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/API_reader_GetClip.html) · [GetImages](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/API_reader_GetImages.html) · [GetHLSStreamingSessionURL (boto3)](https://docs.aws.amazon.com/boto3/latest/reference/services/kinesis-video-archived-media/client/get_hls_streaming_session_url.html)
- [Fragment metadata](https://github.com/awsdocs/AWS-Kinesis-Video-Documentation/blob/master/doc_source/how-meta.md) · [Automated image generation to S3](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/s3-real-time-image-create.html) · [NotificationConfiguration](https://docs.aws.amazon.com/kinesisvideostreams/latest/APIReference/API_NotificationConfiguration.html)
- [Edge Agent](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/edge.html) · [WebRTC ingestion & storage](https://docs.aws.amazon.com/kinesisvideostreams-webrtc-dg/latest/devguide/ingest-media.html) · [WebRTC Multi-Viewer](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-kinesis-video-streams-multi-viewer)
- [Warm storage tier](https://aws.amazon.com/about-aws/whats-new/2025/11/amazon-kinesis-video-streams-warm-storage-tier/) · [Quotas and limits](https://docs.aws.amazon.com/kinesisvideostreams/latest/dg/limits.html) · [Features overview](https://www.amazonaws.cn/en/kinesis/video-streams/features/)

*Checked against AWS documentation, 1 September 2026. Quotas and tier availability change; verify current values before designing to a number.*
