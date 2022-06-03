from dataclasses import dataclass
from typing import Any

import aiohttp
import aiortc
import asyncio
import json
import logging
import random

logger = logging.getLogger(__name__)


class GeckosSignaling:
    '''Signaling client for geckos.io RTC data channel establishment.'''

    def __init__(self, server, port):
        self.baseurl = f'{server}:{port}/.wrtc/v2'

    async def request_offer(self):
        url = f'{self.baseurl}/connections'
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={}) as resp:
                assert resp.status == 200
                data = await resp.json()
                rdp = aiortc.RTCSessionDescription(
                    type=data['localDescription']['type'],
                    sdp=data['localDescription']['sdp'],
                )
                assert rdp.type == 'offer'
                return data['id'], rdp

    async def request_ice_candidates(self, uid):
        url = f'{self.baseurl}/connections/{uid}/additional-candidates'
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                assert resp.status == 200
                candidates = await resp.json()
                for data in candidates:
                    magic, candidate_str = data['candidate'].split(':', 1)
                    assert magic == 'a=candidate'
                    candidate = aiortc.sdp.candidate_from_sdp(candidate_str)
                    candidate.sdpMid = data['sdpMid']
                    yield candidate

    async def send_answer(self, uid, ldp):
        url = f'{self.baseurl}/connections/{uid}/remote-description'
        data = {
            'type': ldp.type,
            'sdp': ldp.sdp,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=data) as resp:
                assert resp.status == 200


@dataclass
class Event:
    type: str
    payload: Any  # Usually dict, bytes for rawstr events.


class GeckosClient:
    '''Client channel implementation for a geckos.io RTC data channel.

    Provides basic WebRTC data channel establishment with custom signaling, as
    well as geckos.io data layer support.

    At the same time, translate from the weird aiortc event-based sync API to
    some more appropriate Python async API.
    '''

    def __init__(self, server, port):
        self.server = server
        self.port = port
        self.channel = None
        self.readq = asyncio.Queue(maxsize=65536)  # for safety
        self.reliable_seen = set()

    async def connect(self):
        pc = aiortc.RTCPeerConnection()
        signaling = GeckosSignaling(self.server, self.port)
        chanq = asyncio.Queue()

        @pc.on('datachannel')
        def on_datachannel(channel):
            assert channel.label == 'geckos.io'

            @channel.on('message')
            def on_message(data):
                self.readq.put_nowait(data)

            @channel.on('close')
            def on_close():
                self.readq.put_nowait(None)

            chanq.put_nowait(channel)

        uid, rdp = await signaling.request_offer()
        await pc.setRemoteDescription(rdp)
        await pc.setLocalDescription(await pc.createAnswer())
        await signaling.send_answer(uid, pc.localDescription)
        async for candidate in signaling.request_ice_candidates(uid):
            await pc.addIceCandidate(candidate)
        self.channel = await chanq.get()

    def send(self, type, payload):
        if type == '_rawstr':
            self.channel.send(payload)
        else:
            self.channel.send(json.dumps({type: payload}))

    def send_reliable(self, type, payload):
        # TODO: Retransmit regularly, if we even need it?
        charset = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
        id = ''
        for i in range(24):
            id += random.choice(charset)
        self.channel.send(json.dumps({
            type: {
                'MESSAGE': payload,
                'RELIABLE': 1,
                'ID': id,
            }
        }))

    async def stream(self):
        while True:
            packet = await self.readq.get()
            if packet is None:
                return

            # There is no hint at the packet level of whether a data packet is
            # a raw string or a typed json event. The JS implementation of
            # Geckos always tries to pass the data through a JSON decoder,
            # instead we slightly optimize by checking for JSON structure.
            likely_json = any(c in packet for c in '{}[]')
            if likely_json:
                try:
                    packet = json.loads(packet)

                    type = list(packet.keys())[0]
                    payload = list(packet.values())[0]
                    if isinstance(payload, dict) and 'RELIABLE' in payload:
                        # Only emit the first time we've seen the event.
                        # TODO: leaking memory, should have an expiry.
                        if payload['ID'] in self.reliable_seen:
                            continue
                        self.reliable_seen.add(payload['ID'])
                        payload = payload['MESSAGE']

                    yield Event(type=type, payload=payload)
                    continue
                except json.JSONDecodeError:
                    pass

            yield Event(type='_rawstr', payload=packet)

    async def close(self):
        pass  # TODO
