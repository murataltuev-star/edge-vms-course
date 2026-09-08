# server/config.py — Lesson 13, Step 2.
# Reads settings, NEVER credentials. AWS_* sit in .env only so load_dotenv()
# puts them in the environment, where boto3 finds them by itself. No code
# path here can log or serialize a credential, because none ever holds one.
import os
from dotenv import load_dotenv

load_dotenv()   # reads .env at the repo root into os.environ, if present

AWS_REGION = os.environ["AWS_REGION"]
STREAM_NAME = os.getenv("KVS_STREAM_NAME", "cam-01")
RETENTION_HOURS = int(os.getenv("KVS_RETENTION_HOURS", "24"))
CLIP_PATH = os.getenv("CLIP_PATH", "./media/clip.mp4")
DOCKER_IMAGE = os.getenv("KVS_DOCKER_IMAGE", "")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8000"))
TIMELINE_WINDOW_MINUTES = int(os.getenv("TIMELINE_WINDOW_MINUTES", "60"))
PLAYBACK_CHUNK_SECONDS = int(os.getenv("PLAYBACK_CHUNK_SECONDS", "300"))

# М9 Lesson 19: on the appliance the pipeline writes segments to a spool
# instead of publishing straight to kvssink. Empty means М8 behaviour.
SPOOL_DIR = os.getenv("VMS_SPOOL_DIR", "")
SEGMENT_SECONDS = int(os.getenv("SEGMENT_SECONDS", "600"))
