"""Lesson 3 — who serves browsers: never a Node.

A Node serves few, trusted, internal clients; the live gateway serves many,
untrusted, external ones. The Node's media worker carries a `tee` after the
parser with a LEAKY queue on the live branch — a stalled subscriber loses
frames, the recorder behind it never stalls. The gateway subscribes to that
tee ONCE per camera and fans out to N viewers, each with its own leaky
queue. The gateway relays the viewer's token to the Node and the Node's
grants decide — the gateway never authorises.

This file is the model of that contract (subscriptions, fan-out, leaky
queues, viewer counts, the token handed through). WebRTC, fMP4 and TURN are
the transport underneath it and belong to a bench with a browser.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Callable


class LeakyQueue:
    """Bounded; a full queue drops its OLDEST frame. push() never blocks."""

    def __init__(self, maxsize: int = 30):
        self.q: deque = deque(maxlen=maxsize)
        self.dropped = 0

    def push(self, frame) -> None:
        if len(self.q) == self.q.maxlen:
            self.dropped += 1
        self.q.append(frame)

    def drain(self) -> list:
        out = list(self.q)
        self.q.clear()
        return out


class LiveTee:
    """The Node side: one per camera, inside the media worker. Subscribers
    are the gateway (one) — never a browser. `push` is called once per
    access unit by the pipeline; it is fire-and-forget."""

    def __init__(self, camera: int):
        self.camera = camera
        self.subscribers: dict[str, LeakyQueue] = {}
        self.frames = 0

    def subscribe(self, who: str, maxsize: int = 30) -> LeakyQueue:
        q = self.subscribers.setdefault(who, LeakyQueue(maxsize))
        return q

    def unsubscribe(self, who: str) -> None:
        self.subscribers.pop(who, None)

    def push(self, frame) -> None:
        self.frames += 1
        for q in self.subscribers.values():
            q.push(frame)

    @property
    def viewers(self) -> int:
        return len(self.subscribers)


class Forbidden(Exception):
    pass


@dataclass
class NodeLiveEndpoint:
    """A Node's live endpoint, found by service discovery. `authorise(token,
    camera) -> subject` is the Node's own check: signature against the
    signer's public key, then its local grants."""
    node: str
    tees: dict[int, LiveTee]
    authorise: Callable[[str, int], str]

    def open(self, camera: int, token: str, who: str) -> LeakyQueue:
        subject = self.authorise(token, camera)        # raises Forbidden
        tee = self.tees.get(camera)
        if tee is None:
            raise KeyError(f"camera {camera} is not on {self.node}")
        return tee.subscribe(who)


@dataclass
class Upstream:
    node: str
    queue: LeakyQueue
    viewers: dict[str, LeakyQueue] = field(default_factory=dict)


class Gateway:
    """`where(camera) -> node` is the directory; `endpoint(node)` is service
    discovery. Both are looked up per subscription, so a failover moves the
    endpoint and `reconnect` follows it."""

    def __init__(self, name: str, where: Callable[[int], str | None], endpoint: Callable[[str], NodeLiveEndpoint]):
        self.name, self.where, self.endpoint = name, where, endpoint
        self.upstreams: dict[int, Upstream] = {}

    def watch(self, camera: int, token: str, viewer: str, maxsize: int = 30) -> LeakyQueue:
        node = self.where(camera)
        if node is None:
            raise KeyError(f"camera {camera} is on no Node this domain can reach")
        up = self.upstreams.get(camera)
        if up is None or up.node != node:
            q = self.endpoint(node).open(camera, token, who=self.name)      # ONE subscription per camera
            up = self.upstreams[camera] = Upstream(node, q)
        else:
            self.endpoint(node).authorise(token, camera)                    # every viewer is still checked by the Node
        vq = up.viewers[viewer] = LeakyQueue(maxsize)
        return vq

    def leave(self, camera: int, viewer: str) -> None:
        up = self.upstreams.get(camera)
        if up and viewer in up.viewers:
            del up.viewers[viewer]
            if not up.viewers:
                self.endpoint(up.node).tees[camera].unsubscribe(self.name)
                del self.upstreams[camera]

    def pump(self) -> int:
        """Move frames from each upstream queue to every viewer's queue. A
        slow viewer's queue leaks; nobody else waits."""
        moved = 0
        for up in self.upstreams.values():
            for frame in up.queue.drain():
                moved += 1
                for vq in up.viewers.values():
                    vq.push(frame)
        return moved

    def reconnect(self, camera: int, token: str) -> str:
        """After a failover: the directory says the camera moved; resubscribe there."""
        node = self.where(camera)
        up = self.upstreams.get(camera)
        if up and up.node != node:
            q = self.endpoint(node).open(camera, token, who=self.name)
            self.upstreams[camera] = Upstream(node, q, up.viewers)
        return node

    def viewers(self, camera: int) -> int:
        up = self.upstreams.get(camera)
        return len(up.viewers) if up else 0
