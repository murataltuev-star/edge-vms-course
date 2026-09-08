# scripts/check_env.py — Lesson 13, Step 5: fail with a fix, not a traceback.
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fail(msg, fix):
    print(f"  FAIL  {msg}\n        → {fix}")
    return False


def ok(msg):
    print(f"  ok    {msg}")
    return True


def check_env_file():
    if not os.path.exists(".env"):
        return fail(".env not found", "cp .env.example .env, then fill in your AWS keys")
    if not os.environ.get("AWS_REGION"):
        return fail("AWS_REGION not set", "add AWS_REGION=<your-region> to .env")
    return ok(f".env loaded (region {os.environ['AWS_REGION']})")


def check_credentials():
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        ident = boto3.client("sts", region_name=os.environ["AWS_REGION"]).get_caller_identity()
    except (ClientError, BotoCoreError) as e:
        return fail(f"AWS credentials rejected ({e.__class__.__name__})",
                    "check AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY in .env; "
                    "if using SSO, refresh and set AWS_SESSION_TOKEN")
    return ok(f"credentials valid ({ident['Arn']})")


def check_kvssink(docker_image):
    if docker_image:
        found = subprocess.run(["docker", "image", "inspect", docker_image],
                               capture_output=True).returncode == 0
        return ok(f"docker image {docker_image} present") if found else fail(
            f"docker image {docker_image} not found",
            f"docker build -t {docker_image} docker/kvssink")
    if shutil.which("gst-inspect-1.0") is None:
        return fail("gst-inspect-1.0 not on PATH", "install GStreamer (see README)")
    found = subprocess.run(["gst-inspect-1.0", "kvssink"], capture_output=True).returncode == 0
    return ok("kvssink available on host") if found else fail(
        "no element \"kvssink\"",
        "build the producer SDK and export GST_PLUGIN_PATH, or set KVS_DOCKER_IMAGE in .env")


def check_clip(clip_path, docker_image):
    if not os.path.exists(clip_path):
        return fail(f"{clip_path} not found", f"scripts/make_clip.sh   (writes {clip_path})")
    if shutil.which("ffprobe"):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "csv=p=0", clip_path],
            capture_output=True, text=True).stdout.strip()
        return ok(f"{clip_path} is {out}") if out == "h264" else fail(
            f"{clip_path} is {out or 'unreadable'}, not h264", "scripts/make_clip.sh")
    if docker_image:
        # Validate with the same four elements the real pipeline uses.
        rc = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{os.path.abspath(clip_path)}:/clip.mp4:ro",
             docker_image, "filesrc", "location=/clip.mp4",
             "!", "qtdemux", "!", "h264parse", "!", "fakesink"],
            capture_output=True).returncode
        return ok("clip demuxes as H.264 (verified in container)") if rc == 0 else fail(
            "clip could not be demuxed as H.264 in the container", "scripts/make_clip.sh")
    return ok(f"{clip_path} exists (not verified — no ffprobe, no docker image)")


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    results = [check_env_file()]
    if results[0]:
        from server.config import CLIP_PATH, DOCKER_IMAGE
        results += [check_credentials(), check_kvssink(DOCKER_IMAGE), check_clip(CLIP_PATH, DOCKER_IMAGE)]
    sys.exit(0 if all(results) else 1)
