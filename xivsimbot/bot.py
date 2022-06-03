from dataclasses import dataclass, field
from typing import Mapping

import asyncio
import logging
import math
import time

from . import ai
from . import geckos

logger = logging.getLogger(__name__)


@dataclass
class Enemy:
    id: int
    name: str
    x: int
    y: int
    angle: float


@dataclass
class Buff:
    id: int
    deadline: int


@dataclass
class Player:
    id: int
    name: str
    x: int = 0
    y: int = 0
    angle: float = 0
    moving: bool = False
    buffs: Mapping[int, Buff] = field(default_factory=dict)

    def reset(self):
        self.x = self.y = self.angle = 0
        self.moving = False
        self.buffs = {}

    def add_buff(self, id, deadline):
        self.buffs[id] = Buff(id=id, deadline=deadline)

    def remove_buff(self, id):
        del self.buffs[id]

    def has_buff(self, id):
        return id in self.buffs

@dataclass
class Ability:
    id: int
    name: str
    cast_deadline: int
    gc_deadline: int


class SimState:
    def __init__(self):
        self.players = {}
        self.enemies = {}
        self.abilities = {}

        self.pause_time = None
        self.server_offset = 0  # TODO?
        self.pause_offset = 0

    def reset(self):
        self.enemies = {}
        for p in self.players.values():
            p.reset()

    def pause(self):
        self.pause_time = self.time()

    def unpause(self, offset):
        self.pause_time = None
        self.pause_offset = offset

    def time(self):
        if self.pause_time is not None:
            return self.pause_time
        return time.time() - self.pause_offset - self.server_offset

    def add_player(self, pid, name):
        self.players[pid] = Player(id=pid, name=name)

    def update_player(self, pid, x, y, angle, moving):
        if pid not in self.players:
            return
        p = self.players[pid]
        p.x, p.y, p.angle, p.moving = int(x), int(y), angle, moving

    def remove_player(self, pid):
        if pid not in self.players:
            return
        del self.players[pid]

    def add_enemy(self, eid, name, x, y, angle):
        self.enemies[eid] = Enemy(id=eid, name=name, x=x, y=y, angle=angle)

    def remove_enemy(self, eid):
        if eid not in self.enemies:
            return
        del self.enemies[eid]

    def add_ability(self, aid, name, duration):
        self.abilities[aid] = Ability(
                id=aid, name=name, cast_deadline=self.time()+duration,
                gc_deadline=self.time()+duration+1)

    def gc_abilities(self):
        to_remove = set()
        for abi in self.abilities.values():
            if self.time() > abi.gc_deadline:
                to_remove.add(abi.id)
        for aid in to_remove:
            del self.abilities[aid]


class XivSimClient:
    def __init__(self, server, port, password):
        self.server = server
        self.port = port
        self.password = password

        self.current_map = None
        self.pid = None

        self.state = SimState()
        self.strategy = None

    def clone(self):
        spawn(self.server, self.port, self.password)

    def continuous_player_update(self, gclient):
        async def func():
            while True:
                await asyncio.sleep(0.05)
                if self.pid not in self.state.players:
                    return
                me = self.state.players[self.pid]
                fields = (
                    0,
                    self.pid,
                    int(me.x),
                    int(me.y),
                    int(10000 * math.cos(me.angle)),
                    int(10000 * math.sin(me.angle)),
                    int(me.moving),
                    0,
                    0,
                )
                s = '|'.join(str(f) for f in fields)
                gclient.send('_rawstr', s)
        asyncio.create_task(func())

    async def mainloop(self):
        gclient = geckos.GeckosClient(self.server, self.port)
        await gclient.connect()

        gclient.send('password', self.password)

        async for evt in gclient.stream():
            if evt.type == 'passOK':
                self.current_map = evt.payload['m']
                logger.info('Password OK, we are in! Current map: %s',
                            self.current_map)
            elif evt.type == 'setId':
                self.pid = evt.payload['id']
                logger.info('Received ID, I am %d', self.pid)
                self.state.add_player(self.pid, 'hi')
                for p in evt.payload['players']:
                    if 'name' not in p:
                        continue
                    self.state.add_player(p['id'], p['name'])
                gclient.send_reliable('setPlayerData', {
                    'id': self.pid,
                    'job': 'rdm',
                    'name': 'hi',
                    'inputType': 0,
                })
                gclient.send('mapLoaded', {'pid': self.pid})
                self.continuous_player_update(gclient)
            elif evt.type == 'newPlayer':
                self.state.add_player(evt.payload['id'], evt.payload['name'])
            elif evt.type == 'mapChange':
                self.current_map = evt.payload['m']
                logger.info('Map change, going to %s', evt.payload['m'])
                gclient.send('mapLoaded', {'pid': self.pid})
            elif evt.type == 'playerDisconnected':
                self.state.remove_player(evt.payload['id'])
                if evt.payload['id'] == self.pid:
                    break
            elif evt.type == 'buff':
                if evt.payload['p'] not in self.state.players:
                    continue
                self.state.players[evt.payload['p']].add_buff(
                        evt.payload['i'],
                        self.state.time() + evt.payload['d'] / 1000)
            elif evt.type == 'buffExpired':
                self.state.players[evt.payload['p']].remove_buff(
                        evt.payload['i'])
            elif evt.type == 'newEnemy':
                self.state.add_enemy(
                        evt.payload['i'], evt.payload['name'],
                        evt.payload['x'], evt.payload['z'],
                        math.atan2(evt.payload['k'], evt.payload['j']))
            elif evt.type == 'rEnemy':
                self.state.remove_enemy(evt.payload['id'])
            elif evt.type == 'newEnemyAbility':
                self.state.add_ability(
                        evt.payload['id'], evt.payload['name'],
                        evt.payload['castTime'] / 1000)
            elif evt.type == 'reset':
                self.state.reset()
                if self.strategy is not None:
                    self.strategy.stop()
                    self.strategy = None
            elif evt.type == 'gamePaused':
                self.state.pause()
            elif evt.type == 'gameUnpaused':
                self.state.unpause(evt.payload / 1000)
            elif evt.type == 'tts':
                # Use as trigger for starting an AI strategy.
                strategy_type = ai.find_strategy(evt.payload['m'])
                if strategy_type is not None:
                    self.strategy = strategy_type(gclient, self.state, self.pid)
                    self.strategy.run()
            elif evt.type == '_rawstr':
                fields = evt.payload.split('|')
                if fields[0] == '0':  # player update
                    pid, x, y, fx, fy, m, _, _ = [int(f) for f in fields[1:]]
                    if pid == self.pid:
                        continue
                    angle = math.atan2(fy, fx)
                    self.state.update_player(pid, x, y, angle, m)
            else:
                pass

        print('Disconnected!')
        if self.strategy is not None:
            self.strategy.stop()
        await gclient.close()



def spawn(server, port, password):
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
    asyncio.create_task(XivSimClient(server, port, password).mainloop())
